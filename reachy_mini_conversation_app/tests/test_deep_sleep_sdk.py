"""Deep-sleep pauses in modules that import the full SDK (run on the robot; ignored in the dev venv)."""

import time
import asyncio
import threading
from unittest.mock import MagicMock

import pytest

from reachy_mini_gemini_live import deep_sleep as ds


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REACHY_MINI_DEEP_SLEEP", raising=False)


@pytest.mark.asyncio
async def test_record_loop_stops_polling_media_while_paused() -> None:
    from reachy_mini_gemini_live.console import LocalStream

    sleep = ds.DeepSleep(MagicMock())
    sleep.media_paused.set()
    stream = LocalStream.__new__(LocalStream)
    stream.handler = MagicMock(deps=MagicMock(deep_sleep=sleep))
    stream._robot = MagicMock()
    stream._stop_event = asyncio.Event()

    task = asyncio.create_task(stream.record_loop())
    await asyncio.sleep(0.25)
    stream._stop_event.set()
    await task

    stream._robot.media.get_audio_sample.assert_not_called()


def test_camera_worker_does_not_read_frames_while_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    from reachy_mini_gemini_live.camera_worker import CameraWorker

    robot = MagicMock()
    worker = CameraWorker(robot, None)
    worker.pause()
    stop = worker._stop_event
    sleeps = []

    def fake_sleep(s: float) -> None:
        sleeps.append(s)
        if len(sleeps) >= 3:
            stop.set()

    monkeypatch.setattr(time, "sleep", fake_sleep)
    thread = threading.Thread(target=worker.working_loop)
    thread.start()
    thread.join(timeout=2)

    robot.media.get_frame.assert_not_called()


def test_movement_loop_ticks_slowly_while_output_paused() -> None:
    from reachy_mini_gemini_live import moves

    manager = moves.MovementManager(current_robot=MagicMock())
    stats = manager._freq_stats
    fast, _ = manager._schedule_next_tick(manager._now(), stats)
    manager._output_paused.set()
    slow, _ = manager._schedule_next_tick(manager._now(), stats)

    assert fast <= manager.target_period < slow <= moves.PAUSED_PERIOD_S
