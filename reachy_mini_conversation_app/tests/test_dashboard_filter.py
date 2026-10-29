"""Placeholder non-answers are dropped before the dashboard and the stuck-response detector."""

from unittest.mock import MagicMock

import pytest


def _handler():
    from reachy_mini_gemini_live import gemini_live

    deps = gemini_live.ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    return gemini_live.GeminiLiveHandler(deps)


def _drain(handler) -> list[dict]:
    out = []
    while not handler.output_queue.empty():
        out.extend(handler.output_queue.get_nowait().args)
    return out


@pytest.mark.parametrize(
    "text",
    [
        "<no speech detected>",
        "<no speech>{pause}",
        "...",
        "---",
        "・",
        "<!-- No response required -->",
        "(No response needed)",
        "\U000e0041\U000e0042",  # invisible tag characters
    ],
)
def test_placeholders_are_recognized(text) -> None:
    from reachy_mini_gemini_live.gemini_live import _is_placeholder

    assert _is_placeholder(text)


@pytest.mark.parametrize("text", ["Hello!", "It's 53°F and clear.", "Goodnight, Nick.", "OK"])
def test_real_speech_is_not_a_placeholder(text) -> None:
    from reachy_mini_gemini_live.gemini_live import _is_placeholder

    assert not _is_placeholder(text)


@pytest.mark.asyncio
async def test_repeated_placeholders_do_not_reach_dashboard_or_restart(monkeypatch) -> None:
    handler = _handler()
    restarts = []

    async def _restart() -> None:
        restarts.append(1)

    monkeypatch.setattr(handler, "_restart_session", _restart)
    for _ in range(3):
        await handler._flush_transcript_chunks("assistant", ["<no speech detected>"])
    await handler._flush_transcript_chunks("assistant", ["Hi there."])

    assert [m["content"] for m in _drain(handler)] == ["Hi there."]
    assert handler._session_transcript == [{"role": "assistant", "content": "Hi there."}]
    assert restarts == []


@pytest.mark.asyncio
async def test_turn_complete_clears_leaked_idle_flag() -> None:
    handler = _handler()
    handler.is_idle_tool_call = True

    await handler._handle_turn_complete()

    assert handler.is_idle_tool_call is False
