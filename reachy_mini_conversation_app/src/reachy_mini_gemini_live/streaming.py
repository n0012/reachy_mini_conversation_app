"""Minimal stand-ins for the fastrtc primitives used by the headless audio loop.

Adapted from pollen-robotics/reachy_mini_conversation_app (streaming.py, #431).
fastrtc pins gradio<6, and every gradio 5.x caps pydantic<=2.12.3 while
reachy-mini>=1.12 needs pydantic>=2.12.5, so the robot install cannot depend
on fastrtc. fastrtc/gradio remain an optional extra for `--gradio` mode.
"""

import asyncio
from typing import TypeVar, TypeAlias
from collections.abc import Mapping, Callable

import numpy as np
from numpy.typing import NDArray


StreamMessage: TypeAlias = Mapping[str, object]
AudioArray: TypeAlias = NDArray[np.int16] | NDArray[np.float32]

QueueItem = TypeVar("QueueItem")


class AdditionalOutputs:
    """Text or metadata emitted alongside audio frames."""

    def __init__(self, *args: StreamMessage) -> None:
        """Initialize with one or more emitted messages."""
        self.args = args


class AsyncStreamHandler:
    """Minimal async stream handler state used by the local audio loop."""

    def __init__(
        self,
        expected_layout: str = "mono",
        output_sample_rate: int = 24000,
        input_sample_rate: int = 48000,
    ) -> None:
        """Initialize shared stream handler state."""
        self.expected_layout = expected_layout
        self.output_sample_rate = output_sample_rate
        self.input_sample_rate = input_sample_rate
        self._clear_queue: Callable[[], object] | None = None


async def wait_for_item(queue: "asyncio.Queue[QueueItem]", timeout: float = 0.1) -> QueueItem | None:
    """Return the next queue item, or None when no item arrives before timeout."""
    try:
        return await asyncio.wait_for(queue.get(), timeout=timeout)
    except asyncio.TimeoutError:
        return None


def audio_to_int16(audio: AudioArray) -> NDArray[np.int16]:
    """Convert int16 or float32 audio data to int16 samples."""
    if audio.dtype == np.int16:
        return audio.astype(np.int16, copy=False)
    if audio.dtype == np.float32:
        return (audio * 32767.0).astype(np.int16)
    raise TypeError(f"Unsupported audio data type: {audio.dtype}")


def audio_to_float32(audio: AudioArray) -> NDArray[np.float32]:
    """Convert int16 or float32 audio data to float32 samples."""
    if audio.dtype == np.int16:
        return audio.astype(np.float32) / 32768.0
    if audio.dtype == np.float32:
        return audio.astype(np.float32, copy=False)
    raise TypeError(f"Unsupported audio data type: {audio.dtype}")
