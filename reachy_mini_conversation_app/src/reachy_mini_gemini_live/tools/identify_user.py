"""identify_user tool — switches the active user for memory scoping."""

import logging
from typing import Any, Dict

from reachy_mini_gemini_live.memory import get_active_user, set_active_user
from reachy_mini_gemini_live.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class IdentifyUserTool(Tool):
    """Set the active user for this and future sessions.

    Memories are scoped per user_id — calling this routes all subsequent
    memory operations to that person's profile so family members don't
    have their memories mixed.
    """

    name = "identify_user"
    description = (
        "Switch the active user for memory and personalization. Call this when a person "
        "introduces themselves (e.g. 'Hi, this is Nick'), when you recognize the speaker "
        "has changed, or when the user asks you to address them as a different person. "
        "After calling, all memory reads and writes will be scoped to this user. "
        "Use a short, lowercase name like 'nick' or 'zach' — no spaces, no titles."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "user_id": {
                "type": "string",
                "description": "Short lowercase identifier for the user, e.g. 'nick', 'zach', 'sarah'.",
            }
        },
        "required": ["user_id"],
    }

    # Words that are clearly not names (common speech-recognition artifacts)
    _NOT_NAMES = {
        "face",
        "hey",
        "yes",
        "no",
        "ok",
        "hi",
        "hello",
        "um",
        "uh",
        "sure",
        "it",
        "me",
        "i",
        "my",
        "this",
        "that",
        "the",
        "a",
        "an",
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Switch the active memory user."""
        user_id = (kwargs.get("user_id") or "").strip().lower().replace(" ", "_")
        if not user_id:
            return {"status": "error", "message": "No user_id provided."}
        if user_id in self._NOT_NAMES or len(user_id) < 2:
            return {"status": "error", "message": f"'{user_id}' doesn't look like a name — please ask again."}

        previous = get_active_user()
        set_active_user(user_id)
        # Update the deps so anything reading active user during this session sees the new value
        deps.active_user = user_id

        logger.info("Active user switched: %r -> %r", previous, user_id)
        return {"status": "switched", "previous_user": previous, "active_user": user_id}
