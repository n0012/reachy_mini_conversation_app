"""Repeat camera calls within the cooldown are answered without taking a snapshot."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _call(call_id: str):
    fc = SimpleNamespace(name="camera", id=call_id, args={"question": "Who is there?"})
    return SimpleNamespace(tool_call=SimpleNamespace(function_calls=[fc]))


@pytest.mark.asyncio
async def test_second_camera_call_within_cooldown_is_skipped() -> None:
    from reachy_mini_gemini_live import gemini_live

    deps = gemini_live.ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = gemini_live.GeminiLiveHandler(deps)
    handler.tool_manager = MagicMock(start_tool=AsyncMock(return_value=SimpleNamespace(tool_id="t")))
    handler.session = MagicMock(send_tool_response=AsyncMock())

    await handler._handle_tool_call(_call("a"))
    await handler._handle_tool_call(_call("b"))

    assert handler.tool_manager.start_tool.await_count == 1
    sent = handler.session.send_tool_response.await_args.kwargs["function_responses"][0]
    assert (sent.id, sent.response["status"]) == ("b", "skipped")

    handler._last_camera_call -= gemini_live.CAMERA_COOLDOWN_S
    await handler._handle_tool_call(_call("c"))
    assert handler.tool_manager.start_tool.await_count == 2
