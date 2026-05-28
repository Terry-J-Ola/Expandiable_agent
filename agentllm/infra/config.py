from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agentllm.infra.errors import ConfigurationError


@dataclass(frozen=True)
class OpenAISettings:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 60.0

    @classmethod
    def load(cls, env_path: Optional[str] = None) -> "OpenAISettings":
        cls.load_env_file(env_path)

        api_key = cls._first_env("API_KEY", "OPENAI_API_KEY")
        base_url = cls._first_env("BASE_URL", "OPENAI_BASE_URL")
        model = cls._first_env("MODEL", "OPENAI_MODEL")
        timeout_raw = cls._first_env("TIMEOUT_SECONDS", "OPENAI_TIMEOUT_SECONDS") or "60"

        if not api_key:
            raise ConfigurationError("Missing API_KEY")
        if not base_url:
            raise ConfigurationError("Missing BASE_URL")
        if not model:
            raise ConfigurationError("Missing MODEL")

        return cls(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            model=model,
            timeout_seconds=float(timeout_raw),
        )

    @classmethod
    def is_configured(cls, env_path: Optional[str] = None) -> bool:
        try:
            cls.load(env_path)
            return True
        except ConfigurationError:
            return False

    @staticmethod
    def load_env_file(path: Optional[str] = None) -> None:
        candidates = [Path(path)] if path else [
            Path.cwd() / ".env",
            Path(__file__).resolve().parents[1] / ".env",
        ]
        for candidate in candidates:
            if not candidate.exists():
                continue
            for raw_line in candidate.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    os.environ.setdefault(key, value)
            return

    @staticmethod
    def _first_env(*keys: str) -> str:
        for key in keys:
            value = os.getenv(key, "").strip()
            if value:
                return value
        return ""
