"""Tutor agent backed by Groq (OpenAI-compatible chat completions with tool calling)."""

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

import groq

from .agent import build_context_block
from .config import Settings
from .memory import Memory
from .tools import TOOL_DEFINITIONS, TOOL_STATUS, ToolInputError, execute_tool

log = logging.getLogger("tutor.groq")

_CONTEXT_RE = re.compile(r"^<session_context>.*?</session_context>\s*", re.S)


def groq_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in TOOL_DEFINITIONS
    ]


def window_history(history: list[dict], max_chars: int) -> list[dict]:
    """Most recent messages that fit in ~max_chars, starting at a user turn.

    Groq's free tier limits tokens per minute, so only recent turns are sent; long-term
    memory (lesson, progress, notes) lives in the database and is reachable through tools.
    """
    total = 0
    start = len(history)
    for i in range(len(history) - 1, -1, -1):
        total += len(json.dumps(history[i], ensure_ascii=False))
        if total > max_chars:
            break
        start = i
    while start < len(history) and history[start].get("role") != "user":
        start += 1
    return history[start:]


def _retry_after(exc: groq.APIStatusError) -> float | None:
    try:
        value = exc.response.headers.get("retry-after")
        if value:
            return float(value)
    except (AttributeError, ValueError):
        pass
    match = re.search(r"try again in ([\d.]+)s", _error_message(exc))
    return float(match.group(1)) if match else None


def _supports_reasoning_effort(model: str) -> bool:
    return "gpt-oss" in model or "qwen3" in model


def _error_message(exc: groq.APIStatusError) -> str:
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else body
    return (error or {}).get("message") or "Please try again."


def _is_tool_use_failed(exc: groq.APIStatusError) -> bool:
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else body
    return exc.status_code == 400 and (error or {}).get("code") == "tool_use_failed"


