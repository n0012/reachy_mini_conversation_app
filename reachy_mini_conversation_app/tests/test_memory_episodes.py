"""Each finished conversation is kept as a dated record, and recent ones lead the session context."""

import sys
import time
import types
from datetime import datetime, timezone, timedelta

import pytest

from reachy_mini_gemini_live import memory as memory_mod
from reachy_mini_gemini_live import location
from reachy_mini_gemini_live.memory import VertexMemoryBackend


TRANSCRIPT = [
    {"role": "user", "content": "The new bed finally arrives tomorrow, and I'm going skiing on Saturday."},
    {"role": "assistant", "content": "That sounds exciting, enjoy the slopes!"},
]


@pytest.fixture()
def vertex(monkeypatch: pytest.MonkeyPatch) -> VertexMemoryBackend:
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("VERTEX_REASONING_ENGINE_ID", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    return VertexMemoryBackend()


def _fake_genai(monkeypatch: pytest.MonkeyPatch, reply: str) -> list[str]:
    prompts: list[str] = []

    class Models:
        def generate_content(self, model: str, contents: str) -> object:
            prompts.append(contents)
            return types.SimpleNamespace(text=reply)

    fake = types.ModuleType("google.genai")
    fake.Client = lambda **_kw: types.SimpleNamespace(models=Models())
    monkeypatch.setitem(sys.modules, "google.genai", fake)
    monkeypatch.setattr(sys.modules["google"], "genai", fake, raising=False)
    return prompts


def _fake_api(monkeypatch: pytest.MonkeyPatch, listed: dict | None = None) -> list[dict]:
    calls: list[dict] = []

    def fake(method: str, url: str, body: dict | None = None, timeout: float = 15.0) -> dict:
        calls.append({"method": method, "url": url, "body": body})
        if method == "GET":
            return listed or {"memories": []}
        return {"retrievedMemories": []} if url.endswith(":retrieve") else {"name": "ops/1"}

    monkeypatch.setattr(memory_mod, "_api", fake)
    return calls


@pytest.mark.asyncio
async def test_finished_conversation_is_saved_verbatim_with_its_date(vertex, monkeypatch) -> None:
    evening = datetime(2026, 10, 29, 19, 30, tzinfo=timezone(timedelta(hours=-6)))
    monkeypatch.setattr(location, "local_now", lambda: evening)
    prompts = _fake_genai(monkeypatch, "Nick said the bed arrives October 30, 2026 and he skis on October 31.")
    calls = _fake_api(monkeypatch)

    assert await vertex.save_episode("default", TRANSCRIPT, user_id="nick") == "saved"

    assert "Thursday October 29, 2026" in prompts[0]
    assert "never record something because the robot said" in prompts[0]  # no self-confirming memories
    (create,) = calls
    assert create["method"] == "POST" and create["url"].endswith("/memories")  # created, not generate/consolidated
    assert create["body"]["fact"].startswith("Conversation on Thursday October 29, 2026, evening: Nick said")
    assert create["body"]["metadata"]["event_time"] == {"timestampValue": "2026-10-30T01:30:00Z"}
    assert create["body"]["metadata"]["kind"] == {"stringValue": "episode"}


@pytest.mark.asyncio
async def test_nothing_worth_keeping_is_not_saved(vertex, monkeypatch) -> None:
    _fake_genai(monkeypatch, "NONE")
    calls = _fake_api(monkeypatch)

    assert await vertex.save_episode("default", TRANSCRIPT, user_id="nick") == "skipped"
    assert await vertex.save_episode("default", TRANSCRIPT[1:], user_id="nick") == "skipped"  # robot only
    assert calls == []


@pytest.mark.asyncio
async def test_recent_conversations_lead_and_old_ones_stay_out(vertex, monkeypatch) -> None:
    def at(days: float) -> str:
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")

    scope = {"user_id": "nick", "profile": "default"}

    def episode(name: str, fact: str, days: float, saved_days: float = 0.0) -> dict:
        return {
            "name": name,
            "fact": fact,
            "scope": scope,
            "createTime": at(saved_days),  # saved later than it happened must not make it look new
            "metadata": {"event_time": {"timestampValue": at(days)}, "kind": {"stringValue": "episode"}},
        }

    listed = {
        "memories": [
            episode("m/e1", "Conversation on Monday: skiing plans.", 3.0),
            episode("m/e2", "Conversation on Wednesday: the bed arrives.", 1.0),
            episode("m/e3", "Conversation long ago: a trip.", 40.0, saved_days=0.1),
            {"name": "m/f1", "fact": "Likes tea.", "scope": scope, "createTime": at(200)},
        ]
    }
    _fake_genai(monkeypatch, "")
    _fake_api(monkeypatch, listed)
    monkeypatch.setattr(
        VertexMemoryBackend,
        "_retrieve",
        lambda self, *a, **k: _done([{"memory_id": "m/f1", "fact": "Likes tea.", "age_days": 200.0}]),
    )

    ctx = await vertex.load_context("default", user_id="nick")

    recent = ctx[ctx.index("<recent_conversations>") :]
    assert recent.index("the bed arrives. (1 day ago)") < recent.index("skiing plans. (3 days ago)")
    assert "a trip" not in ctx  # older than two weeks, by when it happened
    assert "1. Likes tea." in ctx


async def _done(value: object) -> object:
    return value


def test_ages_never_read_24_hours() -> None:
    assert memory_mod._ago(23.6 / 24) == "1 day ago"
    assert memory_mod._ago(5 / 24) == "5 hours ago"
