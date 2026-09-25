"""The tutor agent: streams Claude responses and runs the memory tool loop."""

import logging
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic

from .config import Settings
from .memory import Memory
from .modes import mode_instruction
from .tools import TOOL_STATUS, ToolInputError, api_tools, execute_tool

log = logging.getLogger("tutor.agent")

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Blocks that must not be echoed back when they appear before a mid-output fallback boundary
_PRE_FALLBACK_DROP = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}


def _api_error_message(exc: anthropic.APIStatusError) -> str:
    """The API's own explanation (e.g. low credit balance), falling back to a generic hint."""
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    return error.get("message") or "Please try again."


def load_system_prompt(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _block_to_dict(block: Any) -> dict:
    if isinstance(block, dict):
        return block
    return block.model_dump(mode="json", exclude_none=True)


def prepare_assistant_content(blocks: list[Any], stop_reason: str | None) -> list[dict]:
    """Turn response content into the assistant message to store and echo back.

    - After a mid-output fallback, drop model-internal blocks before the last `fallback` block.
    - The `fallback` marker itself and empty text blocks are dropped.
    - On `max_tokens`, a trailing tool call may be incomplete, so tool_use blocks are dropped.
    """
    content = [_block_to_dict(b) for b in blocks]
    boundary = max((i for i, b in enumerate(content) if b.get("type") == "fallback"), default=-1)
    out = []
    for i, b in enumerate(content):
        btype = b.get("type")
        if btype == "fallback":
            continue
        if i < boundary and btype in _PRE_FALLBACK_DROP:
            continue
        if btype == "text" and not b.get("text"):
            continue
        if stop_reason == "max_tokens" and btype == "tool_use":
            continue
        out.append(b)
    return out


def build_context_block(
    memory: Memory, mode: str | None, urdu_voice: bool, now: datetime | None = None
) -> str:
    now = now or datetime.now()
    lesson = memory.get_current_lesson()
    lesson_text = (
        f"{lesson['subject']} / {lesson['topic']} (next: {lesson['step']})" if lesson else "none yet"
    )
    lines = [
        "<session_context>",
        f"Date: {now:%A, %d %B %Y}",
        f"Saved lesson position: {lesson_text}",
        "Channel: voice. Rizwan's words come from speech recognition (may contain transcription "
        "errors); your reply will be read aloud by text-to-speech.",
        (
            "A natural Urdu voice is available: write any Urdu in Urdu script (not Roman Urdu), "
            "even if Rizwan types Roman Urdu, so it is pronounced correctly. Keep technical terms "
            "and commands in English letters."
            if urdu_voice
            else "No Urdu voice on this device: write any Urdu in Roman Urdu so it can be spoken."
        ),
    ]
    instruction = mode_instruction(mode)
    if instruction:
        lines.append(f"Requested mode: {instruction}")
    lines.append("</session_context>")
    return "\n".join(lines)


class TutorAgent:
    provider = "anthropic"

    def __init__(self, client: Any, settings: Settings, memory: Memory, system_prompt: str):
        self.client = client
        self.settings = settings
        self.memory = memory
        # Frozen system prompt: cached for 1h. Never put volatile data here.
        self.system = [
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral", "ttl": "1h"}}
        ]
        self.tools = api_tools()

    def history_key(self, session_id: str) -> str:
        return session_id

    def visible_history(self, session_id: str) -> list[dict]:
        """Visible transcript (text only) for restoring the UI after a reload."""
        items = []
        for m in self.memory.get_history(self.history_key(session_id)):
            texts = [b["text"] for b in m["content"] if isinstance(b, dict) and b.get("type") == "text"]
            if m["role"] == "user" and texts and texts[0].startswith("<session_context>"):
                texts = texts[1:]
            if texts:
                items.append({"role": m["role"], "text": "\n".join(texts)})
        return items

    def reset(self, session_id: str) -> None:
        self.memory.clear_history(self.history_key(session_id))

    def _request_params(self, messages: list[dict]) -> dict:
        params: dict[str, Any] = {
            "model": self.settings.claude_model,
            "max_tokens": self.settings.max_tokens,
            "system": self.system,
            "tools": self.tools,
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.settings.effort},
            # Auto-cache the conversation so far (second breakpoint after the system prompt)
            "cache_control": {"type": "ephemeral"},
        }
        if self.settings.enable_fallback:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    async def run_turn(
        self,
        session_id: str,
        text: str,
        mode: str | None = None,
        urdu_voice: bool = False,
    ) -> AsyncIterator[dict]:
        """Run one user turn. Yields UI events: text, status, info, error, done."""
        history = self.memory.get_history(self.history_key(session_id))
        if len(history) >= self.settings.max_history_messages:
            # Simple compaction: start a fresh history. Long-term memory (lesson, progress,
            # notes) survives in the database, and the prefix is never edited in place.
            self.reset(session_id)
            history = []
            yield {"type": "info", "text": "Started a fresh conversation. Your progress and notes are kept."}

        user_message = {
            "role": "user",
            "content": [
                {"type": "text", "text": build_context_block(self.memory, mode, urdu_voice)},
                {"type": "text", "text": text.strip() or "(silence)"},
            ],
        }
        new_messages: list[dict] = [user_message]
        usage_total = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                       "cache_creation_input_tokens": 0}

        try:
            for _ in range(self.settings.max_tool_rounds):
                params = self._request_params(history + new_messages)
                async with self.client.beta.messages.stream(**params) as stream:
                    async for event in stream:
                        if event.type == "text" and event.text:
                            yield {"type": "text", "text": event.text}
                    message = await stream.get_final_message()

                for key in usage_total:
                    usage_total[key] += getattr(message.usage, key, 0) or 0

                if message.stop_reason == "refusal":
                    # Discard the whole turn so history stays valid and unedited.
                    yield {"type": "error", "text": "Sorry, I can't help with that request. Let's try another topic."}
                    return

                content = prepare_assistant_content(message.content, message.stop_reason)
                if not content:
                    break
                new_messages.append({"role": "assistant", "content": content})

                tool_uses = [b for b in content if b.get("type") == "tool_use"]
                if message.stop_reason == "tool_use" and tool_uses:
                    results = []
                    for tu in tool_uses:
                        yield {"type": "status", "text": TOOL_STATUS.get(tu["name"], "Working…")}
                        try:
                            output = execute_tool(self.memory, tu["name"], tu.get("input"))
                            results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": output})
                        except ToolInputError as exc:
                            results.append({"type": "tool_result", "tool_use_id": tu["id"],
                                            "content": f"Invalid input: {exc}", "is_error": True})
                    new_messages.append({"role": "user", "content": results})
                    continue
                if message.stop_reason == "pause_turn":
                    continue
                if message.stop_reason == "max_tokens":
                    yield {"type": "info", "text": "(Response was cut short. Say 'continue' for more.)"}
                break
            else:
                yield {"type": "info", "text": "(Stopped after too many tool steps.)"}
        except anthropic.AuthenticationError:
            yield {"type": "error", "text": "Anthropic API key is missing or invalid. Set ANTHROPIC_API_KEY in .env."}
            return
        except anthropic.RateLimitError:
            yield {"type": "error", "text": "Rate limited by the API. Please wait a moment and try again."}
            return
        except anthropic.APIStatusError as exc:
            log.exception("API error")
            yield {"type": "error", "text": f"Anthropic API error ({exc.status_code}): {_api_error_message(exc)}"}
            return
        except anthropic.APIConnectionError:
            yield {"type": "error", "text": "Could not reach the Anthropic API. Check your internet connection."}
            return
        except Exception:
            # e.g. no credentials configured (the SDK raises TypeError before sending)
            log.exception("Unexpected error during turn")
            yield {"type": "error", "text": "Something went wrong. Check the server log "
                                            "and make sure ANTHROPIC_API_KEY is set in .env."}
            return

        # Only persist a turn that ends with an assistant message (never a dangling tool_result)
        if new_messages[-1]["role"] == "assistant":
            self.memory.append_messages(self.history_key(session_id), new_messages)
        log.info("usage %s", usage_total)
        yield {"type": "done", "usage": usage_total}
