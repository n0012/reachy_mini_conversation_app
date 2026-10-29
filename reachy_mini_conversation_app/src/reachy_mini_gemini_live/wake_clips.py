"""Keep the audio that triggered each wake word, labelled real or false, for tuning the detector.

Clips stay on the robot (``~/.reachy_mini_gemini_live/wake_clips``), the newest ``KEEP`` are
kept, and ``index.jsonl`` records score and outcome. ``REACHY_MINI_WAKE_CLIPS=0`` turns it off.
"""

import os
import json
import wave
import logging
import threading
import collections
from pathlib import Path
from datetime import datetime, timezone

import numpy as np


logger = logging.getLogger(__name__)

KEEP = 50
SECONDS = 3.0


class WakeClipRecorder:
    """Rolling buffer of recent mic audio; saves it as a WAV when the wake word fires."""

    def __init__(self, directory: Path, keep: int = KEEP, seconds: float = SECONDS) -> None:
        """Store clips in ``directory``, keeping the newest ``keep``."""
        self.directory = directory
        self.keep = keep
        self.seconds = seconds
        self._chunks: collections.deque[np.ndarray] = collections.deque()
        self._samples = 0
        self._rate = 0
        self._lock = threading.Lock()

    def push(self, mono: np.ndarray, rate: int) -> None:
        """Append int16 mono audio, dropping what's older than ``seconds``."""
        with self._lock:
            if rate != self._rate:
                self._chunks.clear()
                self._samples = 0
                self._rate = rate
            self._chunks.append(mono)
            self._samples += len(mono)
            while self._chunks and self._samples - len(self._chunks[0]) >= self.seconds * rate:
                self._samples -= len(self._chunks.popleft())

    def save(self, score: float) -> Path | None:
        """Write the buffered audio and return its path (None if there's nothing buffered)."""
        with self._lock:
            if not self._chunks:
                return None
            audio = np.concatenate(list(self._chunks)).astype(np.int16)
            rate = self._rate
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.directory / f"{stamp}_{score:.3f}.wav"
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(audio.tobytes())
            self._append({"clip": path.name, "score": round(score, 3), "outcome": None})
            self._prune()
        except OSError as e:
            logger.warning("Wake clip not saved: %s", e)
            return None
        return path

    def label(self, path: Path | None, outcome: str) -> None:
        """Record whether the wake was "real" (someone spoke) or "false"."""
        if path is not None:
            self._append({"clip": path.name, "outcome": outcome})

    def _append(self, entry: dict) -> None:
        with open(self.directory / "index.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def _prune(self) -> None:
        clips = sorted(self.directory.glob("*.wav"))
        for old in clips[: max(0, len(clips) - self.keep)]:
            old.unlink(missing_ok=True)


def recorder_from_env(base_dir: Path) -> WakeClipRecorder | None:
    """Return a recorder in ``base_dir/wake_clips``, or None if REACHY_MINI_WAKE_CLIPS is off."""
    if os.environ.get("REACHY_MINI_WAKE_CLIPS", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    return WakeClipRecorder(base_dir / "wake_clips")
