from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Sequence, runtime_checkable

from agentllm.core.types import Message


@runtime_checkable
class Memory(Protocol):
    system_prompt: str

    def append(self, message: Message) -> None:
        raise NotImplementedError

    def all_messages(self) -> Sequence[Message]:
        raise NotImplementedError


class Summarizer(Protocol):
    """将文本块转换为更短摘要的抽象接口。"""

    def summarize(self, text: str) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class RuleCompressor:
    """
    低成本的初轮压缩器。

    它会修剪噪声内容，将每条消息重写为紧凑的要点，
    使 SummaryMemory 无需在每次溢出时都调用 LLM。
    """

    max_content_chars: int = 180
    max_tool_chars: int = 120

    def compress_messages(self, messages: Sequence[Message]) -> str:
        """将消息序列压缩为紧凑的要点列表。"""
        lines: List[str] = []
        for message in messages:
            line = self._compress_message(message)
            if line:
                lines.append(line)
        return "\n".join(lines)

    def _compress_message(self, message: Message) -> str:
        """将单条消息压缩为紧凑的要点。"""
        content = self._normalize_text(message.content)
        if message.role == "user":
            return f"- 用户说：{self._truncate(content, self.max_content_chars)}"
        if message.role == "assistant":
            return f"- 助手回复：{self._truncate(content, self.max_content_chars)}"
        if message.role == "tool":
            tool_name = message.name or "tool"
            return (
                f"- 调用工具 {tool_name}，结果："
                f"{self._truncate(content, self.max_tool_chars)}"
            )
        return f"- {message.role}：{self._truncate(content, self.max_content_chars)}"

    @staticmethod
    def _normalize_text(text: str) -> str:
        """通过折叠空白字符来规范化文本。"""
        return " ".join(text.split())

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        """将文本截断到指定长度，如果被截断则添加省略号。"""
        if len(text) <= limit:
            return text
        if limit <= 3:
            return text[:limit]
        return f"{text[: limit - 3]}..."


@dataclass
class ConversationMemory(Memory):
    system_prompt: str
    messages: List[Message] = field(default_factory=list)

    def append(self, message: Message) -> None:
        """将消息追加到对话历史中。"""
        self.messages.append(message)

    def all_messages(self) -> List[Message]:
        """返回包含系统提示的所有消息。"""
        return list(self.messages)


@dataclass
class WindowMemory(Memory):
    system_prompt: str
    max_messages: int = 12
    messages: List[Message] = field(default_factory=list)

    def append(self, message: Message) -> None:
        """追加消息，通过丢弃最旧的消息来维护窗口大小。"""
        self.messages.append(message)
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages :]

    def all_messages(self) -> List[Message]:
        """返回窗口内包含系统提示的所有消息。"""
        return list(self.messages)


@dataclass
class SummaryMemory(Memory):
    """
    完整保留近期消息，将较旧的历史压缩为摘要。

    压缩管道有意设计为两个阶段：
    1. RuleCompressor 移除明显的噪声，保留稳定的骨架。
    2. 可选的 Summarizer 可以将该骨架重写为更短的摘要。
    """

    system_prompt: str
    max_recent_messages: int = 10
    max_summary_chars: int = 1200
    summary_prefix: str = "对话摘要："
    compressor: RuleCompressor = field(default_factory=RuleCompressor)
    summarizer: Optional[Summarizer] = None
    summary: str = ""
    recent_messages: List[Message] = field(default_factory=list)

    def append(self, message: Message) -> None:
        """总入口：追加消息，如果近期消息超过限制则压缩历史。"""
        self.recent_messages.append(message)
        if len(self.recent_messages) > self.max_recent_messages:
            self._compress_history_messages() # 开始压缩

    def all_messages(self) -> List[Message]:
        """返回所有消息，包括摘要（作为系统消息）和近期消息。"""
        messages: List[Message] = []
        if self.summary:
            messages.append(
                Message(
                    role="system",
                    content=f"{self.summary_prefix}\n{self.summary}",
                )
            )
        messages.extend(self.recent_messages)
        return messages

    def clear(self) -> None:
        """清空摘要和所有近期消息。"""
        self.summary = ""
        self.recent_messages.clear()

    def snapshot(self) -> dict:
        """返回当前内存状态的快照，用于调试/检查。"""
        return {
            "summary": self.summary,
            "recent_messages": list(self.recent_messages),
            "recent_count": len(self.recent_messages),
        }

    def _compress_history_messages(self) -> None:
        """最上层的调度器，将 recent_messages 中的溢出消息压缩到摘要中。"""
        # 1、先挑选要被压缩的消息
        messages_to_compress = self._select_messages_to_compress()
        if not messages_to_compress:
            return
        
        # 2、把挑选出的旧消息压缩成摘要片段
        summary_chunk = self._build_summary_chunk(messages_to_compress)
        if not summary_chunk:
            return

        self.summary = (
            f"{self.summary}\n{summary_chunk}".strip()
            if self.summary
            else summary_chunk
        )
        # 3、对最终的总摘要检查，还超过就强行裁剪
        self._trim_summary()

    def _select_messages_to_compress(self) -> List[Message]:
        """1.1、这个方法在挑选要被压缩的信息。

        按完整对话轮次压缩：如果下一条不是新的用户消息，
        则前面的助手/工具消息属于同一轮次，应该一起压缩。
        """
        overflow_count = len(self.recent_messages) - self.max_recent_messages
        if overflow_count <= 0:
            return []

        batch = list(self.recent_messages[:overflow_count]) #超出的最旧消息
        remaining = list(self.recent_messages[overflow_count:]) #不需要压缩的

        """如果 remaining 里最前面那条消息不是新的 user 消息，
            说明前面的上下文轮次还没完整结束，不能硬生生切断。"""
        while remaining and remaining[0].role != "user":
            batch.append(remaining.pop(0))

        self.recent_messages = remaining
        return batch

    def _build_summary_chunk(self, messages: Sequence[Message]) -> str:
        """1.2、这个方法正式开始压缩。"""
        # 这里是规则压缩
        summary_text = self.compressor.compress_messages(messages).strip()
        if not summary_text:
            return ""
        # 如果配了LLM压缩，那就再总结一次
        if self.summarizer is not None:
            refined = self.summarizer.summarize(summary_text).strip()
            if refined:
                summary_text = refined

        return summary_text

    def _trim_summary(self) -> None:
        """将摘要裁剪到符合 max_summary_chars 限制的范围内，防止总摘要无限变大。"""
        if len(self.summary) <= self.max_summary_chars:
            return

        if self.summarizer is not None:
            refined = self.summarizer.summarize(self.summary).strip()
            if refined:
                self.summary = refined

        if len(self.summary) <= self.max_summary_chars:
            return

        if self.max_summary_chars <= 3:
            self.summary = self.summary[: self.max_summary_chars]
            return

        self.summary = f"...{self.summary[-(self.max_summary_chars - 3):]}"
