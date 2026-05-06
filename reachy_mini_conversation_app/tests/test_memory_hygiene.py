"""Tests for memory hygiene primitives: recency boost, ttl, purge, search-with-ids."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from reachy_mini_conversation_app.memory import (
    FileMemoryBackend,
    RECENCY_ALPHA,
    RECENCY_TAU_DAYS,
    VertexMemoryBackend,
)


# ── _rerank_with_recency ─────────────────────────────────────────────────────


def _mem(fact: str, distance: float, age_days: float, name: str = "memories/x") -> dict:
    """Build a fake retrieved-memory dict mimicking the Vertex API response shape."""
    create_time = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat().replace("+00:00", "Z")
    return {
        "memory": {"name": name, "fact": fact, "createTime": create_time},
        "distance": distance,
    }


def test_rerank_recent_beats_old_when_similarity_close():
    items = [
        _mem("OLD", distance=0.05, age_days=180),     # sim 0.95
        _mem("RECENT", distance=0.15, age_days=1),    # sim 0.85, very recent
    ]
    ranked = VertexMemoryBackend._rerank_with_recency(items)
    assert ranked[0]["memory"]["fact"] == "RECENT", "Recent fact should outrank old when similarity is close"


def test_rerank_old_wins_when_recent_is_far_off_topic():
    items = [
        _mem("OLD_RELEVANT", distance=0.10, age_days=365),  # sim 0.90
        _mem("RECENT_NOISE", distance=0.90, age_days=1),    # sim 0.10 — almost off-topic
    ]
    ranked = VertexMemoryBackend._rerank_with_recency(items)
    assert ranked[0]["memory"]["fact"] == "OLD_RELEVANT"


def test_rerank_handles_missing_timestamp():
    """Memories without createTime should be treated as old (1 year), not crash."""
    items = [
        {"memory": {"fact": "NO_TS"}, "distance": 0.10},
        _mem("HAS_TS_RECENT", distance=0.20, age_days=2),
    ]
    ranked = VertexMemoryBackend._rerank_with_recency(items)
    # Either order is fine — what matters is no exception
    assert len(ranked) == 2


def test_recency_constants_sane():
    assert 0.0 < RECENCY_ALPHA <= 1.0
    assert RECENCY_TAU_DAYS > 0


# ── purge_expired ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_purge_expired_deletes_only_past_ttl():
    """purge_expired should delete memories with ttl_until in the past, leave others."""
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

    mock_items = [
        {"name": "memories/expired1", "revisionLabels": {"ttl_until": yesterday}},
        {"name": "memories/expired2", "revisionLabels": {"ttl_until": yesterday}},
        {"name": "memories/future", "revisionLabels": {"ttl_until": tomorrow}},
        {"name": "memories/permanent", "revisionLabels": {"source": "session"}},  # no ttl
    ]

    with patch.object(VertexMemoryBackend, "__init__", lambda self: None):
        backend = VertexMemoryBackend()
        backend.list_memories = AsyncMock(return_value=mock_items)
        backend.delete_memory = AsyncMock(return_value=True)

        deleted = await backend.purge_expired("yuki", user_id="nick")

        assert deleted == 2
        deleted_names = [c.args[0] for c in backend.delete_memory.call_args_list]
        assert "memories/expired1" in deleted_names
        assert "memories/expired2" in deleted_names
        assert "memories/future" not in deleted_names
        assert "memories/permanent" not in deleted_names


@pytest.mark.asyncio
async def test_purge_expired_returns_zero_when_no_memories():
    with patch.object(VertexMemoryBackend, "__init__", lambda self: None):
        backend = VertexMemoryBackend()
        backend.list_memories = AsyncMock(return_value=[])
        backend.delete_memory = AsyncMock(return_value=True)

        deleted = await backend.purge_expired("yuki", user_id="nick")

        assert deleted == 0
        backend.delete_memory.assert_not_awaited()


# ── FileMemoryBackend new methods ────────────────────────────────────────────


@pytest.fixture()
def file_backend(tmp_path) -> FileMemoryBackend:
    return FileMemoryBackend(base_dir=tmp_path)


@pytest.mark.asyncio
async def test_file_search_substring_match(file_backend, tmp_path):
    await file_backend.remember("default", "Nick lives in Denver.", user_id="nick")
    await file_backend.remember("default", "Nick prefers black coffee.", user_id="nick")
    await file_backend.remember("default", "Sarah is Nick's sister.", user_id="nick")

    hits = await file_backend.search("default", "denver", user_id="nick")
    assert len(hits) == 1
    assert "Denver" in hits[0]


@pytest.mark.asyncio
async def test_file_search_with_ids_returns_dict_shape(file_backend):
    await file_backend.remember("default", "Nick drinks black coffee.", user_id="nick")
    matches = await file_backend.search("default", "coffee", user_id="nick", with_ids=True)
    assert len(matches) == 1
    assert "memory_id" in matches[0]
    assert "fact" in matches[0]
    assert "age_days" in matches[0]
    assert matches[0]["memory_id"].startswith("file://")


@pytest.mark.asyncio
async def test_file_remember_with_lifetime_writes_ttl_marker(file_backend, tmp_path):
    await file_backend.remember("default", "User is at the airport.", user_id="nick", lifetime_days=1)
    content = (tmp_path / "nick" / "default" / "MEMORY.md").read_text()
    assert "ttl_until=" in content


@pytest.mark.asyncio
async def test_file_purge_expired_is_noop(file_backend):
    """File backend doesn't implement real purge — it returns 0."""
    deleted = await file_backend.purge_expired("default", user_id="nick")
    assert deleted == 0


@pytest.mark.asyncio
async def test_file_delete_memory_is_noop(file_backend):
    ok = await file_backend.delete_memory("file://wherever#0")
    assert ok is False
