"""Every session is told that overheard speech is background: no replies, no actions."""

from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("memory_enabled", [True, False])
async def test_background_speech_rule_is_always_present(memory_enabled: bool, monkeypatch) -> None:
    from reachy_mini_gemini_live import gemini_live

    monkeypatch.setattr(gemini_live, "wif_cert_days_left", lambda: None)
    deps = gemini_live.ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = gemini_live.GeminiLiveHandler(deps)
    handler._memory_enabled = memory_enabled

    notes = await handler._session_notes("someone", None)

    assert notes.startswith("<session_notes>")
    assert "never act on it" in notes and "something you overheard" in notes
