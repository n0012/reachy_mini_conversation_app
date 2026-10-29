"""Speech and input transcription are pinned to one language unless REACHY_MINI_LANGUAGE is empty."""

from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize(("env", "expect"), [(None, "en-US"), ("fr-FR", "fr-FR"), ("", None)])
def test_live_config_language(monkeypatch, env, expect) -> None:
    from reachy_mini_gemini_live import gemini_live

    if env is None:
        monkeypatch.delenv("REACHY_MINI_LANGUAGE", raising=False)
    else:
        monkeypatch.setenv("REACHY_MINI_LANGUAGE", env)
    deps = gemini_live.ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    cfg = gemini_live.GeminiLiveHandler(deps)._build_live_config()

    assert cfg.speech_config.language_code == expect
    assert cfg.input_audio_transcription.language_codes == ([expect] if expect else None)
