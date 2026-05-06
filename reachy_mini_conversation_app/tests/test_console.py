"""Tests for the headless console stream."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from reachy_mini.media.media_manager import MediaBackend
from reachy_mini_gemini_live.config import AVAILABLE_VOICES
from reachy_mini_gemini_live.console import LocalStream
from reachy_mini_gemini_live.headless_personality_ui import mount_personality_routes


def test_clear_audio_queue_prefers_clear_player_when_available() -> None:
    """Local GStreamer audio should use the lower-level player flush when available."""
    handler = MagicMock()
    audio = SimpleNamespace(
        clear_player=MagicMock(),
        clear_output_buffer=MagicMock(),
    )
    robot = SimpleNamespace(media=SimpleNamespace(audio=audio, backend=MediaBackend.LOCAL))
    stream = LocalStream(handler, robot)

    stream.clear_audio_queue()

    audio.clear_player.assert_called_once()
    audio.clear_output_buffer.assert_not_called()
    assert isinstance(handler.output_queue, asyncio.Queue)
    assert handler.output_queue.empty()


def test_clear_audio_queue_uses_output_buffer_for_webrtc() -> None:
    """WebRTC audio should flush queued playback via the output buffer API."""
    handler = MagicMock()
    audio = SimpleNamespace(
        clear_player=MagicMock(),
        clear_output_buffer=MagicMock(),
    )
    robot = SimpleNamespace(media=SimpleNamespace(audio=audio, backend=MediaBackend.WEBRTC))
    stream = LocalStream(handler, robot)

    stream.clear_audio_queue()

    audio.clear_output_buffer.assert_called_once()
    audio.clear_player.assert_not_called()
    assert isinstance(handler.output_queue, asyncio.Queue)
    assert handler.output_queue.empty()


def test_clear_audio_queue_falls_back_when_backend_is_unknown() -> None:
    """Unknown backends should still best-effort flush pending playback."""
    handler = MagicMock()
    audio = SimpleNamespace(clear_output_buffer=MagicMock())
    robot = SimpleNamespace(media=SimpleNamespace(audio=audio, backend=None))
    stream = LocalStream(handler, robot)

    stream.clear_audio_queue()

    audio.clear_output_buffer.assert_called_once()
    assert isinstance(handler.output_queue, asyncio.Queue)
    assert handler.output_queue.empty()


@pytest.mark.asyncio
async def test_play_loop_feeds_head_wobbler_with_local_playback_delay() -> None:
    """Local playback should drive speech wobble using the queued player delay."""
    head_wobbler = MagicMock()
    chunk = np.array([1, -2, 3, -4], dtype=np.int16)

    class Handler:
        def __init__(self) -> None:
            self.deps = SimpleNamespace(head_wobbler=head_wobbler)
            self.output_queue = asyncio.Queue()
            self._emitted = False

        async def emit(self):
            if not self._emitted:
                self._emitted = True
                return (24000, chunk.copy())
            return None

    audio = SimpleNamespace(
        _playback_next_pts_ns=1_500_000_000,
        _get_playback_running_time_ns=lambda: 500_000_000,
    )
    media = SimpleNamespace(
        audio=audio,
        backend=MediaBackend.LOCAL,
        get_output_audio_samplerate=lambda: 24000,
        push_audio_sample=MagicMock(),
    )
    robot = SimpleNamespace(media=media)
    handler = Handler()
    stream = LocalStream(handler, robot)

    async def stop_soon() -> None:
        await asyncio.sleep(0.01)
        stream._stop_event.set()

    stopper = asyncio.create_task(stop_soon())
    try:
        await asyncio.wait_for(stream.play_loop(), timeout=1.0)
    finally:
        await stopper

    head_wobbler.feed_pcm.assert_called_once()
    args, kwargs = head_wobbler.feed_pcm.call_args
    assert np.array_equal(args[0], chunk.reshape(1, -1))
    assert args[1] == 24000
    assert kwargs["start_delay_s"] == pytest.approx(1.0)
    media.push_audio_sample.assert_called_once()


def test_headless_personality_routes_return_gemini_voices() -> None:
    """Headless personality UI should expose the Gemini voice catalog."""
    app = FastAPI()
    handler = MagicMock()
    mount_personality_routes(app, handler, lambda: None)

    client = TestClient(app)
    response = client.get("/voices")

    assert response.status_code == 200
    assert response.json() == AVAILABLE_VOICES


def test_headless_personality_routes_apply_voice_accepts_query_param() -> None:
    """Headless personality UI should apply a voice change from a POST query param."""
    app = FastAPI()
    handler = MagicMock()
    handler.change_voice = AsyncMock(return_value="Voice changed to Kore.")

    loop = asyncio.new_event_loop()
    started = threading.Event()

    def _run_loop() -> None:
        asyncio.set_event_loop(loop)
        started.set()
        loop.run_forever()

    thread = threading.Thread(target=_run_loop, daemon=True)
    thread.start()
    started.wait(timeout=1.0)

    try:
        mount_personality_routes(app, handler, lambda: loop)

        client = TestClient(app)
        response = client.post("/voices/apply?voice=Kore")

        assert response.status_code == 200
        assert response.json() == {"ok": True, "status": "Voice changed to Kore."}
        handler.change_voice.assert_awaited_once_with("Kore")
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=1.0)
        loop.close()
