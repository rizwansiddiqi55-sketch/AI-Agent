"""Client-side tools the tutor can call to read and write long-term memory."""

import json
from typing import Any

from . import knowledge
from .memory import LEVELS, SUBJECTS, Memory


def _obj(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_STR = {"type": "string"}

# Tool order is fixed so the tools prefix stays byte-stable for prompt caching.
TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "get_learner_profile",
        "description": "Get Rizwan's background, certifications and learning goals. "
        "Use at the start of a session or when personalising a plan or interview.",
        "input_schema": _obj({}, []),
    },
    {
        "name": "get_progress",
        "description": "Get the recorded knowledge level for every subject/topic Rizwan has studied. "
        "Use before planning a study session, revising, or when asked about progress.",
        "input_schema": _obj({}, []),
    },
    {
        "name": "update_progress",
        "description": "Record Rizwan's demonstrated level on a topic. Only raise a level after he "
        "has shown understanding through answers or exercises, and cite that evidence.",
        "input_schema": _obj(
            {
                "subject": {"type": "string", "enum": SUBJECTS},
                "topic": {"type": "string", "description": "e.g. 'OSPF', 'Python dictionaries'"},
                "level": {"type": "string", "enum": LEVELS},
                "evidence": {"type": "string", "description": "What he demonstrated"},
            },
            ["subject", "topic", "level", "evidence"],
        ),
    },
    {
        "name": "set_current_lesson",
        "description": "Save where the current course/lesson is up to so 'Continue' can resume "
        "later. Call whenever a lesson starts or moves to a new step.",
        "input_schema": _obj(
            {
                "subject": {"type": "string", "enum": SUBJECTS},
                "topic": _STR,
                "step": {"type": "string", "description": "Next step or sub-topic to cover"},
            },
            ["subject", "topic", "step"],
        ),
    },
    {
        "name": "get_current_lesson",
        "description": "Get the saved lesson position. Use when Rizwan says 'Continue' or "
        "'Continue my course'.",
        "input_schema": _obj({}, []),
    },
    {
        "name": "save_note",
        "description": "Save a short revision note (key point, mistake to remember, homework) "
        "for later review.",
        "input_schema": _obj({"topic": _STR, "content": _STR}, ["topic", "content"]),
    },
    {
        "name": "get_notes",
        "description": "Get saved revision notes, optionally filtered by topic. Use for "
        "'Revise' or to recall earlier sessions.",
        "input_schema": _obj({"topic": _STR}, []),
    },
    {
        "name": "log_english_correction",
        "description": "Record an important English correction so recurring mistakes can be "
        "reviewed later.",
        "input_schema": _obj(
            {"original": _STR, "corrected": _STR, "rule": _STR},
            ["original", "corrected", "rule"],
        ),
    },
    {
        "name": "search_knowledge",
        "description": "Search the built-in learning library (notes, verified commands and interview "
        "Q&A with model answers) for networking, security, SD-WAN, Python/automation, AI and "
        "English. Use it to ground explanations and troubleshooting steps in accurate material.",
        "input_schema": _obj(
            {
                "query": {"type": "string", "description": "Topic or question, e.g. 'OSPF ExStart'"},
                "subject": {"type": "string", "enum": SUBJECTS},
            },
            ["query"],
        ),
    },
    {
        "name": "get_practice_questions",
        "description": "Get practice or interview questions WITH model answers from the library for "
        "a topic. Ask them one at a time; never reveal the model answer before Rizwan answers. "
        "Use the model answer to grade and improve his answer.",
        "input_schema": _obj(
            {
                "topic": {"type": "string", "description": "e.g. 'BGP', 'IPsec', 'RAG', 'interview'"},
                "count": {"type": "integer", "minimum": 1, "maximum": 5},
                "level": {"type": "string", "enum": ["Beginner", "Intermediate", "Advanced"]},
            },
            ["topic"],
        ),
    },
    {
        "name": "get_english_corrections",
        "description": "Get recent English corrections to review recurring mistakes.",
        "input_schema": _obj({}, []),
    },
]

TOOLS_BY_NAME = {t["name"]: t for t in TOOL_DEFINITIONS}

