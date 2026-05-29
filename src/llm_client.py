from __future__ import annotations

import json
import logging
from typing import Any

from .config import load_settings

logger = logging.getLogger(__name__)


class LLMClient:
    """Small OpenAI-compatible wrapper used by planner and answer layers."""

    def __init__(self) -> None:
        self.settings = load_settings()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.openai_api_key)

    def chat_json(self, system_prompt: str, user_prompt: str, timeout: float = 20.0) -> dict[str, Any] | None:
        content = self.chat(system_prompt, user_prompt, timeout=timeout, json_mode=True)
        if not content:
            return None
        try:
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

    def chat(self, system_prompt: str, user_prompt: str, timeout: float = 20.0, json_mode: bool = False) -> str:
        if not self.enabled:
            return ""
        try:
            from openai import OpenAI
        except Exception as exc:
            logger.warning("openai SDK 不可用，跳过 LLM 调用: %s", exc)
            return ""

        kwargs: dict[str, Any] = {"api_key": self.settings.openai_api_key, "timeout": timeout, "max_retries": 1}
        if self.settings.openai_base_url:
            kwargs["base_url"] = self.settings.openai_base_url

        try:
            client = OpenAI(**kwargs)
            request: dict[str, Any] = {
                "model": self.settings.model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0,
            }
            if json_mode:
                request["response_format"] = {"type": "json_object"}
            response = client.chat.completions.create(**request)
            return response.choices[0].message.content or ""
        except Exception as exc:
            logger.warning("LLM 调用失败，使用规则结果: %s", exc)
            return ""
