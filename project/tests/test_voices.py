"""Tests for voice name constants and validation helpers.

These tests cover the AVAILABLE_VOICES list and the validate_voice() helper
added in Phase 2. validate_voice() lives in voices.py (created in Phase 2);
these tests will fail until that module exists — that's intentional, they
document the contract before implementation.
"""

import pytest

import reachy_mini_conversation_app.config as config_mod


EXPECTED_VOICES = {"Aoede", "Charon", "Fenrir", "Kore", "Leda", "Orus", "Puck", "Zephyr"}


# ---------------------------------------------------------------------------
# config.AVAILABLE_VOICES (Phase 1 — passes now)
# ---------------------------------------------------------------------------


def test_available_voices_is_non_empty() -> None:
    assert len(config_mod.AVAILABLE_VOICES) > 0


def test_available_voices_are_title_case() -> None:
    """Gemini voice names are title-cased (e.g. 'Kore', not 'kore')."""
    for v in config_mod.AVAILABLE_VOICES:
        assert v == v.title(), f"Voice {v!r} is not title-cased"


def test_available_voices_contains_all_known() -> None:
    """Every known Gemini voice must be listed."""
    missing = EXPECTED_VOICES - set(config_mod.AVAILABLE_VOICES)
    assert not missing, f"Missing voices: {missing}"


def test_available_voices_has_no_openai_names() -> None:
    openai_voices = {"alloy", "ash", "ballad", "cedar", "coral", "echo", "marin", "sage", "shimmer", "verse"}
    overlap = openai_voices & {v.lower() for v in config_mod.AVAILABLE_VOICES}
    assert not overlap, f"OpenAI voice names found: {overlap}"


# ---------------------------------------------------------------------------
# voices.validate_voice() (Phase 2 — expected to fail until voices.py exists)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(reason="voices.py not yet created (Phase 2)", strict=True)
def test_validate_voice_accepts_valid() -> None:
    from reachy_mini_conversation_app.voices import validate_voice

    for v in EXPECTED_VOICES:
        assert validate_voice(v) == v


@pytest.mark.xfail(reason="voices.py not yet created (Phase 2)", strict=True)
def test_validate_voice_rejects_unknown() -> None:
    from reachy_mini_conversation_app.voices import validate_voice

    with pytest.raises(ValueError, match="Unknown voice"):
        validate_voice("cedar")  # OpenAI voice — must be rejected


@pytest.mark.xfail(reason="voices.py not yet created (Phase 2)", strict=True)
def test_validate_voice_case_insensitive_reject() -> None:
    """Case variants of unknown voices should still be rejected."""
    from reachy_mini_conversation_app.voices import validate_voice

    with pytest.raises(ValueError):
        validate_voice("CEDAR")


@pytest.mark.xfail(reason="voices.py not yet created (Phase 2)", strict=True)
def test_validate_voice_error_message_lists_valid_voices() -> None:
    """The ValueError message should tell the user what voices are valid."""
    from reachy_mini_conversation_app.voices import validate_voice

    with pytest.raises(ValueError) as exc_info:
        validate_voice("invalid_voice")
    error_message = str(exc_info.value)
    assert "Kore" in error_message  # At least one valid voice is named
