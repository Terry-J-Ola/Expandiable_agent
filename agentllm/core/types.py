from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence


JsonDict = Dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: str
    content: str
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Sequence["ToolCall"] = field(default_factory=tuple)
    reasoning_content: Optional[str] = None


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: JsonDict
    id: str = ""


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    content: str
    data: Optional[JsonDict] = None

    def to_message(self, tool_name: str, tool_call_id: Optional[str] = None) -> Message:
        payload = {
            "ok": self.ok,
            "content": self.content,
            "data": self.data or {},
        }
        return Message(
            role="tool",
            name=tool_name,
            content=json.dumps(payload, ensure_ascii=False),
            tool_call_id=tool_call_id,
        )


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: JsonDict


@dataclass(frozen=True)
class AgentContext:
    system_prompt: str
    messages: Sequence[Message]
    tools: Sequence[ToolSpec]


@dataclass(frozen=True)
class AgentAction:
    message: Message
    tool_calls: Sequence[ToolCall] = field(default_factory=tuple)
    raw: Any = None


@dataclass(frozen=True)
class AgentResult:
    output: str
    messages: Sequence[Message]
    steps: int
