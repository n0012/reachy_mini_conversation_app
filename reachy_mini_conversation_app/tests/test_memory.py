"""Tests for the memory system — FileMemoryBackend and MemoryBackend protocol."""

import asyncio
from pathlib import Path

import pytest

from reachy_mini_conversation_app.memory import (
    FileMemoryBackend,
    MemoryBackend,
    VertexMemoryBackend,
    create_memory_backend,
)


# ── FileMemoryBackend ─────────────────────────────────────────────────────────


@pytest.fixture()
def mem(tmp_path: Path) -> FileMemoryBackend:
    return FileMemoryBackend(base_dir=tmp_path)


def test_file_backend_satisfies_protocol(mem: FileMemoryBackend) -> None:
    assert isinstance(mem, MemoryBackend)


@pytest.mark.asyncio
async def test_load_context_empty_returns_empty(mem: FileMemoryBackend) -> None:
    ctx = await mem.load_context("default")
    assert ctx == ""


@pytest.mark.asyncio
async def test_remember_stores_fact(mem: FileMemoryBackend, tmp_path: Path) -> None:
    await mem.remember("default", "Nick drinks black coffee.")
    memory_file = tmp_path / "unknown" / "default" / "MEMORY.md"
    assert memory_file.exists()
    assert "Nick drinks black coffee." in memory_file.read_text()


@pytest.mark.asyncio
async def test_remember_deduplicates(mem: FileMemoryBackend, tmp_path: Path) -> None:
    await mem.remember("default", "Nick drinks black coffee.")
    await mem.remember("default", "Nick drinks black coffee.")
    content = (tmp_path / "unknown" / "default" / "MEMORY.md").read_text()
    assert content.count("Nick drinks black coffee.") == 1


@pytest.mark.asyncio
async def test_load_context_returns_memory_after_remember(mem: FileMemoryBackend) -> None:
    await mem.remember("default", "Nick prefers concise answers.")
    ctx = await mem.load_context("default")
    assert "Nick prefers concise answers." in ctx


@pytest.mark.asyncio
async def test_save_session_writes_last_md(mem: FileMemoryBackend, tmp_path: Path) -> None:
    transcript = [
        {"role": "user", "content": "What is the weather?"},
        {"role": "assistant", "content": "It is sunny today."},
    ]
    await mem.save_session("default", transcript)
    last = tmp_path / "unknown" / "default" / "last.md"
    assert last.exists()
    text = last.read_text()
    assert "What is the weather?" in text
    assert "It is sunny today." in text


@pytest.mark.asyncio
async def test_save_session_empty_transcript_skipped(mem: FileMemoryBackend, tmp_path: Path) -> None:
    await mem.save_session("default", [])
    assert not (tmp_path / "unknown" / "default" / "last.md").exists()


@pytest.mark.asyncio
async def test_profiles_are_isolated(mem: FileMemoryBackend, tmp_path: Path) -> None:
    await mem.remember("profile_a", "Fact for A.")
    await mem.remember("profile_b", "Fact for B.")
    ctx_a = await mem.load_context("profile_a")
    ctx_b = await mem.load_context("profile_b")
    assert "Fact for A." in ctx_a
    assert "Fact for A." not in ctx_b
    assert "Fact for B." in ctx_b
    assert "Fact for B." not in ctx_a


@pytest.mark.asyncio
async def test_load_context_combines_memory_and_last(mem: FileMemoryBackend, tmp_path: Path) -> None:
    await mem.remember("default", "Durable fact.")
    await mem.save_session("default", [
        {"role": "user", "content": "We discussed the project."},
    ])
    ctx = await mem.load_context("default")
    assert "Durable fact." in ctx
    assert "We discussed the project." in ctx


# ── create_memory_backend ─────────────────────────────────────────────────────


def test_create_memory_backend_returns_file_backend_without_gcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("VERTEX_REASONING_ENGINE_ID", raising=False)
    backend = create_memory_backend()
    assert isinstance(backend, FileMemoryBackend)


def test_create_memory_backend_returns_vertex_when_gcp_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GCP_PROJECT_ID", "losiern-vibe")
    monkeypatch.setenv("VERTEX_REASONING_ENGINE_ID", "12345")
    monkeypatch.setenv("GCP_LOCATION", "us-central1")
    backend = create_memory_backend()
    assert isinstance(backend, VertexMemoryBackend)


# ── Multi-user isolation ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_users_are_isolated(mem: FileMemoryBackend) -> None:
    await mem.remember("default", "Likes coffee.", user_id="nick")
    await mem.remember("default", "Likes orange juice.", user_id="zach")
    nick_ctx = await mem.load_context("default", user_id="nick")
    zach_ctx = await mem.load_context("default", user_id="zach")
    assert "Likes coffee." in nick_ctx
    assert "Likes coffee." not in zach_ctx
    assert "Likes orange juice." in zach_ctx
    assert "Likes orange juice." not in nick_ctx


def test_set_and_get_active_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from reachy_mini_conversation_app import memory as mem_mod
    monkeypatch.setattr(mem_mod, "_ACTIVE_USER_FILE", tmp_path / "active_user.json")
    assert mem_mod.get_active_user() == "unknown"
    mem_mod.set_active_user("Nick")
    assert mem_mod.get_active_user() == "nick"  # normalised
