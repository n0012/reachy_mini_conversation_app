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


def _api_seq(monkeypatch: pytest.MonkeyPatch, *outcomes: object, listed: object = None) -> list[dict]:
    """Serve ``outcomes`` in order to :retrieve/generate calls; memory list GETs get ``listed``."""
    calls: list[dict] = []
    queue = list(outcomes)

    def fake(method: str, url: str, body: dict | None = None, timeout: float = 15.0) -> dict:
        calls.append({"method": method, "url": url, "timeout": timeout})
        if method == "GET" and "/memories?" in url:
            out = listed if listed is not None else {"memories": []}
        else:
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


U1 = {"user_id": "u1", "profile": "default"}


def _at(days: float) -> str:
    from datetime import datetime, timezone, timedelta

    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")


@pytest.mark.asyncio
async def test_load_context_dates_memories_and_lists_recent_first(vertex, monkeypatch) -> None:
    old = {"fact": "Works in tech.", "name": "m/1", "createTime": _at(170), "scope": U1}
    new = {"fact": "Started learning chess.", "name": "m/2", "createTime": _at(0.5), "scope": U1}
    # Similarity search only surfaces the old fact; the new one must still come in, by date.
    _api_seq(monkeypatch, {"retrievedMemories": [{"distance": 0.1, "memory": old}]}, listed={"memories": [old, new]})

    ctx = await vertex.load_context("default", user_id="u1")

    # The relevant block keeps its deployed shape; recent conversations follow it, dated.
    assert ctx.startswith("<vertex_memory_bank>\nRelevant memories from prior sessions:\n1. Works in tech.\n")
    assert ctx.index("</vertex_memory_bank>") < ctx.index("<recent_conversations>")
    assert ctx.index("<recent_conversations>") < ctx.index("- Started learning chess. (12 hours ago)")


@pytest.mark.asyncio
async def test_load_context_caps_older_and_does_not_repeat_recent(vertex, monkeypatch) -> None:
    olds = [{"fact": f"Old fact {i}.", "name": f"m/o{i}", "createTime": _at(40 + i), "scope": U1} for i in range(25)]
    recent = [
        {"fact": "Had pancakes yesterday.", "name": "m/r1", "createTime": _at(1.2), "scope": U1},
        {"fact": "Watched a film tonight.", "name": "m/r2", "createTime": _at(0.1), "scope": U1},
    ]
    retrieved = [{"distance": 0.1, "memory": m} for m in [recent[0], *olds]]
    _api_seq(monkeypatch, {"retrievedMemories": retrieved}, listed={"memories": olds + recent})

    ctx = await vertex.load_context("default", user_id="u1")

    assert ctx.count("Had pancakes yesterday.") == 1
    assert ctx.index("Watched a film tonight.") < ctx.index("Had pancakes yesterday.")  # newest first
    assert sum(f"Old fact {i}." in ctx for i in range(25)) == memory_mod.OLDER_MAX


@pytest.mark.asyncio
async def test_load_context_includes_every_known_face_once(vertex, monkeypatch) -> None:
    mine = {"fact": "U1 has short grey hair and glasses.", "name": "m/f1", "createTime": _at(90), "scope": U1}
    theirs = {
        "fact": "Visual ID — Sam: curly red hair.",
        "name": "m/f2",
        "createTime": _at(90),
        "scope": {"user_id": "sam", "profile": "default"},
    }
    other_user_news = {
        "fact": "Sam got a new bike.",
        "name": "m/s1",
        "createTime": _at(0.2),
        "scope": {"user_id": "sam", "profile": "default"},
    }
    retrieved = {"retrievedMemories": [{"distance": 0.1, "memory": mine}]}
    _api_seq(monkeypatch, retrieved, listed={"memories": [mine, theirs, other_user_news]})

    ctx = await vertex.load_context("default", user_id="u1")

    assert "1. Visual ID — U1: U1 has short grey hair and glasses." in ctx  # labelled, not duplicated
    assert "2. Visual ID — Sam: curly red hair." in ctx
    assert ctx.count("grey hair") == 1
    assert "new bike" not in ctx  # another person's recent news isn't this user's catch-up
