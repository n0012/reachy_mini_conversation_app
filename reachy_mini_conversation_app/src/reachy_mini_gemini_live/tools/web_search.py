import os
import asyncio
import logging
from typing import Any, Dict

from reachy_mini_gemini_live.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

# The Live model's built-in google_search only sees result snippets and tends to give
# up on specifics ("I can't see the property names"). A regular model with search +
# page reading digs further, so detailed lookups are delegated to it.
SEARCH_MODEL = os.environ.get("WEB_SEARCH_MODEL", "gemini-3.8-flash")
SEARCH_TIMEOUT_S = 45.0


class WebSearch(Tool):
    """Research a question on the web and return a concise, specific answer."""

    name = "web_search"
    description = (
        "Look something up on the web in depth: specific names, listings, prices, hours, "
        "schedules, addresses, or anything your quick search couldn't pin down. "
        "Say you're looking it up first — it takes a few seconds."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The full question to research, with any relevant context.",
            },
        },
        "required": ["query"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Run the query through a search-grounded Gemini model."""
        query = (kwargs.get("query") or "").strip()
        if not query:
            return {"error": "query must be a non-empty string"}
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            return {"error": "GEMINI_API_KEY not set"}

        logger.info("Tool call: web_search query=%s", query[:200])

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        config = types.GenerateContentConfig(
            tools=[
                types.Tool(google_search=types.GoogleSearch()),
                types.Tool(url_context=types.UrlContext()),
            ],
            system_instruction=(
                "Answer for a voice assistant. Be specific: give real names, numbers and "
                "dates found in the sources. Keep it under 120 words, no markdown. "
                "If something genuinely can't be found, say so briefly."
            ),
        )
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(model=SEARCH_MODEL, contents=query, config=config),
                timeout=SEARCH_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning("web_search timed out after %.0fs", SEARCH_TIMEOUT_S)
            return {"error": "search timed out"}
        except Exception as e:
            logger.warning("web_search failed: %s", e)
            return {"error": f"search failed: {e}"}

        answer = (response.text or "").strip()
        logger.info("web_search answer: %s", answer[:200])
        return {"answer": answer} if answer else {"error": "no answer found"}
