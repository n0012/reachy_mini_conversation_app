"""Turn detection waits through short mid-sentence pauses instead of answering fragments."""

from unittest.mock import MagicMock

from google.genai import types


def _cfg():
    from reachy_mini_gemini_live import gemini_live

    deps = gemini_live.ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    return gemini_live.GeminiLiveHandler(deps)._build_live_config()


def test_vad_defaults_tolerate_pauses(monkeypatch) -> None:
    monkeypatch.delenv("REACHY_MINI_VAD_SILENCE_MS", raising=False)
    monkeypatch.delenv("REACHY_MINI_VAD_END_SENSITIVITY", raising=False)
    vad = _cfg().realtime_input_config.automatic_activity_detection

    assert vad.end_of_speech_sensitivity == types.EndSensitivity.END_SENSITIVITY_LOW
    assert vad.silence_duration_ms == 800
    assert not vad.disabled


def test_vad_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("REACHY_MINI_VAD_SILENCE_MS", "1200")
    monkeypatch.setenv("REACHY_MINI_VAD_END_SENSITIVITY", "high")
    vad = _cfg().realtime_input_config.automatic_activity_detection

    assert vad.end_of_speech_sensitivity == types.EndSensitivity.END_SENSITIVITY_HIGH
    assert vad.silence_duration_ms == 1200


def test_vad_bad_silence_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("REACHY_MINI_VAD_SILENCE_MS", "soon")
    assert _cfg().realtime_input_config.automatic_activity_detection.silence_duration_ms == 800
