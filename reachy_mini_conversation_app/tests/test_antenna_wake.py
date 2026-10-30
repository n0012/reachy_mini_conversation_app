"""Pushing an antenna wakes the robot; the wake word only listens when opted in."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from reachy_mini_gemini_live.antenna_wake import AntennaTouchDetector, watch


def test_detector_fires_once_per_push_and_rearms_on_release() -> None:
    d = AntennaTouchDetector()
    assert not d.update((-3.05, 3.05))  # first reading is the resting pose
    assert not d.update((-3.00, 3.10))  # jitter and small nudges are ignored
    assert d.update((-2.70, 3.05))  # left antenna pushed ~20°
    assert not d.update((-2.60, 3.05))  # held: no repeat
    assert not d.update((-3.02, 3.05))  # released near rest: re-armed
    assert d.update((-3.05, 3.40))  # right antenna pushed


@pytest.mark.asyncio
async def test_watch_wakes_on_push_and_tolerates_read_errors() -> None:
    robot = MagicMock()
    robot.get_present_antenna_joint_positions.side_effect = [
        [-3.05, 3.05],
        RuntimeError("daemon busy"),
        [-3.05, 3.05],
        [-3.04, 3.06],
        [-2.60, 3.05],
    ]
    on_touch = AsyncMock()

    fired = await watch(robot, lambda: True, on_touch, poll_s=0, settle_s=0)

    assert fired
    on_touch.assert_awaited_once()


@pytest.mark.asyncio
async def test_watch_stops_when_woken_another_way() -> None:
    robot = MagicMock()
    robot.get_present_antenna_joint_positions.return_value = [-3.05, 3.05]
    awake_after = iter([True, True, False])
    on_touch = AsyncMock()

    assert not await watch(robot, lambda: next(awake_after), on_touch, poll_s=0, settle_s=0)
    on_touch.assert_not_awaited()


def _handler(monkeypatch):
    from reachy_mini_gemini_live import gemini_live
    from reachy_mini_gemini_live.tools.core_tools import ToolDependencies

    handler = gemini_live.GeminiLiveHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler._memory_enabled = False
    monkeypatch.setattr(handler, "_start_ei_detection", MagicMock())
    monkeypatch.setattr(handler, "_ei_watchdog", AsyncMock())
    real_sleep = asyncio.sleep
    monkeypatch.setattr(gemini_live.asyncio, "sleep", lambda *_a, **_k: real_sleep(0))
    return handler


@pytest.mark.asyncio
async def test_sleep_arms_antenna_not_wake_word_by_default(monkeypatch) -> None:
    monkeypatch.delenv("REACHY_MINI_WAKE_WORD", raising=False)
    monkeypatch.delenv("REACHY_MINI_ANTENNA_WAKE", raising=False)
    handler = _handler(monkeypatch)

    await handler.sleep()

    handler._start_ei_detection.assert_not_called()
    assert handler._antenna_task is not None
    await handler.wake()
    assert handler._antenna_task is None  # waking cancels the watcher


@pytest.mark.asyncio
async def test_wake_word_is_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("REACHY_MINI_WAKE_WORD", "1")
    monkeypatch.setenv("REACHY_MINI_ANTENNA_WAKE", "0")
    handler = _handler(monkeypatch)

    await handler.sleep()

    handler._start_ei_detection.assert_called_once()
    assert handler._antenna_task is None
