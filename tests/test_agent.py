import asyncio

from app.agent import TutorAgent, prepare_assistant_content
from app.config import Settings
from tests.conftest import Block, FakeClient


def make_agent(memory, script, **overrides):
    settings = Settings(anthropic_api_key="test", **overrides)
    return TutorAgent(FakeClient(script), settings, memory, "SYSTEM PROMPT")


def run(agent, *args, **kwargs):
    async def collect():
        return [e async for e in agent.run_turn(*args, **kwargs)]
    return asyncio.run(collect())


def test_tool_loop_then_answer(memory):
    script = [
        ([], [Block(type="thinking", thinking="", signature="sig"),
              Block(type="tool_use", id="tu1", name="set_current_lesson",
                    input={"subject": "Networking", "topic": "OSPF", "step": "areas"})], "tool_use"),
        (["Let's start ", "with OSPF."], [Block(type="text", text="Let's start with OSPF.")], "end_turn"),
    ]
    agent = make_agent(memory, script)
    events = run(agent, "default", "OSPF mujhe samjhao", mode="teach")

    assert [e["text"] for e in events if e["type"] == "text"] == ["Let's start ", "with OSPF."]
    assert any(e["type"] == "status" for e in events)
    assert events[-1]["type"] == "done"
    assert memory.get_current_lesson()["topic"] == "OSPF"

    history = memory.get_history("default")
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    assert history[2]["content"][0]["tool_use_id"] == "tu1"
    # Thinking block echoed back unchanged
    assert history[1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig"}

    call = agent.client.calls[0]
    assert call["system"][0]["cache_control"]["ttl"] == "1h"
    assert call["thinking"] == {"type": "adaptive"}
    assert call["fallbacks"] == "default"
    assert "session_context" in call["messages"][0]["content"][0]["text"]
    assert "structured lesson" in call["messages"][0]["content"][0]["text"]
    # Second request carries the first request's messages as an unchanged prefix
    second = agent.client.calls[1]["messages"]
    assert second[: len(call["messages"])] == call["messages"]


def test_invalid_tool_input_returns_error_result(memory):
    script = [
        ([], [Block(type="tool_use", id="tu1", name="update_progress", input={"subject": "AI"})], "tool_use"),
        (["ok"], [Block(type="text", text="ok")], "end_turn"),
    ]
    agent = make_agent(memory, script)
    run(agent, "default", "hi")
    result = memory.get_history("default")[2]["content"][0]
    assert result["is_error"] is True


def test_refusal_discards_turn(memory):
    agent = make_agent(memory, [([], [], "refusal")])
    events = run(agent, "default", "hi")
    assert events[-1]["type"] == "error"
    assert memory.get_history("default") == []


def test_history_reset_when_too_long(memory):
    memory.append_messages("default", [
        {"role": "user", "content": [{"type": "text", "text": "q"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "a"}]},
    ])
    agent = make_agent(memory, [(["hi"], [Block(type="text", text="hi")], "end_turn")],
                       max_history_messages=2)
    events = run(agent, "default", "hello")
    assert events[0]["type"] == "info"
    assert len(agent.client.calls[0]["messages"]) == 1


def test_fallback_disabled(memory):
    agent = make_agent(memory, [(["hi"], [Block(type="text", text="hi")], "end_turn")],
                       enable_fallback=False)
    run(agent, "default", "hello")
    assert "fallbacks" not in agent.client.calls[0]


def test_prepare_content_handles_fallback_boundary():
    blocks = [
        {"type": "thinking", "thinking": "", "signature": "a"},
        {"type": "text", "text": "partial"},
        {"type": "fallback"},
        {"type": "thinking", "thinking": "", "signature": "b"},
        {"type": "text", "text": ""},
        {"type": "text", "text": "rest"},
    ]
    out = prepare_assistant_content(blocks, "end_turn")
    assert out == [
        {"type": "text", "text": "partial"},
        {"type": "thinking", "thinking": "", "signature": "b"},
        {"type": "text", "text": "rest"},
    ]


def test_prepare_content_drops_tool_use_on_max_tokens():
    blocks = [{"type": "text", "text": "x"}, {"type": "tool_use", "id": "1", "name": "n", "input": {}}]
    assert prepare_assistant_content(blocks, "max_tokens") == [{"type": "text", "text": "x"}]
