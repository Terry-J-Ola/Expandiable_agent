import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agentllm.mcp_client.client import BlockingMCPClient, MCPClientError
from agentllm.tools import MCPTool, ToolRegistry, discover_mcp_tools_sync
from agentllm.tools.mcp import MCPToolResponse
from agentllm.types import ToolSpec


class RecordingSyncMCPClient:
    """
    A realistic synchronous fake MCP client.

    It exposes a remote tool catalog and records every tools/call request so
    the test can verify whether MCPTool truly forwards the invocation.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.remote_tools = [
            {
                "name": "read_text_file",
                "description": "Read a text file from the workspace",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "encoding": {"type": "string"},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "search_text",
                "description": "Search text inside the workspace",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "directory": {"type": "string"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        ]

    def list_tools(self) -> list[dict]:
        return list(self.remote_tools)

    def call_tool(self, name: str, arguments: dict) -> MCPToolResponse:
        self.calls.append((name, dict(arguments)))

        if name == "read_text_file":
            return MCPToolResponse(
                ok=True,
                content="file contents: hello from mcp server",
                data={
                    "path": arguments["path"],
                    "encoding": arguments.get("encoding", "utf-8"),
                },
            )

        if name == "search_text":
            return MCPToolResponse(
                ok=True,
                content="found 2 matches",
                data={
                    "matches": [
                        {"path": "docs/a.txt", "line_number": "3", "line": "memory"},
                        {"path": "docs/b.txt", "line_number": "8", "line": "memory"},
                    ]
                },
            )

        return MCPToolResponse(ok=False, content=f"unknown tool: {name}")


class FailingSyncMCPClient:
    def list_tools(self) -> list[dict]:
        return [
            {
                "name": "broken_tool",
                "description": "A tool that always fails",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ]

    def call_tool(self, name: str, arguments: dict) -> MCPToolResponse:
        raise RuntimeError("remote mcp server is unavailable")


class AsyncLifecycleMCPClient:
    def __init__(self) -> None:
        self.connected = False
        self.closed = False
        self._server_info: dict = {"name": "fake-async-mcp"}
        self._server_capabilities: dict = {"tools": {}}

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.closed = True

    async def list_tools(self) -> list[dict]:
        return [
            {
                "name": "search_text",
                "description": "Search text asynchronously",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            }
        ]

    async def call_tool(self, name: str, arguments: dict) -> MCPToolResponse:
        return MCPToolResponse(
            ok=True,
            content=f"async:{name}",
            data={"arguments": arguments},
        )

    @property
    def server_info(self) -> dict:
        return dict(self._server_info)

    @property
    def server_capabilities(self) -> dict:
        return dict(self._server_capabilities)


class MCPToolTestCase(unittest.TestCase):
    """Test MCPTool through a realistic discovery -> register -> call flow."""

    def test_can_discover_and_register_remote_tools(self) -> None:
        client = RecordingSyncMCPClient()

        discovered_tools = discover_mcp_tools_sync(client)
        registry = ToolRegistry()
        for tool in discovered_tools:
            registry.register(tool)

        self.assertEqual(len(discovered_tools), 2)
        self.assertIsInstance(discovered_tools[0], MCPTool)
        self.assertEqual(set(registry.names()), {"read_text_file", "search_text"})

    def test_registered_mcp_tool_forwards_calls_to_remote_client(self) -> None:
        client = RecordingSyncMCPClient()
        registry = ToolRegistry()
        for tool in discover_mcp_tools_sync(client):
            registry.register(tool)

        read_tool = registry.get("read_text_file")
        result = read_tool.run({"path": "notes.txt"})

        self.assertTrue(result.ok)
        self.assertEqual(result.content, "file contents: hello from mcp server")
        self.assertEqual(
            result.data,
            {"path": "notes.txt", "encoding": "utf-8"},
        )
        self.assertEqual(client.calls, [("read_text_file", {"path": "notes.txt"})])

    def test_static_and_dynamic_arguments_are_merged_before_forwarding(self) -> None:
        client = RecordingSyncMCPClient()
        tool = MCPTool(
            spec=ToolSpec(
                name="search_text",
                description="Search text inside the workspace",
                input_schema={"type": "object"},
            ),
            client=client,
            remote_name="search_text",
            static_arguments={"directory": "docs"},
        )

        result = tool.run({"query": "memory"})

        self.assertTrue(result.ok)
        self.assertEqual(result.content, "found 2 matches")
        self.assertEqual(
            client.calls,
            [("search_text", {"directory": "docs", "query": "memory"})],
        )

    def test_remote_failure_is_returned_as_failed_tool_result(self) -> None:
        client = FailingSyncMCPClient()
        tool = discover_mcp_tools_sync(client)[0]

        with self.assertLogs("agentllm.tools.mcp", level="ERROR") as log_context:
            result = tool.run({})

        self.assertFalse(result.ok)
        self.assertIn("broken_tool", result.content)
        self.assertIn("remote mcp server is unavailable", result.content)
        self.assertTrue(
            any("MCP tool `broken_tool` failed" in message for message in log_context.output)
        )

    def test_blocking_client_allows_sync_runtime_to_use_async_client(self) -> None:
        async_client = AsyncLifecycleMCPClient()

        with BlockingMCPClient(async_client) as blocking_client:
            self.assertTrue(async_client.connected)

            discovered_tools = discover_mcp_tools_sync(blocking_client)
            registry = ToolRegistry()
            for tool in discovered_tools:
                registry.register(tool)

            result = registry.get("search_text").run({"query": "memory"})

        self.assertTrue(async_client.closed)
        self.assertTrue(result.ok)
        self.assertEqual(result.content, "async:search_text")
        self.assertEqual(result.data, {"arguments": {"query": "memory"}})

    def test_blocking_client_rejects_calls_after_close(self) -> None:
        async_client = AsyncLifecycleMCPClient()
        client = BlockingMCPClient(async_client)
        client.connect()
        client.close()

        with self.assertRaises(MCPClientError):
            client.list_tools()


if __name__ == "__main__":
    unittest.main()
