"""Recall tool — Gemini fetches additional memories mid-conversation.

The session-start memory dump injects the top 20 facts ranked by similarity to
a generic "what do I know about this user" query. When the conversation goes
somewhere that wasn't covered by that initial pull (e.g. user mentions "the
project we discussed in March" or "Sarah's birthday"), Gemini can call this
tool with a topic-specific query to fetch more.

Each call = 1 Vertex Memory Bank :retrieve hit, ~500ms latency.
"""

import logging
from typing import Any, Dict

from reachy_mini_gemini_live.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class RecallTool(Tool):
    """Search prior memories for facts matching a specific topic."""

    name = "recall"
    description = (
        "Search this user's prior memories for facts matching a specific topic. "
        "Use when the conversation references something you don't already have "
        "context on — e.g. the user mentions a name, project, place, or past "
        "event you weren't briefed on at session start. Pass a short search "
        "query (a few keywords or a question). Returns up to 10 matching facts "
        "with their memory_ids (ranked by relevance and recency). The memory_ids "
        "can be passed to the `forget` tool if you decide a memory is stale or "
        "incorrect. If nothing comes back, the user has no stored memories on "
        "that topic — ask them to fill you in."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "What to search for. Examples: 'the Avalanche game', "
                    "'kid named Maya', 'trip to Berlin', 'recurring back pain'. "
                    "Plain phrasing works best — this is a semantic search."
                ),
            },
        },
        "required": ["query"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        query: str = (kwargs.get("query") or "").strip()
        if not query:
            return {"status": "error", "message": "No query provided."}

        memory = getattr(deps, "memory_backend", None)
        memory_enabled = getattr(deps, "memory_enabled", False)
        if memory is None or not memory_enabled:
            return {"status": "skipped", "reason": "Memory not enabled for this profile."}

        profile = getattr(deps, "active_profile", "default") or "default"
        user_id = getattr(deps, "active_user", None)

        try:
            matches = await memory.search(profile, query, user_id=user_id, top_k=10, with_ids=True)
        except Exception as exc:
            logger.warning("recall failed: %s", exc)
            return {"status": "error", "message": str(exc)}

        if not matches:
            return {"status": "no_matches", "query": query, "matches": []}
        return {"status": "ok", "query": query, "matches": matches, "count": len(matches)}
