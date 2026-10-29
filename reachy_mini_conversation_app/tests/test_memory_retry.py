"""Memory Bank reads survive a blip; generate is never retried (it would store memories twice)."""

import time
import urllib.error

import pytest

from reachy_mini_gemini_live import memory as memory_mod
from reachy_mini_gemini_live.memory import VertexMemoryBackend


FACT = {"retrievedMemories": [{"distance": 0.1, "memory": {"fact": "Likes tea.", "name": "m/1"}}]}


@pytest.fixture()
def vertex(monkeypatch: pytest.MonkeyPatch) -> VertexMemoryBackend:
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("VERTEX_REASONING_ENGINE_ID", "1")
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    return VertexMemoryBackend()


def _api_seq(monkeypatch: pytest.MonkeyPatch, *outcomes: object) -> list[dict]:
    calls: list[dict] = []
    queue = list(outcomes)

    def fake(method: str, url: str, body: dict | None = None, timeout: float = 15.0) -> dict:
        calls.append({"method": method, "url": url, "timeout": timeout})
        out = queue.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(memory_mod, "_api", fake)
    return calls


@pytest.mark.asyncio
async def test_retrieve_retries_once_on_timeout(vertex, monkeypatch) -> None:
    calls = _api_seq(monkeypatch, TimeoutError("timed out"), FACT)

    assert await vertex.search("default", "tea", user_id="u1") == ["Likes tea."]
    assert len(calls) == 2
    assert vertex.last_error is None


@pytest.mark.asyncio
async def test_retrieve_does_not_retry_auth_errors(vertex, monkeypatch) -> None:
    calls = _api_seq(monkeypatch, urllib.error.HTTPError("u", 403, "denied", None, None), FACT)

    assert await vertex.search("default", "tea", user_id="u1") == []
    assert len(calls) == 1
    assert vertex.last_error is not None


@pytest.mark.asyncio
async def test_generate_is_not_retried_and_waits_longer(vertex, monkeypatch) -> None:
    calls = _api_seq(monkeypatch, TimeoutError("timed out"), {"name": "ops/1"})

    assert await vertex.remember("default", "Likes tea.", user_id="u1") is False
    assert [c["timeout"] for c in calls] == [memory_mod.GENERATE_TIMEOUT_S]


@pytest.mark.asyncio
async def test_load_context_falls_back_to_last_good_context(vertex, monkeypatch) -> None:
    _api_seq(monkeypatch, FACT, TimeoutError("t1"), TimeoutError("t2"))
    good = await vertex.load_context("default", user_id="u1")
    assert "Likes tea." in good

    assert await vertex.load_context("default", user_id="u1") == good
    assert vertex.last_error is None  # served from cache: no "memory is down" announcement

    _api_seq(monkeypatch, TimeoutError("t1"), TimeoutError("t2"))
    assert await vertex.load_context("default", user_id="someone-else") == ""
    assert vertex.last_error == "t2"
