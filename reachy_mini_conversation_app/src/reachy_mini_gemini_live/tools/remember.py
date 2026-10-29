"""Remember tool — lets Reachy save a fact to long-term memory mid-conversation."""

from typing import Any, Dict

from reachy_mini_gemini_live.tools.core_tools import Tool, ToolDependencies


class RememberTool(Tool):
    """Save an important fact to long-term memory."""

    name = "remember"
    description = (
        "Save a fact to long-term memory right now. "
        "The whole conversation is automatically distilled into memory when it ends, "
        "so only call this when the user explicitly asks you to remember something, "
        "or to correct a fact you got wrong. Do not call it for details you merely heard. "
        "If the fact is intentionally short-lived (e.g. 'user is at the airport today', "
        "'feeling sick this week'), set lifetime_days to auto-expire it. "
        "Default is permanent."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "fact": {
                "type": "string",
                "description": "The fact to remember, written as a short declarative sentence.",
            },
            "lifetime_days": {
                "type": "integer",
                "description": (
                    "Optional. Days until this memory auto-expires (deleted at next session start). "
                    "Use for time-bounded facts: 1 = today, 7 = this week, 30 = this month. "
                    "Omit for permanent memories (the default)."
                ),
            },
        },
        "required": ["fact"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Save the fact to long-term memory."""
        fact: str = kwargs.get("fact", "").strip()
        if not fact:
            return {"status": "error", "message": "No fact provided."}
        lifetime_days = kwargs.get("lifetime_days")
        try:
            lifetime_days = int(lifetime_days) if lifetime_days is not None else None
        except (TypeError, ValueError):
            lifetime_days = None

        memory = getattr(deps, "memory_backend", None)
        profile = getattr(deps, "active_profile", "default") or "default"
        user_id = getattr(deps, "active_user", None)

        memory_enabled = getattr(deps, "memory_enabled", False)
        if memory is not None and memory_enabled:
            if not await memory.remember(profile, fact, user_id=user_id, lifetime_days=lifetime_days):
                return {
                    "status": "error",
                    "message": "Long-term memory is unavailable right now — tell the user it wasn't saved.",
                }
            return {
                "status": "remembered",
                "fact": fact,
                "user": user_id or "unknown",
                "lifetime_days": lifetime_days,
            }

        return {"status": "skipped", "reason": "Memory not enabled for this profile."}