class GroqTutorAgent:
    provider = "groq"

    def __init__(self, client: Any, settings: Settings, memory: Memory, system_prompt: str):
        self.client = client
        self.settings = settings
        self.memory = memory
        self.system_message = {"role": "system", "content": system_prompt}
        self.tools = groq_tools()

    def history_key(self, session_id: str) -> str:
        # Separate from Claude-format history so switching providers never mixes formats.
        return f"groq:{session_id}"

    def visible_history(self, session_id: str) -> list[dict]:
        items = []
        for m in self.memory.get_history(self.history_key(session_id)):
            text = m["content"].get("content") if isinstance(m["content"], dict) else None
            if m["role"] not in ("user", "assistant") or not text:
                continue
            if m["role"] == "user":
                text = _CONTEXT_RE.sub("", text)
            items.append({"role": m["role"], "text": text})
        return items

    def reset(self, session_id: str) -> None:
        self.memory.clear_history(self.history_key(session_id))

    def _load_messages(self, session_id: str) -> list[dict]:
        # Stored as {"role", "content": <full message dict>} so tool calls round-trip exactly.
        return [m["content"] for m in self.memory.get_history(self.history_key(session_id))]

    def _save_messages(self, session_id: str, messages: list[dict]) -> None:
        self.memory.append_messages(
            self.history_key(session_id), [{"role": m["role"], "content": m} for m in messages]
        )

    def _request_params(self, messages: list[dict]) -> dict:
        params: dict[str, Any] = {
            "model": self.settings.groq_model,
            "messages": [self.system_message, *messages],
            "tools": self.tools,
            "tool_choice": "auto",
            "max_completion_tokens": self.settings.groq_max_tokens,
            "stream": True,
        }
        if self.settings.groq_reasoning_effort and _supports_reasoning_effort(self.settings.groq_model):
            params["reasoning_effort"] = self.settings.groq_reasoning_effort
            params["include_reasoning"] = False
        return params

    async def _stream_once(self, messages: list[dict], out: dict) -> AsyncIterator[str]:
        """Stream one completion. Yields text deltas; fills `out` with the final message."""
        text_parts: list[str] = []
        calls: dict[int, dict] = {}
        finish_reason = None
        usage = None
        stream = await self.client.chat.completions.create(**self._request_params(messages))
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            x_groq = getattr(chunk, "x_groq", None)
            if x_groq is not None and getattr(x_groq, "usage", None):
                usage = x_groq.usage
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta.content:
                text_parts.append(delta.content)
                yield delta.content
            for tc in delta.tool_calls or []:
                slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    slot["arguments"] += tc.function.arguments
            if choice.finish_reason:
                finish_reason = choice.finish_reason
        out["text"] = "".join(text_parts)
        out["tool_calls"] = [calls[i] for i in sorted(calls)]
        out["finish_reason"] = finish_reason
        out["usage"] = usage

    async def run_turn(
        self,
        session_id: str,
        text: str,
        mode: str | None = None,
        urdu_voice: bool = False,
    ) -> AsyncIterator[dict]:
        """Run one user turn. Yields UI events: text, status, info, error, done."""
        history = self._load_messages(session_id)
        if len(history) >= self.settings.max_history_messages:
            self.reset(session_id)
            history = []
            yield {"type": "info", "text": "Started a fresh conversation. Your progress and notes are kept."}

        context = build_context_block(self.memory, mode, urdu_voice)
        new_messages: list[dict] = [
            {"role": "user", "content": f"{context}\n\n{text.strip() or '(silence)'}"}
        ]
        usage_total = {"input_tokens": 0, "output_tokens": 0}
        retried_tool_failure = False
        waited_for_rate_limit = False
        shrunk_history = False
        window = window_history(history, self.settings.groq_history_chars)

        try:
            rounds = 0
            while rounds < self.settings.max_tool_rounds:
                rounds += 1
                out: dict = {}
                try:
                    async for delta in self._stream_once(window + new_messages, out):
                        yield {"type": "text", "text": delta}
                except groq.RateLimitError as exc:
                    # Free tier: short waits are worth it; long ones (daily limit) are reported.
                    wait = _retry_after(exc)
                    if wait is not None and wait <= self.settings.groq_max_rate_limit_wait \
                            and not waited_for_rate_limit:
                        waited_for_rate_limit = True
                        yield {"type": "status", "text": f"Groq free-plan limit, waiting {wait:.0f}s…"}
                        await asyncio.sleep(wait + 0.5)
                        rounds -= 1
                        continue
                    raise
                except groq.APIStatusError as exc:
                    # Groq rejects malformed tool calls with tool_use_failed; one retry usually fixes it.
                    if _is_tool_use_failed(exc) and not retried_tool_failure:
                        retried_tool_failure = True
                        log.warning("tool_use_failed, retrying once")
                        rounds -= 1
                        continue
                    # 413: this single request exceeds the per-minute token limit; send less history.
                    if exc.status_code == 413 and not shrunk_history and window:
                        shrunk_history = True
                        window = window_history(window, self.settings.groq_history_chars // 4)
                        log.warning("request too large, retrying with %d history messages", len(window))
                        rounds -= 1
                        continue
                    raise

                usage = out.get("usage")
                if usage is not None:
                    usage_total["input_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
                    usage_total["output_tokens"] += getattr(usage, "completion_tokens", 0) or 0

                tool_calls = out["tool_calls"]
                assistant: dict[str, Any] = {"role": "assistant", "content": out["text"] or None}
                if tool_calls:
                    assistant["tool_calls"] = [
                        {"id": c["id"], "type": "function",
                         "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                        for c in tool_calls
                    ]
                if assistant["content"] is None and not tool_calls:
                    break
                new_messages.append(assistant)

                if not tool_calls:
                    if out["finish_reason"] == "length":
                        yield {"type": "info", "text": "(Response was cut short. Say 'continue' for more.)"}
                    break

                for call in tool_calls:
                    yield {"type": "status", "text": TOOL_STATUS.get(call["name"], "Working…")}
                    try:
                        args = json.loads(call["arguments"] or "{}")
                        result = execute_tool(self.memory, call["name"], args)
                    except (ToolInputError, json.JSONDecodeError) as exc:
                        result = f"Error: invalid tool input ({exc}). Fix the arguments and try again."
                    new_messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            else:
                yield {"type": "info", "text": "(Stopped after too many tool steps.)"}
        except groq.AuthenticationError:
            yield {"type": "error", "text": "Groq API key is missing or invalid. Set GROQ_API_KEY."}
            return
        except groq.RateLimitError as exc:
            log.warning("Groq rate limit: %s", _error_message(exc))
            yield {"type": "error", "text": f"Groq rate limit reached: {_error_message(exc)}"}
            return
        except groq.APIStatusError as exc:
            log.exception("Groq API error")
            yield {"type": "error", "text": f"Groq API error ({exc.status_code}): {_error_message(exc)}"}
            return
        except groq.APIConnectionError:
            yield {"type": "error", "text": "Could not reach the Groq API. Check your internet connection."}
            return
        except Exception:
            log.exception("Unexpected error during turn")
            yield {"type": "error", "text": "Something went wrong. Check the server log "
                                            "and make sure GROQ_API_KEY is set."}
            return

        # Only persist a turn that ends with an assistant message (never a dangling tool result)
        if new_messages[-1]["role"] == "assistant":
            self._save_messages(session_id, new_messages)
        log.info("usage %s", usage_total)
        yield {"type": "done", "usage": usage_total}
