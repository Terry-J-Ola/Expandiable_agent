from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Protocol, Sequence
from urllib import error, request

from agentllm.errors import MCPConnectionError, MCPRequestError
from agentllm.tools.mcp import MCPToolResponse
from agentllm.types import JsonDict


class MCPClientError(MCPConnectionError):
    """Raised when an MCP client cannot connect or continue working."""


MCPTransport = Literal["stdio", "remote-http"]


@dataclass(frozen=True)
class MCPServerConfig:
    """
    Unified MCP client configuration.

    Think of this as one envelope that can describe either:
    - how to start a local stdio server
    - how to connect to a remote HTTP MCP endpoint
    """

    transport: MCPTransport = "stdio"
    command: Sequence[str] = field(default_factory=tuple)
    cwd: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    server_url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    initialization_timeout_seconds: float = 10.0
    request_timeout_seconds: float = 30.0
    protocol_version: str = "2025-06-18"


@dataclass
class _PendingRequest:
    future: asyncio.Future[JsonDict]


class AsyncMCPClientProtocol(Protocol):
    async def connect(self) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError

    async def list_tools(self) -> List[JsonDict]:
        raise NotImplementedError

    async def call_tool(self, name: str, arguments: JsonDict) -> MCPToolResponse:
        raise NotImplementedError

    @property
    def server_info(self) -> JsonDict:
        raise NotImplementedError

    @property
    def server_capabilities(self) -> JsonDict:
        raise NotImplementedError


class StdioMCPClient:
    """Async MCP client that talks to a local subprocess over stdio."""

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._process: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._request_id = 0
        self._pending: Dict[int, _PendingRequest] = {}
        self._server_info: JsonDict = {}
        self._server_capabilities: JsonDict = {}

    async def connect(self) -> None:
        if self._process is not None:
            return
        if not self._config.command:
            raise MCPClientError("missing stdio server command")

        self._process = await asyncio.create_subprocess_exec(
            *self._config.command,
            cwd=self._config.cwd,
            env=self._config.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_loop())

        init_result = await asyncio.wait_for(
            self._request(
                "initialize",
                {
                    "protocolVersion": self._config.protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": "agentllm", "version": "0.1.0"},
                },
            ),
            timeout=self._config.initialization_timeout_seconds,
        )
        self._server_info = init_result.get("serverInfo", {})
        self._server_capabilities = init_result.get("capabilities", {})
        await self._notify("notifications/initialized", {})

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None

        if self._process is not None:
            if self._process.stdin is not None:
                self._process.stdin.close()
                with contextlib.suppress(Exception):
                    await self._process.stdin.wait_closed()
            self._process.terminate()
            with contextlib.suppress(ProcessLookupError):
                await self._process.wait()
            self._process = None

        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(MCPClientError("client closed"))
        self._pending.clear()

    async def list_tools(self) -> List[JsonDict]:
        result = await self._request("tools/list", {})
        return list(result.get("tools", []))

    async def call_tool(self, name: str, arguments: JsonDict) -> MCPToolResponse:
        result = await self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        return _normalize_tool_result(result)

    @property
    def server_info(self) -> JsonDict:
        return dict(self._server_info)

    @property
    def server_capabilities(self) -> JsonDict:
        return dict(self._server_capabilities)

    async def _request(self, method: str, params: JsonDict) -> JsonDict:
        request_id = self._next_request_id()
        future: asyncio.Future[JsonDict] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = _PendingRequest(future=future)

        await self._send_jsonrpc(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )

        response = await future
        if "error" in response:
            raise MCPRequestError(json.dumps(response["error"], ensure_ascii=False))
        return response.get("result", {})

    async def _notify(self, method: str, params: JsonDict) -> None:
        await self._send_jsonrpc(
            {"jsonrpc": "2.0", "method": method, "params": params}
        )

    async def _send_jsonrpc(self, payload: JsonDict) -> None:
        if self._process is None or self._process.stdin is None:
            raise MCPClientError("client is not connected")
        body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self._process.stdin.write(body)
        await self._process.stdin.drain()

    async def _read_loop(self) -> None:
        if self._process is None or self._process.stdout is None:
            return

        while True:
            line = await self._process.stdout.readline()
            if not line:
                break
            message = _safe_load_json_bytes(line)
            if message is None or "id" not in message:
                continue

            request_id = int(message["id"])
            pending = self._pending.pop(request_id, None)
            if pending is None or pending.future.done():
                continue
            pending.future.set_result(message)

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id


