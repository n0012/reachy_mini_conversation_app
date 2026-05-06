"""Forget tool — Gemini deletes specific memories it identifies as stale.

Typical flow Gemini uses:
1. Notice a fact that's wrong/stale during conversation
2. Call recall(query="...") to find the offending memory's memory_id
3. Call forget(memory_ids=["memories/..."]) to delete it

Each delete is permanent. The tool description explicitly tells the model to be
conservative and only forget facts it has high confidence are wrong/stale.
"""

import logging
from typing import Any, Dict, List

from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class ForgetTool(Tool):
    """Delete one or more memories by ID."""

    name = "forget"
    description = (
        "Permanently delete one or more memories from long-term storage. "
        "Use when you identify facts that are stale, contradicted by newer "
        "information, or were saved by mistake. To get memory_ids, first call "
        "the `recall` tool — it returns matches with their memory_id field. "
        "Be conservative: only forget memories you have HIGH confidence are "
        "wrong or no longer relevant. When in doubt, ask the user before "
        "deleting. Returns the count deleted and the facts that were removed "
        "so you can confirm to the user what changed."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "memory_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "List of memory_id strings to delete (full resource names like "
                    "'projects/.../memories/abc'). Get these from a prior recall "
                    "call's `matches[*].memory_id` field. Pass 1-10 at a time."
                ),
            },
            "reason": {
                "type": "string",
                "description": (
                    "Brief reason for forgetting these memories — for the activity log. "
                    "E.g. 'user moved to Boston, old Denver address is stale'."
                ),
            },
        },
        "required": ["memory_ids"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        memory_ids: List[str] = kwargs.get("memory_ids") or []
        reason: str = (kwargs.get("reason") or "").strip() or "no reason given"
        if not memory_ids:
            return {"status": "error", "message": "No memory_ids provided."}
        if len(memory_ids) > 20:
            return {"status": "error", "message": "Refuse to delete more than 20 memories at once."}

        memory = getattr(deps, "memory_backend", None)
        memory_enabled = getattr(deps, "memory_enabled", False)
        if memory is None or not memory_enabled:
            return {"status": "skipped", "reason": "Memory not enabled for this profile."}

        deleted: list[str] = []
        failed: list[str] = []
        for mid in memory_ids:
            mid = mid.strip()
            if not mid:
                continue
            ok = await memory.delete_memory(mid)
            (deleted if ok else failed).append(mid)
            logger.info("forget(%s): %s (reason=%s)", mid, "ok" if ok else "FAILED", reason)

        return {
            "status": "ok",
            "deleted_count": len(deleted),
            "failed_count": len(failed),
            "reason": reason,
        }
