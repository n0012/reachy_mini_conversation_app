"""Idle nudges are opt-in: by default a quiet room gets no prompt to say something."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(("env", "expect_nudge"), [(None, False), ("1", True), ("false", False)])
async def test_idle_nudge_only_when_enabled(monkeypatch, env, expect_nudge) -> None:
    from reachy_mini_gemini_live import gemini_live

    if env is None:
        monkeypatch.delenv("REACHY_MINI_IDLE_NUDGES", raising=False)
    else:
        monkeypatch.setenv("REACHY_MINI_IDLE_NUDGES", env)
    movement_manager = MagicMock()
    movement_manager.is_idle.return_value = True
    deps = gemini_live.ToolDependencies(reachy_mini=MagicMock(), movement_manager=movement_manager)
    handler = gemini_live.GeminiLiveHandler(deps)
    handler.session = MagicMock(send_realtime_input=AsyncMock())
    handler.last_activity_time -= 60  # a minute of silence

    await handler.emit()

    assert handler.session.send_realtime_input.await_count == (1 if expect_nudge else 0)