class RemoteHTTPMCPClient:
    """Async MCP client that talks to a remote Streamable HTTP MCP server."""

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._request_id = 0
        self._session_id: Optional[str] = None
        self._server_info: JsonDict = {}
        self._server_capabilities: JsonDict = {}

    async def connect(self) -> None:
        if not self._config.server_url:
            raise MCPClientError("missing remote MCP server URL")
        if self._session_id is not None:
            return

        result, response_headers = await self._post_jsonrpc(
            method="initialize",
            params={
                "protocolVersion": self._config.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "agentllm", "version": "0.1.0"},
            },
            include_session=False,
        )
        self._server_info = result.get("serverInfo", {})
        self._server_capabilities = result.get("capabilities", {})
        self._session_id = response_headers.get("Mcp-Session-Id")
        await self._post_notification("notifications/initialized", {})

    async def close(self) -> None:
        # Many remote MCP servers are stateless or session headers are optional.
        # Keeping close lightweight is fine for the current skeleton.
        self._session_id = None

    async def list_tools(self) -> List[JsonDict]:
        result, _ = await self._post_jsonrpc("tools/list", {})
        return list(result.get("tools", []))

    async def call_tool(self, name: str, arguments: JsonDict) -> MCPToolResponse:
        result, _ = await self._post_jsonrpc(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        return _normalize_tool_result(result)

    @property
    def server_info(self) -> JsonDict:
        return dict(self._server_info)

    @property
    def server_capabilities(self) -> JsonDict:
        return dict(self._server_capabilities)

    async def _post_jsonrpc(
        self,
        method: str,
        params: JsonDict,
        *,
        include_session: bool = True,
    ) -> tuple[JsonDict, Dict[str, str]]:
        if not self._config.server_url:
            raise MCPClientError("remote MCP server URL is not configured")

        request_id = self._next_request_id()
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self._config.protocol_version,
        }
        if self._config.headers:
            headers.update(self._config.headers)
        if include_session and self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        return await asyncio.to_thread(
            self._send_http_request,
            payload,
            headers,
        )

    async def _post_notification(self, method: str, params: JsonDict) -> None:
        if not self._config.server_url:
            raise MCPClientError("remote MCP server URL is not configured")

        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self._config.protocol_version,
        }
        if self._config.headers:
            headers.update(self._config.headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        await asyncio.to_thread(self._send_http_notification, payload, headers)

    def _send_http_request(
        self,
        payload: JsonDict,
        headers: Dict[str, str],
    ) -> tuple[JsonDict, Dict[str, str]]:
        req = request.Request(
            url=self._config.server_url or "",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self._config.request_timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise MCPRequestError("remote MCP server returned a non-object JSON response")
                if "error" in parsed:
                    raise MCPRequestError(json.dumps(parsed["error"], ensure_ascii=False))
                return parsed.get("result", {}), dict(resp.headers.items())
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise MCPRequestError(f"remote MCP HTTP error {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise MCPClientError(f"remote MCP network error: {exc}") from exc

    def _send_http_notification(self, payload: JsonDict, headers: Dict[str, str]) -> None:
        req = request.Request(
            url=self._config.server_url or "",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self._config.request_timeout_seconds):
                return
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise MCPRequestError(f"remote MCP HTTP error {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise MCPClientError(f"remote MCP network error: {exc}") from exc

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id


class BlockingMCPClient:
    """Sync facade over one async transport-specific MCP client."""

    def __init__(self, async_client: AsyncMCPClientProtocol) -> None:
        self._async_client = async_client
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="agentllm-mcp-loop",
            daemon=True,
        )
        self._closed = False
        self._thread.start()

    def connect(self) -> None:
        if self._closed:
            raise MCPClientError("client is closed")
        self._run(self._async_client.connect())

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._run(self._async_client.close())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=2)
            self._loop.close()
            self._closed = True

    def list_tools(self) -> List[JsonDict]:
        if self._closed:
            raise MCPClientError("client is closed")
        return self._run(self._async_client.list_tools())

    def call_tool(self, name: str, arguments: JsonDict) -> MCPToolResponse:
        if self._closed:
            raise MCPClientError("client is closed")
        return self._run(self._async_client.call_tool(name, arguments))

    @property
    def server_info(self) -> JsonDict:
        return self._async_client.server_info

    @property
    def server_capabilities(self) -> JsonDict:
        return self._async_client.server_capabilities

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run(self, awaitable):
        future = asyncio.run_coroutine_threadsafe(awaitable, self._loop)
        return future.result()

    def __enter__(self) -> "BlockingMCPClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class MCPClient:
    """
    Unified sync-facing MCP client.

    This is the 'big unified class' you asked for:
    callers do not need to care whether the real transport is stdio or remote HTTP.
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._blocking_client = BlockingMCPClient(_build_async_client(config))

    def connect(self) -> None:
        self._blocking_client.connect()

    def close(self) -> None:
        self._blocking_client.close()

    def list_tools(self) -> List[JsonDict]:
        return self._blocking_client.list_tools()

    def call_tool(self, name: str, arguments: JsonDict) -> MCPToolResponse:
        return self._blocking_client.call_tool(name, arguments)

    @property
    def server_info(self) -> JsonDict:
        return self._blocking_client.server_info

    @property
    def server_capabilities(self) -> JsonDict:
        return self._blocking_client.server_capabilities

    def __enter__(self) -> "MCPClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def create_mcp_client(config: MCPServerConfig) -> MCPClient:
    """Unified entry point for building an MCP client from config."""

    return MCPClient(config)


def _build_async_client(config: MCPServerConfig) -> AsyncMCPClientProtocol:
    if config.transport == "stdio":
        return StdioMCPClient(config)
    if config.transport == "remote-http":
        return RemoteHTTPMCPClient(config)
    raise MCPClientError(f"unsupported MCP transport: {config.transport}")


def _safe_load_json_bytes(raw: bytes) -> Optional[JsonDict]:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None

# 对MCP返回的结果进行包装成MCPToolResponse
def _normalize_tool_result(result: JsonDict) -> MCPToolResponse:
    content_items = result.get("content", [])
    text_parts = [
        item.get("text", "")
        for item in content_items
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    content = "\n".join(part for part in text_parts if part).strip()
    if not content:
        content = json.dumps(result, ensure_ascii=False)

    return MCPToolResponse(
        ok=not result.get("isError", False),
        content=content,
        data={"raw_result": result},
    )
