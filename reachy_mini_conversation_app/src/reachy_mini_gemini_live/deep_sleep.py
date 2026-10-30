"""Deep sleep: while asleep, stop the media pipelines and the motors so the robot idles quietly.

Asleep, most of the CPU (and fan noise) is the daemon's camera/audio pipelines and the app's
loops polling them, and the servos are powered just to hold the sleep pose. Wake-word
detection reads ALSA (``reachymini_audio_src``) directly, so it keeps working with media
released. ``REACHY_MINI_DEEP_SLEEP=0`` keeps the old light sleep.

Releasing media also stops the daemon's central signalling relay, which takes the robot off
remote apps (Hugging Face Spaces using the JS SDK) until it wakes. So media is only released
when the opt-in wake word needs the microphone (``REACHY_MINI_WAKE_WORD``); otherwise sleep
turns the motors and camera polling off and the relay stays up.
``REACHY_MINI_SLEEP_RELEASE_MEDIA=1``/``0`` overrides that choice.
"""

import os
import time
import logging
import threading


logger = logging.getLogger(__name__)

ACQUIRE_ATTEMPTS = 3


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in ("", "0", "false", "no", "off")


def _released(robot: object) -> bool:
    # The SDK keeps this private; acquire_media() logs instead of raising on failure.
    return bool(getattr(robot, "_media_released", False))


class DeepSleep:
    """Pause media consumers, disable motors and release the daemon's media while asleep."""

    def __init__(self, robot: object, camera_worker: object | None = None) -> None:
        """Hold the robot and the camera worker whose loop must stop polling while asleep."""
        self.robot = robot
        self.camera_worker = camera_worker
        self.enabled = _flag("REACHY_MINI_DEEP_SLEEP", True)
        self.release_media = _flag("REACHY_MINI_SLEEP_RELEASE_MEDIA", _flag("REACHY_MINI_WAKE_WORD", False))
        # Set while media is (or is about to be) unavailable; the console audio loops check it.
        self.media_paused = threading.Event()
        self._asleep = False
        self._lock = threading.Lock()

    def enter(self) -> None:
        """Go into deep sleep (blocking — run in an executor after the sleep pose is reached)."""
        if not self.enabled or self.robot is None:
            return
        with self._lock:
            if self._asleep:
                return
            self._asleep = True
            if self.release_media:
                self.media_paused.set()
            self._pause_camera(True)
            time.sleep(0.2)  # let the loops see the flag before the media manager goes away
            try:
                self.robot.disable_motors()  # type: ignore[attr-defined]
            except Exception as e:
                logger.warning("Deep sleep: could not disable motors: %s", e)
            if not self.release_media:
                logger.info("Deep sleep: motors off, media kept so remote apps can still connect")
                return
            try:
                self.robot.release_media()  # type: ignore[attr-defined]
            except Exception as e:
                logger.warning("Deep sleep: could not release media, staying in light sleep: %s", e)
                self._pause_camera(False)
                self.media_paused.clear()
                return
            logger.info("Deep sleep: motors off, media released")

    def exit(self) -> bool:
        """Leave deep sleep (blocking). False if media could not be re-acquired."""
        with self._lock:
            if not self._asleep:
                return True
            try:
                self.robot.enable_motors()  # type: ignore[attr-defined]
            except Exception as e:
                logger.warning("Deep sleep: could not enable motors: %s", e)
            if not self.media_paused.is_set():  # media was kept (or its release failed)
                self._pause_camera(False)
                self._asleep = False
                logger.info("Deep sleep: motors on")
                return True
            for attempt in range(1, ACQUIRE_ATTEMPTS + 1):
                try:
                    self.robot.acquire_media()  # type: ignore[attr-defined]
                except Exception as e:
                    logger.warning("Deep sleep: acquire_media attempt %d failed: %s", attempt, e)
                if not _released(self.robot):
                    break
                time.sleep(1.0)
            else:
                logger.error("Deep sleep: media still released after %d attempts", ACQUIRE_ATTEMPTS)
                return False
            try:
                self.robot.media.start_recording()  # type: ignore[attr-defined]
                self.robot.media.start_playing()  # type: ignore[attr-defined]
            except Exception as e:
                logger.error("Deep sleep: media pipelines did not restart: %s", e)
                return False
            time.sleep(1.0)  # same settle time the console gives the pipelines at startup
            self._pause_camera(False)
            self.media_paused.clear()
            self._asleep = False
            logger.info("Deep sleep: motors on, media re-acquired")
            return True

    def restore(self) -> None:
        """Hand media back to the daemon on shutdown so the next app finds it acquired."""
        if self.robot is None or not _released(self.robot):
            return
        try:
            self.robot.acquire_media()  # type: ignore[attr-defined]
        except Exception as e:
            logger.warning("Deep sleep: could not re-acquire media on shutdown: %s", e)

    def _pause_camera(self, paused: bool) -> None:
        if self.camera_worker is None:
            return
        (self.camera_worker.pause if paused else self.camera_worker.resume)()  # type: ignore[attr-defined]


def recover_released_media(robot: object) -> None:
    """At startup, re-acquire media a previous run left released (e.g. it crashed asleep)."""
    try:
        status = robot.client.get_status()  # type: ignore[attr-defined]
        released = (
            status.get("media_released") if isinstance(status, dict) else getattr(status, "media_released", False)
        )
    except Exception as e:
        logger.debug("media status unavailable: %s", e)
        return
    if not released:
        return
    logger.warning("Daemon media was left released — re-acquiring")
    robot._media_released = True  # type: ignore[attr-defined]  # so acquire_media() doesn't no-op
    try:
        robot.acquire_media()  # type: ignore[attr-defined]
    except Exception as e:
        logger.error("Could not re-acquire media: %s", e)
