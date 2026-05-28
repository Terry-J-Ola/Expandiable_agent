from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from agentllm.logging_utils import get_logger
from agentllm.types import JsonDict, ToolResult, ToolSpec


LOGGER = get_logger(__name__)


class Tool(ABC):
    """Minimal contract for executable tools."""

    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        raise NotImplementedError

    @abstractmethod
    def run(self, arguments: JsonDict) -> ToolResult:
        raise NotImplementedError


class FunctionTool(Tool):
    """Wrap a plain handler function as a tool."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_schema: JsonDict,
        handler: Callable[[JsonDict], ToolResult],
    ) -> None:
        # Keep the model-facing schema and the execution handler bundled
        # together so registration stays simple.
        self._spec = ToolSpec(
            name=name,
            description=description,
            input_schema=input_schema,
        )
        self._handler = handler

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def run(self, arguments: JsonDict) -> ToolResult:
        # This thin wrapper is intentional. More complex tools can implement
        # Tool directly when they need state, retries, or resource control.
        try:
            return self._handler(arguments)
        except Exception as exc:
            LOGGER.exception("Function tool `%s` failed", self._spec.name)
            return ToolResult(
                ok=False,
                content=f"Tool `{self._spec.name}` failed: {exc}",
            )
