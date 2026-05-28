from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
from collections.abc import Awaitable, Coroutine
from typing import Mapping, Optional, Protocol, Sequence, TypeVar, runtime_checkable

from agentllm.logging_utils import get_logger
from agentllm.tools.base import Tool
from agentllm.types import JsonDict, ToolResult, ToolSpec


T = TypeVar("T")
LOGGER = get_logger(__name__)


@dataclass(frozen=True)
class MCPToolResponse:
    """MCP 客户端返回结果的统一包装。"""

    ok: bool
    content: str
    data: Optional[JsonDict] = None


@runtime_checkable
class MCPClientProtocol(Protocol):
    """
    MCPTool 依赖的最小客户端协议。

    可以把它想成“快递接口”：
    - `list_tools()` 负责问远端服务器“你这边有哪些工具”
    - `call_tool()` 负责真正把请求送过去并把结果带回来
    """

    def call_tool(
        self,
        name: str,
        arguments: JsonDict,
    ) -> MCPToolResponse | Awaitable[MCPToolResponse]:
        raise NotImplementedError

    def list_tools(self) -> Sequence[JsonDict] | Awaitable[Sequence[JsonDict]]:
        raise NotImplementedError


class MCPTool(Tool):
    """
    把“ MCP server ”伪装成本地 Tool。

    形象一点说：
    - Agent 和 ToolRegistry 像前台
    - MCP server 像另一栋楼里的专业部门
    - MCPTool 就像前台和外部部门之间的“转接员”

    前台只管说“我要调用 read_file”
    转接员负责把请求转给远端 MCP client，
    再把远端结果整理成当前项目认识的 ToolResult。
    """

    def __init__(
        self,
        *,
        spec: ToolSpec,
        client: MCPClientProtocol,
        remote_name: Optional[str] = None,
        static_arguments: Optional[Mapping[str, object]] = None,
    ) -> None:
        self._spec = spec
        self._client = client
        self._remote_name = remote_name or spec.name
        # 这些静态参数像“随单附带信息”。
        # 例如本地工具名虽然叫 search_docs，
        # 但每次调用都固定带上 workspace_id 之类的远端上下文。
        self._static_arguments = dict(static_arguments or {})

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def run(self, arguments: JsonDict) -> ToolResult:
        # 调用时把“固定参数”和“本次动态参数”合并。
        # 后传入的 arguments 会覆盖同名静态参数。
        merged_arguments: JsonDict = {
            **self._static_arguments,
            **arguments,
        }
        # 这里不直接假设 client 一定是同步的。
        # 如果底层 client 是 async 的，就交给下面的桥接函数处理。
        try:
            response = _resolve_maybe_awaitable(
                self._client.call_tool(self._remote_name, merged_arguments)
            )
            return ToolResult(
                ok=response.ok,
                content=response.content,
                data=response.data,
            )
        except Exception as exc:
            LOGGER.exception("MCP tool `%s` failed", self._remote_name)
            return ToolResult(
                ok=False,
                content=f"MCP tool `{self._remote_name}` failed: {exc}",
            )


async def discover_mcp_tools(client: MCPClientProtocol) -> list[MCPTool]:
    """
    从远端 MCP server 的工具目录，批量构造本地 MCPTool。

    可以把它想成“通讯录同步”：
    先问远端“你有哪些人/工具”，
    再把这些远端能力一个个登记到本地，方便 Agent 后续直接使用。
    """

    remote_tools = _resolve_maybe_awaitable(client.list_tools())
    tools: list[MCPTool] = []
    for item in remote_tools:
        name = str(item["name"])
        description = str(item.get("description", ""))
        # 不同实现里字段名可能略有差异，这里顺手兼容两种常见写法。
        input_schema = item.get("inputSchema") or item.get("input_schema") or {
            "type": "object",
            "properties": {},
        }
        tools.append(
            MCPTool(
                spec=ToolSpec(
                    name=name,
                    description=description,
                    input_schema=input_schema,
                ),
                client=client,
                remote_name=name,
            )
        )
    return tools


def discover_mcp_tools_sync(client: MCPClientProtocol) -> list[MCPTool]:
    """
    给“整体还是同步代码”的项目准备的便捷入口。

    当前这个项目的 Agent 主循环还是同步的，
    所以这里提供一个同步外壳，方便先把 MCP 接进来。
    """

    return _resolve_maybe_awaitable(discover_mcp_tools(client))


def _resolve_maybe_awaitable(value: T | Awaitable[T]) -> T:
    """
    处理“这个值到底是直接结果，还是 await 之后才有结果”。

    这是同步世界和异步世界之间的一块小桥：
    - 如果拿到的是普通值，直接返回
    - 如果拿到的是 awaitable，并且当前没有事件循环，就临时跑一下
    - 如果当前已经在事件循环里，就不强行嵌套，直接报错提醒调用方改走 async 路径
    """
    if inspect.isawaitable(value):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_ensure_coroutine(value))
        raise RuntimeError(
            "Encountered an async MCP operation inside an active event loop. "
            "Use the async helper directly instead of the sync Tool.run path."
        )
    return value


def _ensure_coroutine(value: Awaitable[T]) -> Coroutine[object, object, T]:
    """把一般 Awaitable 包装成 asyncio.run 能接受的 Coroutine。"""

    if inspect.iscoroutine(value):
        return value

    async def _wrapper() -> T:
        return await value

    return _wrapper()
