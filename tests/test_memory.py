import unittest

from agentllm.memory import RuleCompressor, SummaryMemory, Summarizer
from agentllm.types import Message


class FakeSummarizer(Summarizer):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def summarize(self, text: str) -> str:
        self.calls.append(text)
        return f"SUMMARY::{text[:40]}"


class SummaryMemoryTest(unittest.TestCase):
    """验证 SummaryMemory 的双层摘要骨架。"""

    def test_under_limit_keeps_recent_messages(self) -> None:
        memory = SummaryMemory(system_prompt="test", max_recent_messages=3)
        first = Message(role="user", content="hello")
        second = Message(role="assistant", content="hi")

        memory.append(first)
        memory.append(second)

        self.assertEqual(memory.summary, "")
        self.assertEqual(memory.recent_messages, [first, second])
        self.assertEqual(memory.all_messages(), [first, second])

    def test_overflow_compresses_old_messages(self) -> None:
        memory = SummaryMemory(system_prompt="test", max_recent_messages=2)
        first = Message(role="user", content="who am i")
        second = Message(role="assistant", content="you are a student")
        third = Message(role="user", content="what did i ask")

        memory.append(first)
        memory.append(second)
        memory.append(third)

        self.assertIn("用户提到", memory.summary)
        self.assertIn("who am i", memory.summary)
        self.assertIn("助手回应", memory.summary)
        self.assertEqual(memory.recent_messages, [third])
        self.assertEqual(
            memory.all_messages()[0],
            Message(
                role="system",
                content=f"Conversation summary:\n{memory.summary}",
            ),
        )

    def test_compression_prefers_complete_turns(self) -> None:
        memory = SummaryMemory(system_prompt="test", max_recent_messages=2)
        messages = [
            Message(role="user", content="question"),
            Message(role="assistant", content="I will search"),
            Message(role="tool", name="web_search", content="result payload"),
        ]

        for message in messages:
            memory.append(message)

        self.assertIn("助手回应", memory.summary)
        self.assertEqual(memory.recent_messages, [])

    def test_uses_optional_summarizer_after_rule_compression(self) -> None:
        summarizer = FakeSummarizer()
        memory = SummaryMemory(
            system_prompt="test",
            max_recent_messages=1,
            summarizer=summarizer,
        )

        memory.append(Message(role="user", content="first question"))
        memory.append(Message(role="assistant", content="first answer"))

        self.assertTrue(memory.summary.startswith("SUMMARY::"))
        self.assertEqual(len(summarizer.calls), 1)
        self.assertIn("用户提到", summarizer.calls[0])

    def test_trim_summary_calls_summarizer_and_keeps_limit(self) -> None:
        summarizer = FakeSummarizer()
        memory = SummaryMemory(
            system_prompt="test",
            max_recent_messages=1,
            max_summary_chars=30,
            summarizer=summarizer,
        )

        memory.summary = "x" * 80
        memory._trim_summary()

        self.assertLessEqual(len(memory.summary), 30)
        self.assertGreaterEqual(len(summarizer.calls), 1)

    def test_clear_and_snapshot(self) -> None:
        memory = SummaryMemory(system_prompt="test", max_recent_messages=2)
        memory.summary = "old summary"
        memory.append(Message(role="user", content="hello"))

        snapshot = memory.snapshot()
        self.assertEqual(snapshot["summary"], "old summary")
        self.assertEqual(snapshot["recent_count"], 1)

        memory.clear()
        self.assertEqual(memory.summary, "")
        self.assertEqual(memory.recent_messages, [])


class RuleCompressorTest(unittest.TestCase):
    def test_compresses_tool_message_with_tool_name(self) -> None:
        compressor = RuleCompressor(max_tool_chars=20)

        text = compressor.compress_messages(
            [Message(role="tool", name="search", content="a" * 50)]
        )

        self.assertIn("工具 search", text)
        self.assertIn("...", text)


if __name__ == "__main__":
    unittest.main()
