import asyncio
import json

import groq
import httpx

from app.config import Settings
from app.groq_agent import GroqTutorAgent


def sse(chunks):
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


def chunk(delta, finish=None, usage=None):
    c = {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": "openai/gpt-oss-120b",
         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage:
        c["x_groq"] = {"id": "x", "usage": usage}
    return c


TOOL_TURN = [
    chunk({"role": "assistant", "tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                                "function": {"name": "set_current_lesson", "arguments": ""}}]}),
    chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"subject":"Networking",'}}]}),
    chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"topic":"BGP","step":"AS basics"}'}}]}),
    chunk({}, "tool_calls", {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}),
]
TEXT_TURN = [
    chunk({"role": "assistant", "content": "Do you know what an "}),
    chunk({"content": "Autonomous System is?"}),
    chunk({}, "stop", {"prompt_tokens": 70, "completion_tokens": 8, "total_tokens": 78}),
]


def make_agent(memory, responses, **settings):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        r = responses.pop(0)
        return r if isinstance(r, httpx.Response) else sse(r)

    client = groq.AsyncGroq(api_key="gsk-test", max_retries=0,
                            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    agent = GroqTutorAgent(client, Settings(groq_api_key="gsk-test", **settings), memory, "SYSTEM")
    return agent, requests


def run(agent, *args, **kwargs):
    async def collect():
        return [e async for e in agent.run_turn(*args, **kwargs)]
    return asyncio.run(collect())


def test_tool_call_then_answer(memory):
    agent, requests = make_agent(memory, [TOOL_TURN, TEXT_TURN])
    events = run(agent, "s1", "Mujhe BGP samjhao", mode="teach")

    assert "".join(e["text"] for e in events if e["type"] == "text") == "Do you know what an Autonomous System is?"
    assert any(e["type"] == "status" for e in events)
    assert events[-1] == {"type": "done", "usage": {"input_tokens": 120, "output_tokens": 18}}
    assert memory.get_current_lesson()["topic"] == "BGP"

    first = requests[0]
    assert first["model"] == "openai/gpt-oss-120b"
    assert first["messages"][0] == {"role": "system", "content": "SYSTEM"}
    assert "<session_context>" in first["messages"][1]["content"]
    assert first["tools"][0]["type"] == "function"
    assert first["reasoning_effort"] == "low" and first["include_reasoning"] is False
    second = requests[1]["messages"]
    assert second[2]["tool_calls"][0]["function"]["arguments"] == \
        '{"subject":"Networking","topic":"BGP","step":"AS basics"}'
    assert second[3] == {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": true}'}

    # History is persisted under the provider key and replayed on the next turn
    assert agent.visible_history("s1") == [
        {"role": "user", "text": "Mujhe BGP samjhao"},
        {"role": "assistant", "text": "Do you know what an Autonomous System is?"},
    ]
    assert memory.get_history("s1") == []


def test_history_replayed_next_turn(memory):
    agent, requests = make_agent(memory, [TEXT_TURN, TEXT_TURN])
    run(agent, "s1", "hello")
    run(agent, "s1", "again")
    roles = [m["role"] for m in requests[1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_bad_tool_arguments_return_error_result(memory):
    bad = [chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                  "function": {"name": "update_progress", "arguments": '{"subject": "AI"'}}]}),
           chunk({}, "tool_calls")]
    agent, requests = make_agent(memory, [bad, TEXT_TURN])
    run(agent, "s1", "hi")
    tool_msg = requests[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["content"].startswith("Error: invalid tool input")


def test_tool_use_failed_is_retried_once(memory):
    failed = httpx.Response(400, json={"error": {"message": "Failed to call a function", "type": "invalid_request_error",
                                                  "code": "tool_use_failed"}})
    agent, requests = make_agent(memory, [failed, TEXT_TURN])
    events = run(agent, "s1", "hi")
    assert events[-1]["type"] == "done" and len(requests) == 2


def test_auth_error_message(memory):
    agent, _ = make_agent(memory, [httpx.Response(401, json={"error": {"message": "Invalid API Key"}})])
    events = run(agent, "s1", "hi")
    assert events[-1] == {"type": "error", "text": "Groq API key is missing or invalid. Set GROQ_API_KEY."}
    assert agent.visible_history("s1") == []


def test_api_error_message_shown(memory):
    agent, _ = make_agent(memory, [httpx.Response(404, json={"error": {"message": "The model `x` does not exist"}})])
    events = run(agent, "s1", "hi")
    assert events[-1] == {"type": "error", "text": "Groq API error (404): The model `x` does not exist"}


def test_no_reasoning_params_for_non_reasoning_model(memory):
    agent, requests = make_agent(memory, [TEXT_TURN], groq_model="llama-3.3-70b-versatile")
    run(agent, "s1", "hi")
    assert "reasoning_effort" not in requests[0] and "include_reasoning" not in requests[0]


def test_reset_clears_only_this_provider(memory):
    agent, _ = make_agent(memory, [TEXT_TURN])
    run(agent, "s1", "hi")
    agent.reset("s1")
    assert agent.visible_history("s1") == []


def test_rate_limit_message(memory):
    msg = "Rate limit reached for model `openai/gpt-oss-120b` on tokens per minute (TPM): Limit 8000"
    agent, _ = make_agent(memory, [httpx.Response(429, json={"error": {"message": msg}})])
    events = run(agent, "s1", "hi")
    assert events[-1] == {"type": "error", "text": f"Groq rate limit reached: {msg}"}


def test_window_history_starts_at_user_turn():
    from app.groq_agent import window_history

    history = [
        {"role": "user", "content": "a" * 100},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
        {"role": "tool", "tool_call_id": "1", "content": "x" * 50},
        {"role": "assistant", "content": "b" * 100},
        {"role": "user", "content": "c" * 100},
        {"role": "assistant", "content": "d" * 100},
    ]
    assert window_history(history, 10_000) == history
    # Budget covers the last few messages but not the first user turn: never start mid tool exchange
    window = window_history(history, 400)
    assert window[0]["role"] == "user" and window[0]["content"] == "c" * 100
    assert window_history(history, 10) == []


def test_only_recent_history_is_sent(memory):
    agent, requests = make_agent(memory, [TEXT_TURN] * 6, groq_history_chars=600)
    for i in range(5):
        run(agent, "s1", f"question {i} " + "x" * 100)
    sent = requests[-1]["messages"]
    assert sent[0]["role"] == "system" and sent[1]["role"] == "user"
    assert len(sent) < 1 + 2 * 5  # older turns dropped from the request
    assert len(agent.visible_history("s1")) == 10  # but all turns are kept in storage


def test_rate_limit_waits_and_retries(memory, monkeypatch):
    import app.groq_agent as ga

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(ga.asyncio, "sleep", fake_sleep)
    limited = httpx.Response(429, headers={"retry-after": "7"},
                             json={"error": {"message": "Rate limit reached. Please try again in 6.5s."}})
    agent, requests = make_agent(memory, [limited, TEXT_TURN])
    events = run(agent, "s1", "hi")
    assert {"type": "status", "text": "Groq free-plan limit, waiting 7s…"} in events
    assert slept == [7.5] and events[-1]["type"] == "done"


def test_long_rate_limit_reports_error(memory):
    limited = httpx.Response(429, headers={"retry-after": "3600"},
                             json={"error": {"message": "Rate limit reached on tokens per day (TPD)"}})
    agent, _ = make_agent(memory, [limited])
    events = run(agent, "s1", "hi")
    assert events[-1]["type"] == "error" and "tokens per day" in events[-1]["text"]


def test_request_too_large_retries_with_less_history(memory):
    too_big = httpx.Response(413, json={"error": {"message": "Request too large ... (TPM): Limit 8000",
                                                  "code": "rate_limit_exceeded"}})
    agent, requests = make_agent(memory, [TEXT_TURN, TEXT_TURN, too_big, TEXT_TURN])
    run(agent, "s1", "one " + "x" * 400)
    run(agent, "s1", "two " + "x" * 400)
    events = run(agent, "s1", "three")
    assert events[-1]["type"] == "done"
    assert len(requests[3]["messages"]) < len(requests[2]["messages"])
