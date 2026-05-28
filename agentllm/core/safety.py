from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from typing import Iterable, Optional, Sequence

from agentllm.core.types import ToolCall


class SecurityError(Exception):
    """Raised when input or tool usage violates runtime safety rules."""


@dataclass(frozen=True)
class SafetyPolicy:
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    blocked_patterns: Sequence[str] = field(
        default_factory=lambda: (
            r"ignore\s+previous\s+instructions",
            r"reveal\s+(system|developer)\s+prompt",
            r"(api[_ -]?key|secret|token|password)\s*[:=]",
        )
    )
    max_steps: int = 8
    max_tool_calls_per_step: int = 5
    max_input_chars: int = 12_000
    max_tool_argument_chars: int = 4_000

    def with_allowed_tools(self, tool_names: Iterable[str]) -> "SafetyPolicy":
        return replace(self, allowed_tools=frozenset(tool_names))

    def validate_user_input(self, text: str) -> None:
        if len(text) > self.max_input_chars:
            raise SecurityError(f"Input too long: {len(text)} chars")

        lowered = text.lower()
        for pattern in self.blocked_patterns:
            if re.search(pattern, lowered):
                raise SecurityError(f"Blocked prompt pattern: {pattern}")

    def validate_tool_call(self, call: ToolCall) -> None:
        if self.allowed_tools and call.name not in self.allowed_tools:
            raise SecurityError(f"Tool not allowed: {call.name}")

        size = len(json.dumps(call.arguments, ensure_ascii=False))
        if size > self.max_tool_argument_chars:
            raise SecurityError(f"Tool arguments too large: {size} chars")


class SecretRedactor:
    def __init__(self, patterns: Optional[Sequence[str]] = None) -> None:
        default_patterns = (
            r"sk-[A-Za-z0-9]{20,}",
            r"(?i)(api[_ -]?key|secret|token|password)\s*[:=]\s*['\"]?[^'\"\s]+",
            r"-----BEGIN [A-Z ]+PRIVATE KEY-----",
        )
        self._patterns = tuple(patterns or default_patterns)

    def redact(self, text: str) -> str:
        output = text
        for pattern in self._patterns:
            output = re.sub(pattern, "[REDACTED]", output)
        return output
