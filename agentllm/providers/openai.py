from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence
from urllib import error, request

from agentllm.config import OpenAISettings
from agentllm.errors import ProviderHTTPError, ProviderNetworkError
from agentllm.logging_utils import get_logger
from agentllm.types import AgentAction, AgentContext, JsonDict, Message, ToolCall, ToolSpec


LOGGER = get_logger(__name__)


@dataclass(frozen=True)
class OpenAIResponseMetadata:
    """记录一次模型调用里比较有排查价值的元数据。"""

    request_id: Optional[str] = None
    response_id: Optional[str] = None
    usage: Optional[JsonDict] = None


class OpenAIProvider:
    """
    基于 OpenAI-compatible `/chat/completions` 的 provider。

    这里的“compatible”表示：
    只要服务端接受相同的请求/响应结构，这个 provider 就可以复用。
    """

    def __init__(
        self,
        settings: OpenAISettings,
        *,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
    ) -> None:
        # 保存和模型服务端交互所需的配置，以及可选的采样参数。
        self._settings = settings
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._max_retries = 2

    def complete(self, context: AgentContext) -> AgentAction:
        # 这是 provider 的主入口：把上下文发给模型，再把模型响应还原成统一的 AgentAction。
        payload = self._build_payload(context)
        response_json, metadata = self._post_json("/chat/completions", payload)
        LOGGER.info(
            "OpenAI-compatible response received: response_id=%s request_id=%s",
            metadata.response_id,
            metadata.request_id,
        )
        return self._parse_response(response_json, metadata)

    def _build_payload(self, context: AgentContext) -> JsonDict:
        # 把项目内部的 AgentContext 组装成 OpenAI-compatible 接口能识别的请求体。
        payload: JsonDict = {
            "model": self._settings.model,
            "messages": self._serialize_messages(context),
            "tools": self._serialize_tools(context.tools),
        }

        if self._temperature is not None:
            payload["temperature"] = self._temperature
        if self._max_output_tokens is not None:
            payload["max_tokens"] = self._max_output_tokens

        return payload

    def _serialize_messages(self, context: AgentContext) -> List[JsonDict]:
        # 把系统提示词和历史消息翻译成模型 API 需要的 messages 格式。
        messages: List[JsonDict] = [
            {"role": "system", "content": context.system_prompt},
        ]

        for message in context.messages:
            if message.role == "tool":
                # 工具结果必须带回 tool_call_id，模型才能把这次结果和上一次工具调用对应起来。
                messages.append(
                    {
                        "role": "tool",
                        "content": message.content,
                        "tool_call_id": message.tool_call_id or "",
                    }
                )
                continue

            payload: JsonDict = {
                "role": self._map_role(message.role),
                "content": message.content,
            }

            # 某些兼容服务要求把上一轮的 reasoning_content 原样回传，
            # 否则多轮工具调用时可能在下一轮被服务端拒绝。
            if message.reasoning_content:
                payload["reasoning_content"] = message.reasoning_content

            if message.role == "assistant" and message.tool_calls:
                # assistant 如果发起过工具调用，历史里也要把这段 tool_calls 带回去。
                payload["tool_calls"] = self._serialize_tool_calls(message.tool_calls)
            messages.append(payload)

        return messages

    def _serialize_tools(self, tools: Sequence[ToolSpec]) -> List[JsonDict]:
        # 把本地 ToolSpec 转成模型能看懂的 function tool 定义。
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in tools
        ]

    def _serialize_tool_calls(self, tool_calls: Sequence[ToolCall]) -> List[JsonDict]:
        # 把项目内部的 ToolCall 重新编码成模型 API 约定的历史格式。
        return [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in tool_calls
        ]

    def _post_json(
        self,
        path: str,
        payload: JsonDict,
    ) -> tuple[JsonDict, OpenAIResponseMetadata]:
        # 统一负责发送 POST 请求，并在这里处理重试、网络错误和响应元数据提取。
        url = f"{self._settings.base_url}{path}"
        body = json.dumps(payload).encode("utf-8")

        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Content-Type": "application/json",
        }
        # 正式请求
        req = request.Request(url=url, data=body, headers=headers, method="POST")

        # 这里只做有限重试：
        # 429、5xx、临时网络抖动值得重试，但配置错误和协议错误要尽快暴露。
        for attempt in range(1, self._max_retries + 2):
            try:
                with request.urlopen(req, timeout=self._settings.timeout_seconds) as resp:
                    raw_text = resp.read().decode("utf-8")
                    response_json = json.loads(raw_text)
                    metadata = OpenAIResponseMetadata(
                        request_id=resp.headers.get("x-request-id"),
                        response_id=response_json.get("id"),
                        usage=response_json.get("usage"),
                    )
                    return response_json, metadata
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                LOGGER.error(
                    "OpenAI-compatible HTTP error: status=%s url=%s attempt=%s body=%s",
                    exc.code,
                    url,
                    attempt,
                    detail,
                )
                if self._should_retry_http(exc.code, attempt):
                    time.sleep(0.5 * attempt)
                    continue
                raise ProviderHTTPError(
                    f"Provider HTTP error {exc.code}: {detail}"
                ) from exc
            except error.URLError as exc:
                LOGGER.error(
                    "OpenAI-compatible network error on attempt %s: %s",
                    attempt,
                    exc,
                )
                if attempt <= self._max_retries:
                    time.sleep(0.5 * attempt)
                    continue
                raise ProviderNetworkError(f"Provider network error: {exc}") from exc
        raise ProviderNetworkError("Provider request failed after retries")

    def _parse_response(
        self,
        response_json: JsonDict,
        metadata: OpenAIResponseMetadata,
    ) -> AgentAction:
        # provider 层负责把厂商 JSON 还原成统一的 AgentAction，
        # 上层 runtime 因此不需要理解具体服务商的响应细节。
        choice = self._first_choice(response_json)
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        # 解析模型生成的tool_calls
        tool_calls = self._extract_tool_calls(message)
        # 解析模型生成的文本消息
        text = self._extract_output_text(message)
        reasoning_content = self._extract_reasoning_content(message)

        if not text and tool_calls: # 如果没有文本，但是有tool_calls，那就给它添加文本，这样也不会进入第二个if
            text = "I need to call a tool before I can finish this request."
        if not text: # 如果没有文本又没tool_calls，那第一个if不会进去，直接进入第二个if
            text = "The model did not return any parsable text."

        return AgentAction(
            message=Message(
                role="assistant",
                content=text,
                tool_calls=tool_calls,
                reasoning_content=reasoning_content,
            ),
            tool_calls=tool_calls,
            raw={
                "response": response_json,
                "metadata": {
                    "request_id": metadata.request_id,
                    "response_id": metadata.response_id,
                    "usage": metadata.usage,
                },
            },
        )

    @staticmethod
    def _first_choice(response_json: JsonDict) -> JsonDict:
        # chat/completions 常见返回是 choices 数组，这里只取第一项作为当前回复。
        choices = response_json.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                return first
        return {}

    @staticmethod
    def _extract_output_text(message: JsonDict) -> str:
        # 兼容字符串 content 和分块 content 两种格式，尽量提取出最终可展示文本。
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            text_parts: List[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str) and text:
                    text_parts.append(text)
            return "\n".join(text_parts).strip()
        return ""

    @staticmethod
    def _extract_reasoning_content(message: JsonDict) -> Optional[str]:
        # 某些兼容模型会额外返回推理内容，这里单独提取出来给后续轮次复用。
        reasoning = message.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning.strip():
            return reasoning
        return None

    def _extract_tool_calls(self, message: JsonDict) -> List[ToolCall]:
        # 把模型返回的 tool_calls 转成项目内部统一的 ToolCall 对象。
        raw_calls = message.get("tool_calls")
        if not isinstance(raw_calls, list):
            return []

        calls: List[ToolCall] = []
        for item in raw_calls:
            if not isinstance(item, dict):
                continue
            function = item.get("function", {})
            if not isinstance(function, dict):
                continue

            name = function.get("name", "")
            arguments = self._safe_json_loads(function.get("arguments"))
            call_id = item.get("id", "")
            if name:
                calls.append(ToolCall(name=name, arguments=arguments, id=call_id))

        return calls

    @staticmethod
    def _map_role(role: str) -> str:
        # 这个项目的历史消息里只区分 assistant 和非 assistant；
        # 其余消息统一按 user 角色回传给模型。
        if role == "assistant":
            return "assistant"
        return "user"

    @staticmethod
    def _safe_json_loads(value: Any) -> Dict[str, Any]:
        # 工具参数在模型响应里通常是 JSON 字符串，这里做一次尽量温和的解析。
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        return {}

    @staticmethod
    def _should_retry_http(status_code: int, attempt: int) -> bool:
        # 只对典型的限流/临时故障状态码做有限重试，避免把确定性错误拖得太久。
        return status_code in {408, 429, 500, 502, 503, 504} and attempt <= 2
