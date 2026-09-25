import os
from types import SimpleNamespace

import pytest

from app.memory import Memory


class Block:
    """Minimal stand-in for an SDK content block."""

    def __init__(self, **data):
        self._data = data
        self.type = data["type"]

    def model_dump(self, **_):
        return dict(self._data)


class FakeStream:
    def __init__(self, texts, message):
        self._texts = texts
        self._message = message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for t in self._texts:
                yield SimpleNamespace(type="text", text=t)
        return gen()

    async def get_final_message(self):
        return self._message


class FakeClient:
    """Replays scripted responses: list of (text_deltas, content_blocks, stop_reason)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **params):
        self.calls.append(params)
        texts, blocks, stop = self.script.pop(0)
        usage = SimpleNamespace(input_tokens=10, output_tokens=5,
                                cache_read_input_tokens=100, cache_creation_input_tokens=0)
        return FakeStream(texts, SimpleNamespace(content=blocks, stop_reason=stop, usage=usage))


# Set TEST_DATABASE_URL=postgresql://... to also run the memory tests against Postgres.
PG_URL = os.environ.get("TEST_DATABASE_URL")
TABLES = "messages, progress, notes, current_lesson, english_corrections, profile"



@pytest.fixture(params=["sqlite"] + (["postgres"] if PG_URL else []))
def memory(request, tmp_path):
    if request.param == "postgres":
        import psycopg

        from app.memory import clean_postgres_url

        with psycopg.connect(clean_postgres_url(PG_URL), autocommit=True) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {TABLES}")
        return Memory(PG_URL)
    return Memory(str(tmp_path / "test.db"))