# Short status labels shown in the UI while a tool runs
TOOL_STATUS = {
    "get_learner_profile": "Checking your profile…",
    "get_progress": "Checking your progress…",
    "update_progress": "Updating your progress…",
    "set_current_lesson": "Saving lesson position…",
    "get_current_lesson": "Loading your course…",
    "save_note": "Saving a note…",
    "get_notes": "Reading your notes…",
    "log_english_correction": "Logging English correction…",
    "get_english_corrections": "Reviewing English corrections…",
    "search_knowledge": "Checking the study library…",
    "get_practice_questions": "Picking practice questions…",
}


def api_tools() -> list[dict]:
    """Tool definitions as sent to the API (eager input streaming on each tool)."""
    return [{**t, "eager_input_streaming": True} for t in TOOL_DEFINITIONS]


class ToolInputError(ValueError):
    pass


def validate_input(name: str, data: Any) -> dict:
    """Validate a tool input against its schema.

    Eager input streaming means the API does not validate inputs, so do it here.
    """
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise ToolInputError(f"Unknown tool: {name}")
    if not isinstance(data, dict):
        raise ToolInputError("Tool input must be a JSON object")
    schema = tool["input_schema"]
    props = schema["properties"]
    for key in schema["required"]:
        if key not in data:
            raise ToolInputError(f"Missing required field: {key}")
    for key, value in data.items():
        if key not in props:
            raise ToolInputError(f"Unexpected field: {key}")
        if props[key].get("type") == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ToolInputError(f"Field {key} must be an integer")
            lo, hi = props[key].get("minimum"), props[key].get("maximum")
            if (lo is not None and value < lo) or (hi is not None and value > hi):
                raise ToolInputError(f"Field {key} must be between {lo} and {hi}")
            continue
        if not isinstance(value, str):
            raise ToolInputError(f"Field {key} must be a string")
        allowed = props[key].get("enum")
        if allowed and value not in allowed:
            raise ToolInputError(f"Field {key} must be one of {allowed}")
    return data


def _search_knowledge(query: str, subject: str | None = None) -> Any:
    results = knowledge.search(query, subject, limit=3)
    if not results:
        return {"results": [], "note": "Nothing in the library for this; answer from general knowledge "
                "and say so if unsure.", "topics": knowledge.list_topic_titles()}
    trimmed = []
    for r in results:
        if r["type"] == "topic":
            trimmed.append({"topic": r["topic"], "key_points": r["key_points"][:6],
                            "commands": r["commands"][:4]})
        else:
            trimmed.append({"topic": r["topic"], "q": r["q"], "model_answer": r["a"]})
    return {"results": trimmed}


def _practice_questions(topic: str, count: int = 3, level: str | None = None) -> Any:
    found = knowledge.practice_questions(topic, count, level)
    if found is None:
        return {"questions": [], "topics": knowledge.list_topic_titles()}
    return {
        "topic": found["topic"],
        "instructions": "Ask one question at a time. Do not reveal model_answer until he has answered.",
        "questions": [{"q": q["q"], "level": q["level"], "model_answer": q["a"]}
                      for q in found["questions"]],
    }


def execute_tool(memory: Memory, name: str, data: Any) -> str:
    """Run a tool and return its result as a JSON string. Raises ToolInputError on bad input."""
    args = validate_input(name, data)
    if name == "get_learner_profile":
        result: Any = memory.get_profile()
    elif name == "get_progress":
        result = memory.get_progress() or "No progress recorded yet."
    elif name == "update_progress":
        memory.update_progress(**args)
        result = {"ok": True}
    elif name == "set_current_lesson":
        memory.set_current_lesson(**args)
        result = {"ok": True}
    elif name == "get_current_lesson":
        result = memory.get_current_lesson() or "No lesson in progress yet."
    elif name == "save_note":
        memory.save_note(**args)
        result = {"ok": True}
    elif name == "get_notes":
        result = memory.get_notes(args.get("topic")) or "No notes saved yet."
    elif name == "log_english_correction":
        memory.log_english_correction(**args)
        result = {"ok": True}
    elif name == "get_english_corrections":
        result = memory.get_english_corrections() or "No corrections logged yet."
    elif name == "search_knowledge":
        result = _search_knowledge(**args)
    elif name == "get_practice_questions":
        result = _practice_questions(**args)
    else:  # pragma: no cover - guarded by validate_input
        raise ToolInputError(f"Unknown tool: {name}")
    return json.dumps(result, ensure_ascii=False)
