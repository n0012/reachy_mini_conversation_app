"""Tests for Google Search grounding flag resolution."""

from pathlib import Path

import pytest

import reachy_mini_conversation_app.prompts as prompts_mod
from reachy_mini_conversation_app.prompts import get_session_grounding


@pytest.fixture(autouse=True)
def clear_grounding_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_SEARCH_GROUNDING", raising=False)
    monkeypatch.setattr(prompts_mod.config, "REACHY_MINI_CUSTOM_PROFILE", None)


# ── Global env var ────────────────────────────────────────────────────────────


def test_grounding_off_by_default() -> None:
    assert get_session_grounding() is False


@pytest.mark.parametrize("val", ["1", "true", "True", "yes"])
def test_grounding_on_via_env(monkeypatch: pytest.MonkeyPatch, val: str) -> None:
    monkeypatch.setenv("GOOGLE_SEARCH_GROUNDING", val)
    assert get_session_grounding() is True


@pytest.mark.parametrize("val", ["0", "false", "no", ""])
def test_grounding_off_via_env(monkeypatch: pytest.MonkeyPatch, val: str) -> None:
    monkeypatch.setenv("GOOGLE_SEARCH_GROUNDING", val)
    assert get_session_grounding() is False


# ── Per-profile grounding.txt ─────────────────────────────────────────────────


@pytest.mark.parametrize("content", ["true", "1", "yes", "google_search=true", "True"])
def test_grounding_on_via_profile_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    profile_dir = tmp_path / "test_profile"
    profile_dir.mkdir()
    (profile_dir / "grounding.txt").write_text(content)
    monkeypatch.setattr(prompts_mod.config, "REACHY_MINI_CUSTOM_PROFILE", "test_profile")
    monkeypatch.setattr(prompts_mod.config, "PROFILES_DIRECTORY", tmp_path)
    assert get_session_grounding() is True


def test_grounding_off_when_profile_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "test_profile").mkdir()
    monkeypatch.setattr(prompts_mod.config, "REACHY_MINI_CUSTOM_PROFILE", "test_profile")
    monkeypatch.setattr(prompts_mod.config, "PROFILES_DIRECTORY", tmp_path)
    assert get_session_grounding() is False


def test_profile_grounding_overrides_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-profile file takes priority — profile says false, env says true."""
    profile_dir = tmp_path / "test_profile"
    profile_dir.mkdir()
    (profile_dir / "grounding.txt").write_text("false")
    monkeypatch.setenv("GOOGLE_SEARCH_GROUNDING", "1")
    monkeypatch.setattr(prompts_mod.config, "REACHY_MINI_CUSTOM_PROFILE", "test_profile")
    monkeypatch.setattr(prompts_mod.config, "PROFILES_DIRECTORY", tmp_path)
    assert get_session_grounding() is False
