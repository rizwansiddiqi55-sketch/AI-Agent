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


@pytest.fixture
def memory(tmp_path):
    return Memory(str(tmp_path / "test.db"))
