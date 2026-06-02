from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import load_settings

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """Represents a single tool call from the LLM."""
    id: str
    name: str
    arguments: dict[str, Any]

    @classmethod
    def from_openai(cls, raw: dict[str, Any]) -> ToolCall:
        func = raw.get("function", {})
        try:
            args = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            args = {}
        return cls(id=raw.get("id", ""), name=func.get("name", ""), arguments=args)


@dataclass
class ToolResult:
    """Result from executing a tool, to be fed back to the LLM."""
    tool_call_id: str
    name: str
    content: str
    is_error: bool = False


@dataclass
class ToolDefinition:
    """OpenAI-compatible tool/function definition."""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the parameters
    strict: bool = False

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ChatResponse:
    """Structured response from chat_with_tools."""
    content: str | None  # Text content (may be None if only tool calls)
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"  # "stop", "tool_calls", "length"


class LLMClient:
    """OpenAI-compatible wrapper with Tool Calling support.

    Used by all agent layers: planner, executor, reflector, answer.
    Provides three call modes:
      - chat(): free text
      - chat_json(): JSON mode
      - chat_with_tools(): Tool calling mode (the backbone of agentic behavior)
    """

    def __init__(self) -> None:
        self.settings = load_settings()

    # ── helpers ──────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return bool(self.settings.openai_api_key)

    def _create_client(self, timeout: float = 60.0):
        """Create an OpenAI client instance. Returns None if SDK unavailable."""
        if not self.enabled:
            return None
        try:
            from openai import OpenAI
        except Exception as exc:
            logger.warning("openai SDK 不可用，跳过 LLM 调用: %s", exc)
            return None

        kwargs: dict[str, Any] = {
            "api_key": self.settings.openai_api_key,
            "timeout": timeout,
            "max_retries": 1,
        }
        if self.settings.openai_base_url:
            kwargs["base_url"] = self.settings.openai_base_url

        try:
            return OpenAI(**kwargs)
        except Exception as exc:
            logger.warning("OpenAI client 创建失败: %s", exc)
            return None

    # ── basic chat (legacy) ──────────────────────────────────────

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        timeout: float = 20.0,
        json_mode: bool = False,
    ) -> str:
        """Simple text chat. Returns empty string on failure."""
        client = self._create_client(timeout)
        if client is None:
            return ""

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

        try:
            response = client.chat.completions.create(**request)
            return response.choices[0].message.content or ""
        except Exception as exc:
            logger.warning("LLM 调用失败: %s", exc)
            return ""

    def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        timeout: float = 20.0,
    ) -> dict[str, Any] | None:
        """JSON mode chat. Returns parsed dict or None on failure."""
        content = self.chat(system_prompt, user_prompt, timeout=timeout, json_mode=True)
        if not content:
            return None
        try:
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

    # ── multi-turn conversation (for agent loops) ─────────────────

    def chat_with_history(
        self,
        messages: list[dict[str, Any]],
        timeout: float = 60.0,
        json_mode: bool = False,
    ) -> str:
        """Chat with a full message history (system + alternating user/assistant).

        messages: list of {"role": "system"|"user"|"assistant", "content": str}
        """
        client = self._create_client(timeout)
        if client is None:
            return ""

        request: dict[str, Any] = {
            "model": self.settings.model_name,
            "messages": messages,
            "temperature": 0,
        }
        if json_mode:
            request["response_format"] = {"type": "json_object"}

        try:
            response = client.chat.completions.create(**request)
            return response.choices[0].message.content or ""
        except Exception as exc:
            logger.warning("LLM chat_with_history 失败: %s", exc)
            return ""

    # ── tool calling (core of agentic behavior) ───────────────────

    def chat_with_tools(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list[ToolDefinition],
        tool_choice: str = "auto",
        timeout: float = 60.0,
    ) -> ChatResponse:
        """Single-turn tool-calling chat.

        The LLM decides whether to call tools or respond with text.
        Returns a ChatResponse with content and/or tool_calls.

        tool_choice: "auto" (LLM decides), "required" (must call a tool),
                     "none" (text-only), or a specific tool name.
        """
        client = self._create_client(timeout)
        if client is None:
            return ChatResponse(content="", finish_reason="stop")

        request: dict[str, Any] = {
            "model": self.settings.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "tools": [t.to_openai() for t in tools],
            "tool_choice": tool_choice,
        }

        try:
            response = client.chat.completions.create(**request)
            msg = response.choices[0].message
            finish = response.choices[0].finish_reason or "stop"

            tool_calls = []
            if msg.tool_calls:
                tool_calls = [
                    ToolCall.from_openai(tc.model_dump()) for tc in msg.tool_calls
                ]

            return ChatResponse(
                content=msg.content,
                tool_calls=tool_calls,
                finish_reason=finish,
            )
        except Exception as exc:
            logger.warning("LLM tool calling 失败: %s", exc)
            return ChatResponse(content="", finish_reason="stop")

    def chat_with_tools_multi_turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        tool_choice: str = "auto",
        timeout: float = 60.0,
    ) -> ChatResponse:
        """Multi-turn tool-calling chat with full message history.

        messages should include assistant messages with tool_calls,
        and tool messages with role="tool" and tool_call_id.
        """
        client = self._create_client(timeout)
        if client is None:
            return ChatResponse(content="", finish_reason="stop")

        request: dict[str, Any] = {
            "model": self.settings.model_name,
            "messages": messages,
            "temperature": 0,
            "tools": [t.to_openai() for t in tools],
            "tool_choice": tool_choice,
        }

        try:
            response = client.chat.completions.create(**request)
            msg = response.choices[0].message
            finish = response.choices[0].finish_reason or "stop"

            tool_calls = []
            if msg.tool_calls:
                tool_calls = [
                    ToolCall.from_openai(tc.model_dump()) for tc in msg.tool_calls
                ]

            return ChatResponse(
                content=msg.content,
                tool_calls=tool_calls,
                finish_reason=finish,
            )
        except Exception as exc:
            logger.warning("LLM multi-turn tool calling 失败: %s", exc)
            return ChatResponse(content="", finish_reason="stop")

    # ── ReAct loop helper ─────────────────────────────────────────

    def run_agent_loop(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list[ToolDefinition],
        tool_executor: Callable[[ToolCall], ToolResult],
        max_iterations: int = 15,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """Run a full ReAct agent loop: LLM decides → execute tools → repeat.

        Args:
            system_prompt: System-level instructions for the agent.
            user_prompt: The user's task/question.
            tools: Available tool definitions.
            tool_executor: Function that takes a ToolCall and returns a ToolResult.
            max_iterations: Safety limit on tool-calling rounds.
            timeout: Per-request timeout in seconds.

        Returns:
            dict with keys: final_answer, tool_calls_made, iterations, messages
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        tool_calls_made: list[dict[str, Any]] = []
        iterations = 0

        while iterations < max_iterations:
            iterations += 1
            response = self.chat_with_tools_multi_turn(
                messages=messages,
                tools=tools,
                tool_choice="auto",
                timeout=timeout,
            )

            # If the LLM produced no tool calls, it's the final answer
            if not response.tool_calls:
                return {
                    "final_answer": response.content or "",
                    "tool_calls_made": tool_calls_made,
                    "iterations": iterations,
                    "messages": messages,
                    "finish_reason": response.finish_reason,
                }

            # Record the assistant message with tool calls
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": response.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in response.tool_calls
                ],
            }
            messages.append(assistant_msg)

            # Execute each tool call and append results
            for tc in response.tool_calls:
                result = tool_executor(tc)
                tool_calls_made.append(
                    {
                        "tool": tc.name,
                        "arguments": tc.arguments,
                        "result": result.content[:500] if result.content else "",
                        "is_error": result.is_error,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": result.tool_call_id or tc.id,
                        "content": result.content,
                    }
                )

            # Safety: if tool_choice was "required" and got tool calls,
            # check for endless loops with the same call pattern
            if len(tool_calls_made) >= max_iterations * 3:
                logger.warning("ReAct loop hit safety limit of %d tool calls", max_iterations * 3)
                break

        # Max iterations reached — ask LLM to summarize
        messages.append(
            {
                "role": "user",
                "content": "已达到最大工具调用次数。请基于已有信息给出最佳回答。",
            }
        )
        final = self.chat_with_history(messages, timeout=timeout)
        return {
            "final_answer": final,
            "tool_calls_made": tool_calls_made,
            "iterations": iterations,
            "messages": messages,
            "finish_reason": "max_iterations",
        }
