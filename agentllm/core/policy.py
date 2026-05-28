from __future__ import annotations

from typing import Protocol

from agentllm.core.types import AgentAction, AgentContext

# 这是一个接口
class AgentPolicy(Protocol):
    def next_action(self, context: AgentContext) -> AgentAction:
        ...

# 这也是一个接口
class LLMProvider(Protocol):
    def complete(self, context: AgentContext) -> AgentAction:
        ...


class LLMPolicy:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    def next_action(self, context: AgentContext) -> AgentAction:
        return self._provider.complete(context)
