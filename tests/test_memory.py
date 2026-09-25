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


def test_clean_postgres_url_supabase():
    from app.memory import clean_postgres_url

    url = ("postgres://postgres.abc:pw@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"
           "?sslmode=require&supa=base-pooler.x")
    assert clean_postgres_url(url) == (
        "postgres://postgres.abc:pw@aws-0-eu-central-1.pooler.supabase.com:6543/postgres?sslmode=require")
    assert clean_postgres_url(
        "postgresql://postgres.abc:p%40ss@aws-0-x.pooler.supabase.com:6543/postgres?pgbouncer=true"
    ) == "postgresql://postgres.abc:p%40ss@aws-0-x.pooler.supabase.com:6543/postgres?sslmode=require"
    # Non-Supabase URLs are left alone apart from unknown params
    assert clean_postgres_url("postgresql://u@localhost/db") == "postgresql://u@localhost/db"


def test_postgres_tables_have_rls(memory):
    if not hasattr(memory._db, "after_schema"):
        return  # SQLite backend
    rows = memory._query("SELECT relname FROM pg_class WHERE relrowsecurity AND relname = 'progress'")
    assert rows and rows[0]["relname"] == "progress"
    memory.save_note("rls", "owner can still write")
    assert memory.get_notes("rls")
