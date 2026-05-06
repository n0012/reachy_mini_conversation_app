import asyncio
import logging
from typing import Any, Dict

from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class GoToSleep(Tool):
    """Enter sleep mode — close the Gemini Live session and listen only for the wake word.

    Call this when the user says goodnight, asks you to rest, says they're done
    talking, asks you to stop listening, or otherwise indicates the conversation
    is over. Always say a brief verbal goodbye BEFORE calling this tool — the
    Live session closes immediately when sleep starts so any speech queued
    afterward is dropped.
    """

    name = "go_to_sleep"
    description = (
        "Enter sleep mode. Use when the user says goodnight, asks you to rest, "
        "says bye, or wants the conversation to end. Always speak a short goodbye "
        "FIRST — the audio session closes as soon as this tool is called. "
        "After sleeping, only 'Hey Reachy' will wake you again."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Brief reason for sleeping (e.g., 'user said goodnight', 'long pause').",
            },
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        reason = kwargs.get("reason", "user requested sleep")
        logger.info("Tool call: go_to_sleep reason=%s", reason)
        if deps.request_sleep is None:
            return {"status": "error", "message": "sleep handler not wired"}
        # Give Gemini a moment to finish speaking the goodbye before tearing down
        # the Live session. ~1.5s is enough for "Goodnight!" without feeling laggy.
        await asyncio.sleep(1.5)
        asyncio.create_task(deps.request_sleep())
        return {"status": "sleeping", "reason": reason}
