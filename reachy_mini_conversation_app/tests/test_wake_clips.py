"""Wake clips keep the last few seconds of audio, cap their number, and record the outcome."""

import json
import wave

import numpy as np
import pytest

from reachy_mini_gemini_live.wake_clips import WakeClipRecorder, recorder_from_env


def test_saves_only_the_last_seconds(tmp_path) -> None:
    rec = WakeClipRecorder(tmp_path, seconds=1.0)
    for i in range(30):  # 3 s of 100 ms chunks, each filled with its index
        rec.push(np.full(1600, i, dtype=np.int16), 16000)

    path = rec.save(0.93)

    with wave.open(str(path)) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        assert w.getframerate() == 16000
    assert 16000 <= len(audio) < 16000 + 1600
    assert audio[-1] == 29 and audio[0] >= 19
    assert path.name.endswith("_0.930.wav")


def test_keeps_newest_and_labels_outcome(tmp_path, monkeypatch) -> None:
    rec = WakeClipRecorder(tmp_path, keep=2)
    names = iter(f"2026010{i}T000000Z" for i in range(1, 9))

    class _Now:
        @staticmethod
        def now(tz=None):
            class _T:
                def strftime(self, _fmt):
                    return next(names)

            return _T()

    monkeypatch.setattr("reachy_mini_gemini_live.wake_clips.datetime", _Now)
    rec.push(np.zeros(160, dtype=np.int16), 16000)
    paths = [rec.save(0.9) for _ in range(3)]
    rec.label(paths[-1], "false")

    assert sorted(p.name for p in tmp_path.glob("*.wav")) == [paths[1].name, paths[2].name]
    entries = [json.loads(line) for line in (tmp_path / "index.jsonl").read_text().splitlines()]
    assert entries[-1] == {"clip": paths[2].name, "outcome": "false"}


def test_nothing_buffered_and_env_off(tmp_path, monkeypatch) -> None:
    assert WakeClipRecorder(tmp_path).save(0.9) is None
    monkeypatch.setenv("REACHY_MINI_WAKE_CLIPS", "0")
    assert recorder_from_env(tmp_path) is None
    monkeypatch.delenv("REACHY_MINI_WAKE_CLIPS")
    assert recorder_from_env(tmp_path).directory == tmp_path / "wake_clips"


@pytest.mark.asyncio
async def test_false_wake_guard_labels_the_clip(tmp_path) -> None:
    from unittest.mock import MagicMock

    from reachy_mini_gemini_live import gemini_live

    deps = gemini_live.ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = gemini_live.GeminiLiveHandler(deps)
    handler._false_wake_timeout_s = 0
    handler._wake_clips = MagicMock()
    handler._last_wake_clip = tmp_path / "clip.wav"
    handler._user_spoke_since_wake = True

    await handler._false_wake_guard()

    handler._wake_clips.label.assert_called_once_with(tmp_path / "clip.wav", "real")
    assert handler._last_wake_clip is None
