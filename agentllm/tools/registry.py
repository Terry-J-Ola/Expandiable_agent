from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List

from agentllm.tools.base import Tool
from agentllm.types import ToolSpec


@dataclass
class ToolRegistry:
    """Tool lookup and metadata aggregation."""

    _tools: Dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        if tool.spec.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.spec.name}")
        self._tools[tool.spec.name] = tool

    # 获取所有工具
    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ValueError(f"Unknown tool: {name}") from exc
        
    # 获得所有注册好的tools描述
    def specs(self) -> List[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]
    
    # 获得工具名字
    def names(self) -> Iterable[str]:
        return self._tools.keys()
