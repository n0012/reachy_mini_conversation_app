"""Tests for the recall, forget, and remember tools."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from reachy_mini_conversation_app.tools.recall import RecallTool
from reachy_mini_conversation_app.tools.forget import ForgetTool
from reachy_mini_conversation_app.tools.remember import RememberTool


def _deps(memory=None, memory_enabled=True, profile="yuki", user="nick"):
    """Build a ToolDependencies-like SimpleNamespace for tool tests."""
    return SimpleNamespace(
        memory_backend=memory,
        memory_enabled=memory_enabled,
        active_profile=profile,
        active_user=user,
    )


# ── RecallTool ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recall_returns_matches_with_memory_ids():
    memory = SimpleNamespace(search=AsyncMock(return_value=[
        {"memory_id": "memories/abc", "fact": "Nick lives in Denver.", "age_days": 30.5},
        {"memory_id": "memories/xyz", "fact": "Nick drinks black coffee.", "age_days": 2.0},
    ]))
    out = await RecallTool()(_deps(memory=memory), query="coffee")
    assert out["status"] == "ok"
    assert out["count"] == 2
    assert out["matches"][0]["memory_id"] == "memories/abc"
    memory.search.assert_awaited_once_with("yuki", "coffee", user_id="nick", top_k=10, with_ids=True)


@pytest.mark.asyncio
async def test_recall_no_matches():
    memory = SimpleNamespace(search=AsyncMock(return_value=[]))
    out = await RecallTool()(_deps(memory=memory), query="moon landing")
    assert out["status"] == "no_matches"
    assert out["matches"] == []


@pytest.mark.asyncio
async def test_recall_empty_query_errors():
    memory = SimpleNamespace(search=AsyncMock(return_value=[]))
    out = await RecallTool()(_deps(memory=memory), query="")
    assert out["status"] == "error"
    memory.search.assert_not_awaited()


@pytest.mark.asyncio
async def test_recall_skipped_when_memory_disabled():
    memory = SimpleNamespace(search=AsyncMock())
    out = await RecallTool()(_deps(memory=memory, memory_enabled=False), query="anything")
    assert out["status"] == "skipped"
    memory.search.assert_not_awaited()


@pytest.mark.asyncio
async def test_recall_handles_backend_error():
    memory = SimpleNamespace(search=AsyncMock(side_effect=RuntimeError("API down")))
    out = await RecallTool()(_deps(memory=memory), query="anything")
    assert out["status"] == "error"
    assert "API down" in out["message"]


# ── ForgetTool ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forget_deletes_each_memory_id():
    memory = SimpleNamespace(delete_memory=AsyncMock(return_value=True))
    out = await ForgetTool()(
        _deps(memory=memory),
        memory_ids=["memories/a", "memories/b", "memories/c"],
        reason="user moved to Boston",
    )
    assert out["status"] == "ok"
    assert out["deleted_count"] == 3
    assert out["failed_count"] == 0
    assert memory.delete_memory.await_count == 3
    deleted_args = [c.args[0] for c in memory.delete_memory.call_args_list]
    assert deleted_args == ["memories/a", "memories/b", "memories/c"]


@pytest.mark.asyncio
async def test_forget_partial_failure_counted():
    memory = SimpleNamespace(delete_memory=AsyncMock(side_effect=[True, False, True]))
    out = await ForgetTool()(
        _deps(memory=memory),
        memory_ids=["memories/ok1", "memories/bad", "memories/ok2"],
    )
    assert out["deleted_count"] == 2
    assert out["failed_count"] == 1


@pytest.mark.asyncio
async def test_forget_empty_ids_errors():
    memory = SimpleNamespace(delete_memory=AsyncMock())
    out = await ForgetTool()(_deps(memory=memory), memory_ids=[])
    assert out["status"] == "error"
    memory.delete_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_forget_too_many_refused():
    memory = SimpleNamespace(delete_memory=AsyncMock())
    out = await ForgetTool()(
        _deps(memory=memory),
        memory_ids=[f"memories/{i}" for i in range(25)],
    )
    assert out["status"] == "error"
    assert "20" in out["message"]
    memory.delete_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_forget_skipped_when_memory_disabled():
    memory = SimpleNamespace(delete_memory=AsyncMock())
    out = await ForgetTool()(
        _deps(memory=memory, memory_enabled=False),
        memory_ids=["memories/a"],
    )
    assert out["status"] == "skipped"
    memory.delete_memory.assert_not_awaited()


# ── RememberTool ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_remember_stores_permanent_by_default():
    memory = SimpleNamespace(remember=AsyncMock())
    out = await RememberTool()(_deps(memory=memory), fact="Nick prefers black coffee.")
    assert out["status"] == "remembered"
    assert out["lifetime_days"] is None
    memory.remember.assert_awaited_once_with(
        "yuki", "Nick prefers black coffee.", user_id="nick", lifetime_days=None,
    )


@pytest.mark.asyncio
async def test_remember_passes_lifetime_days_through():
    memory = SimpleNamespace(remember=AsyncMock())
    out = await RememberTool()(_deps(memory=memory), fact="At airport", lifetime_days=1)
    assert out["status"] == "remembered"
    assert out["lifetime_days"] == 1
    memory.remember.assert_awaited_once_with(
        "yuki", "At airport", user_id="nick", lifetime_days=1,
    )


@pytest.mark.asyncio
async def test_remember_invalid_lifetime_treated_as_none():
    memory = SimpleNamespace(remember=AsyncMock())
    out = await RememberTool()(_deps(memory=memory), fact="x", lifetime_days="not-a-number")
    assert out["lifetime_days"] is None


@pytest.mark.asyncio
async def test_remember_empty_fact_errors():
    memory = SimpleNamespace(remember=AsyncMock())
    out = await RememberTool()(_deps(memory=memory), fact="   ")
    assert out["status"] == "error"
    memory.remember.assert_not_awaited()


@pytest.mark.asyncio
async def test_remember_skipped_when_memory_disabled():
    memory = SimpleNamespace(remember=AsyncMock())
    out = await RememberTool()(_deps(memory=memory, memory_enabled=False), fact="x")
    assert out["status"] == "skipped"
    memory.remember.assert_not_awaited()
