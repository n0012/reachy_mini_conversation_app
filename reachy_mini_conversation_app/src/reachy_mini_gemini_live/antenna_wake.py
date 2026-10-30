"""Wake by pushing an antenna: the robot's physical "button".

Ported from the daemon's idle-antenna launcher (pollen-robotics/reachy_mini,
``daemon/app/startup_app.py``, #1244). The daemon only watches while no app runs, and
this app keeps running asleep, so we watch ourselves. Deep sleep turns the motors off,
which leaves the antennas free to push while their positions still read.
"""

import asyncio
import logging
from typing import Any, Callable, Awaitable
from dataclasses import dataclass


logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 0.1
# Let the antennas settle after the sleep pose and motor shutdown before arming.
SETTLE_S = 1.5


@dataclass
class AntennaTouchDetector:
    """Hysteresis detector for antenna pushes relative to the resting pose."""

    press_delta_rad: float = 0.25  # ~14°
    release_delta_rad: float = 0.10  # ~6°
    _reference: tuple[float, float] | None = None
    _armed: bool = False

    def reset(self) -> None:
        """Forget the resting pose; the next reading becomes the new reference."""
        self._reference = None
        self._armed = False

    def update(self, present: tuple[float, float]) -> bool:
        """Return True once when either antenna is pushed away from the reference."""
        if self._reference is None:
            self._reference = present
            self._armed = True
            return False
        delta = max(abs(p - r) for p, r in zip(present, self._reference))
        if not self._armed:
            if delta <= self.release_delta_rad:
                self._armed = True
            return False
        if delta >= self.press_delta_rad:
            self._armed = False
            return True
        return False


def _read_pair(robot: Any) -> tuple[float, float] | None:
    try:
        values = robot.get_present_antenna_joint_positions()
        return (float(values[0]), float(values[1]))
    except Exception as e:
        logger.debug("antenna read failed: %s", e)
        return None


async def watch(
    robot: Any,
    still_sleeping: Callable[[], bool],
    on_touch: Callable[[], Awaitable[None]],
    *,
    detector: AntennaTouchDetector | None = None,
    poll_s: float = POLL_INTERVAL_S,
    settle_s: float = SETTLE_S,
) -> bool:
    """Poll the antennas while asleep; call ``on_touch`` once on a push. True if it fired."""
    detector = detector or AntennaTouchDetector()
    loop = asyncio.get_running_loop()
    await asyncio.sleep(settle_s)
    logger.info("Antenna wake armed — push an antenna to wake")
    while still_sleeping():
        present = await loop.run_in_executor(None, _read_pair, robot)
        if present is None:
            detector.reset()
        elif detector.update(present):
            logger.info("Antenna pushed — waking up")
            await on_touch()
            return True
        await asyncio.sleep(poll_s)
    return False
