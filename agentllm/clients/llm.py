from __future__ import annotations

import json
import time
from typing import Optional
from urllib import error, request

from agentllm.infra.config import OpenAISettings
from agentllm.infra.errors import ProviderHTTPError, ProviderNetworkError


class LLMClient:
    """Small OpenAI-compatible client for single-turn chat requests."""

    def __init__(self, settings: OpenAISettings) -> None:
        self._settings = settings
        self._max_retries = 2

    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        payload = {
            "model": self._settings.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt or "You are a helpful assistant.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        }

        url = f"{self._settings.base_url}/chat/completions"
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Content-Type": "application/json",
        }
        req = request.Request(url=url, data=body, headers=headers, method="POST")

        for attempt in range(1, self._max_retries + 2):
            try:
                with request.urlopen(req, timeout=self._settings.timeout_seconds) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    break
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code in {408, 429, 500, 502, 503, 504} and attempt <= self._max_retries:
                    time.sleep(0.5 * attempt)
                    continue
                raise ProviderHTTPError(f"HTTP {exc.code}: {detail}") from exc
            except error.URLError as exc:
                if attempt <= self._max_retries:
                    time.sleep(0.5 * attempt)
                    continue
                raise ProviderNetworkError(f"Network error: {exc}") from exc

        choices = data.get("choices") or []
        if not choices:
            raise ProviderHTTPError("Model returned no choices")

        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        raise ProviderHTTPError("Model returned empty content")
