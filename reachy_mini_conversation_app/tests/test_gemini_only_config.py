"""Tests verifying the fork is Gemini-only — no OpenAI code paths remain."""

import importlib

import pytest

import reachy_mini_gemini_live.config as config_mod


# ---------------------------------------------------------------------------
# is_gemini_model() — always True
# ---------------------------------------------------------------------------


def test_is_gemini_model_always_true() -> None:
    """is_gemini_model() must always return True in the Gemini-only fork."""
    assert config_mod.is_gemini_model() is True


def test_is_gemini_model_ignores_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """is_gemini_model() returns True regardless of what MODEL_NAME env is set to."""
    monkeypatch.setenv("MODEL_NAME", "gpt-realtime")
    assert config_mod.is_gemini_model() is True


# ---------------------------------------------------------------------------
# No OpenAI artefacts in the module
# ---------------------------------------------------------------------------


def test_no_openai_api_key_in_config() -> None:
    """Config class must not expose OPENAI_API_KEY."""
    assert not hasattr(config_mod.config, "OPENAI_API_KEY")


def test_no_openai_module() -> None:
    """openai_realtime module must not be importable from the package."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("reachy_mini_gemini_live.openai_realtime")


# ---------------------------------------------------------------------------
# Default model resolves to Gemini Live
# ---------------------------------------------------------------------------


def test_default_model_is_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    """When MODEL_NAME is unset the default is the Gemini Live preview model."""
    monkeypatch.delenv("MODEL_NAME", raising=False)
    assert config_mod._resolve_model_name(None).startswith("gemini")


def test_model_name_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """A custom MODEL_NAME env var is passed through by _resolve_model_name."""
    monkeypatch.setenv("MODEL_NAME", "gemini-2.0-flash-live-preview")
    assert config_mod._resolve_model_name("gemini-2.0-flash-live-preview") == "gemini-2.0-flash-live-preview"


# ---------------------------------------------------------------------------
# Available voices — Gemini only
# ---------------------------------------------------------------------------


def test_available_voices_are_gemini() -> None:
    """AVAILABLE_VOICES must contain only known Gemini voice names."""
    known_gemini_voices = {"Aoede", "Charon", "Fenrir", "Kore", "Leda", "Orus", "Puck", "Zephyr"}
    for voice in config_mod.AVAILABLE_VOICES:
        assert voice in known_gemini_voices, f"Unexpected voice {voice!r} in AVAILABLE_VOICES"


def test_get_available_voices_for_backend_returns_gemini_voices() -> None:
    """get_available_voices_for_backend() ignores the backend arg and returns Gemini voices."""
    voices = config_mod.get_available_voices_for_backend()
    assert "Kore" in voices
    assert "cedar" not in voices  # OpenAI voice must not appear


def test_get_default_voice_is_gemini() -> None:
    """Default voice must be a valid Gemini voice name."""
    default = config_mod.get_default_voice_for_backend()
    assert default in config_mod.AVAILABLE_VOICES


def test_get_default_voice_ignores_backend_arg() -> None:
    """get_default_voice_for_backend() returns the same value regardless of backend arg."""
    assert config_mod.get_default_voice_for_backend("openai") == config_mod.get_default_voice_for_backend("gemini")


# ---------------------------------------------------------------------------
# refresh_runtime_config_from_env
# ---------------------------------------------------------------------------


def test_refresh_does_not_set_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """refresh_runtime_config_from_env must not touch any OPENAI_API_KEY attribute."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    config_mod.refresh_runtime_config_from_env()
    assert not hasattr(config_mod.config, "OPENAI_API_KEY")


def test_refresh_updates_gemini_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """refresh_runtime_config_from_env picks up a new GEMINI_API_KEY from the environment."""
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    config_mod.refresh_runtime_config_from_env()
    assert config_mod.config.GEMINI_API_KEY == "AIza-test"
