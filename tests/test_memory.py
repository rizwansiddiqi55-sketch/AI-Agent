import pytest


def test_profile_seeded(memory):
    assert memory.get_profile()["name"] == "Rizwan Siddiqi"


def test_progress_upsert(memory):
    memory.update_progress("Networking", "OSPF", "Beginner", "explained LSAs")
    memory.update_progress("Networking", "OSPF", "Intermediate", "fixed adjacency lab")
    rows = memory.get_progress()
    assert len(rows) == 1 and rows[0]["level"] == "Intermediate"


def test_progress_rejects_bad_level(memory):
    with pytest.raises(ValueError):
        memory.update_progress("Networking", "BGP", "Expert", "x")


def test_lesson_notes_corrections(memory):
    assert memory.get_current_lesson() is None
    memory.set_current_lesson("Python", "Dictionaries", "nested dicts")
    assert memory.get_current_lesson()["step"] == "nested dicts"
    memory.save_note("BGP", "AS path is a loop-prevention attribute")
    assert memory.get_notes("bgp")[0]["topic"] == "BGP"
    memory.log_english_correction("I am working in UAE from 2015",
                                  "I have been working in the UAE since 2015", "present perfect continuous")
    assert memory.get_english_corrections()[0]["rule"] == "present perfect continuous"


def test_history_roundtrip_and_clear(memory):
    memory.append_messages("s1", [{"role": "user", "content": [{"type": "text", "text": "hi"}]}])
    assert memory.get_history("s1")[0]["content"][0]["text"] == "hi"
    assert memory.get_history("s2") == []
    memory.clear_history("s1")
    assert memory.get_history("s1") == []
