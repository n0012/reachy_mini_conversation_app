"""Deep sleep pauses media consumers, turns motors off and releases media; wake undoes it in order."""

import time
from unittest.mock import MagicMock

import pytest

from reachy_mini_gemini_live import deep_sleep as ds


class FakeRobot:
    def __init__(self, acquire_works: bool = True, release_works: bool = True) -> None:
        self.calls: list[str] = []
        self._media_released = False
        self.acquire_works = acquire_works
        self.release_works = release_works
        self.media = MagicMock()
        self.media.start_recording.side_effect = lambda: self.calls.append("start_recording")
        self.media.start_playing.side_effect = lambda: self.calls.append("start_playing")

    def disable_motors(self) -> None:
        self.calls.append("disable_motors")

    def enable_motors(self) -> None:
        self.calls.append("enable_motors")

    def release_media(self) -> None:
        self.calls.append("release_media")
        if not self.release_works:
            raise RuntimeError("daemon said no")
        self._media_released = True

    def acquire_media(self) -> None:
        self.calls.append("acquire_media")
        if self.acquire_works:
            self._media_released = False


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    monkeypatch.delenv("REACHY_MINI_DEEP_SLEEP", raising=False)


def test_enter_then_exit_round_trip() -> None:
    robot, camera = FakeRobot(), MagicMock()
    sleep = ds.DeepSleep(robot, camera)

    sleep.enter()
    assert sleep.media_paused.is_set()
    camera.pause.assert_called_once()
    assert robot.calls == ["disable_motors", "release_media"]

    robot.calls.clear()
    assert sleep.exit() is True
    assert robot.calls == ["enable_motors", "acquire_media", "start_recording", "start_playing"]
    assert not sleep.media_paused.is_set()
    camera.resume.assert_called_once()


def test_exit_reports_failure_when_media_stays_released() -> None:
    robot = FakeRobot(acquire_works=False)
    sleep = ds.DeepSleep(robot)
    sleep.enter()

    assert sleep.exit() is False
    assert robot.calls.count("acquire_media") == ds.ACQUIRE_ATTEMPTS
    assert sleep.media_paused.is_set()  # media consumers stay off; the app restarts instead


def test_release_failure_falls_back_to_light_sleep() -> None:
    robot, camera = FakeRobot(release_works=False), MagicMock()
    sleep = ds.DeepSleep(robot, camera)

    sleep.enter()

    assert not sleep.media_paused.is_set()
    camera.resume.assert_called_once()


def test_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REACHY_MINI_DEEP_SLEEP", "0")
    robot = FakeRobot()
    sleep = ds.DeepSleep(robot)

    sleep.enter()

    assert robot.calls == [] and not sleep.media_paused.is_set()
    assert sleep.exit() is True


def test_restore_and_startup_recovery() -> None:
    robot = FakeRobot()
    sleep = ds.DeepSleep(robot)
    sleep.enter()
    sleep.restore()
    assert robot.calls[-1] == "acquire_media" and not robot._media_released

    fresh = FakeRobot()
    fresh.client = MagicMock(get_status=MagicMock(return_value={"media_released": True}))
    ds.recover_released_media(fresh)
    assert fresh.calls == ["acquire_media"]


@pytest.mark.asyncio
async def test_wake_requests_restart_when_media_does_not_come_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from reachy_mini_gemini_live import gemini_live

    robot = FakeRobot(acquire_works=False)
    sleep = ds.DeepSleep(robot)
    sleep.enter()
    deps = gemini_live.ToolDependencies(reachy_mini=robot, movement_manager=MagicMock(), deep_sleep=sleep)
    handler = gemini_live.GeminiLiveHandler(deps)
    handler._sleeping = True
    restarts = []
    monkeypatch.setattr(gemini_live, "_request_app_restart", lambda: restarts.append(1))

    await handler.wake()

    assert restarts == [1]
    deps.movement_manager.resume_output.assert_not_called()
