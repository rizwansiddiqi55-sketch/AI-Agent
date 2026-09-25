import json

import pytest

from app.tools import TOOL_DEFINITIONS, ToolInputError, api_tools, execute_tool


def test_tool_schemas_are_closed():
    for tool in TOOL_DEFINITIONS:
        assert tool["input_schema"]["additionalProperties"] is False
    assert all(t["eager_input_streaming"] for t in api_tools())


def test_update_and_get_progress(memory):
    execute_tool(memory, "update_progress",
                 {"subject": "AI", "topic": "RAG", "level": "Developing", "evidence": "explained retrieval"})
    out = json.loads(execute_tool(memory, "get_progress", {}))
    assert out[0]["topic"] == "RAG"


@pytest.mark.parametrize("name,data", [
    ("update_progress", {"subject": "AI", "topic": "RAG", "level": "Guru", "evidence": "x"}),
    ("update_progress", {"subject": "AI"}),
    ("save_note", {"topic": "x", "content": "y", "extra": "z"}),
    ("save_note", "not an object"),
    ("does_not_exist", {}),
])
def test_invalid_inputs_rejected(memory, name, data):
    with pytest.raises(ToolInputError):
        execute_tool(memory, name, data)


def test_empty_results_are_friendly(memory):
    assert "No lesson" in execute_tool(memory, "get_current_lesson", {})
