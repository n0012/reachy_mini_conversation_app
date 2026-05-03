"""Remember tool — lets Reachy save a fact to long-term memory mid-conversation."""

from typing import Any, Dict

from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


class RememberTool(Tool):
    """Save an important fact to long-term memory."""

    name = "remember"
    description = (
        "Save an important fact about the user or your conversation to long-term memory. "
        "Call this when you learn something worth remembering across future sessions — "
        "such as a name, preference, relationship, or recurring topic. "
        "Do not call this for trivial or one-off details."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "fact": {
                "type": "string",
                "description": "The fact to remember, written as a short declarative sentence.",
            }
        },
        "required": ["fact"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        fact: str = kwargs.get("fact", "").strip()
        if not fact:
            return {"status": "error", "message": "No fact provided."}

        # Access the memory backend via the handler stored on deps (set in gemini_live.py)
        memory = getattr(deps, "memory_backend", None)
        profile = getattr(deps, "active_profile", "default") or "default"
        user_id = getattr(deps, "active_user", None)

        if memory is not None:
            await memory.remember(profile, fact, user_id=user_id)
            return {"status": "remembered", "fact": fact, "user": user_id or "unknown"}

        return {"status": "skipped", "reason": "No memory backend available."}
