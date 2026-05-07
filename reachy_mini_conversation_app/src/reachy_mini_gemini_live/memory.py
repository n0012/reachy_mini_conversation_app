"""Vertex AI Memory Bank integration and fallback FileMemoryBackend.

Phase 4 implementation. The MemoryBackend protocol defines the interface;
VertexMemoryBackend calls the Vertex AI Memory Bank REST API; FileMemoryBackend
is a local-file fallback for development without GCP.

Memory Bank REST API (no Python SDK required — uses google-auth + urllib):
  Base: https://{location}-aiplatform.googleapis.com/v1beta1
  Parent: projects/{project}/locations/{location}/reasoningEngines/{engine_id}

  POST /{parent}/memories:generate   — ingest transcript (LRO, fire-and-forget)
  POST /{parent}/memories:generate   — store direct fact (LRO, fire-and-forget)
  POST /{parent}/memories:retrieve   — fetch relevant memories (synchronous)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
import asyncio
from datetime import date
from pathlib import Path
from typing import Protocol, runtime_checkable

import google.auth
import google.auth.transport.requests
import math
import urllib.parse
from datetime import datetime, timedelta as _td, timezone


logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

MEMORY_SEARCH_QUERY = "What do I know about this user, their preferences, and our recent conversations?"

from reachy_mini_gemini_live.config import REACHY_MINI_DIR  # noqa: E402

_DEFAULT_MEMORY_DIR = REACHY_MINI_DIR / "memory"
_ACTIVE_USER_FILE = REACHY_MINI_DIR / "active_user.json"
DEFAULT_USER_ID = "unknown"

# Recency-boost re-ranking parameters. Vertex Memory Bank's :retrieve API
# only ranks by similarity (no native time-weighting), so we fetch a wider
# candidate pool and re-score client-side combining similarity + recency:
#   score = similarity + RECENCY_ALPHA * exp(-age_days / RECENCY_TAU_DAYS)
# Tuned so a fact from yesterday outranks a slightly more similar fact from
# 6 months ago, but old facts still surface when nothing recent matches.
RECENCY_ALPHA = float(os.getenv("REACHY_MINI_RECENCY_ALPHA", "0.15"))  # recency weight (0=off, 1=strong)
RECENCY_TAU_DAYS = 30.0     # decay constant — bonus ~37% at this age
MAX_DISTANCE = 1.5          # client-side similarity filter
DEFAULT_FETCH_K = 50        # over-fetch before re-rank
DEFAULT_TOP_K = int(os.getenv("REACHY_MINI_TOP_K", "20"))             # memories surfaced per session


def get_active_user() -> str:
    """Return the active user_id from disk, falling back to DEFAULT_USER_ID."""
    try:
        if _ACTIVE_USER_FILE.exists():
            data = json.loads(_ACTIVE_USER_FILE.read_text())
            return data.get("user_id", DEFAULT_USER_ID)
    except Exception as exc:
        logger.warning("active_user read failed: %s", exc)
    return DEFAULT_USER_ID


def set_active_user(user_id: str) -> None:
    """Persist the active user_id to disk so future sessions remember it."""
    user_id = (user_id or "").strip().lower().replace(" ", "_")
    if not user_id:
        return
    try:
        _ACTIVE_USER_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ACTIVE_USER_FILE.write_text(json.dumps({"user_id": user_id}))
        logger.info("Active user set to %r", user_id)
    except Exception as exc:
        logger.warning("active_user write failed: %s", exc)


# ── Protocol ─────────────────────────────────────────────────────────────────

@runtime_checkable
class MemoryBackend(Protocol):
    """Interface all memory backends must satisfy."""

    async def load_context(self, profile: str, user_id: str | None = None) -> str:
        """Return a text block injected into system_instruction at session start."""
        ...

    async def search(self, profile: str, query: str, user_id: str | None = None, top_k: int = 10, with_ids: bool = False) -> list:
        """Mid-conversation recall — return up to top_k facts matching the query."""
        ...

    async def save_session(self, profile: str, transcript: list[dict], user_id: str | None = None) -> None:
        """Persist a completed session transcript (fire-and-forget is fine)."""
        ...

    async def remember(self, profile: str, fact: str, user_id: str | None = None, lifetime_days: int | None = None) -> None:
        """Store an explicit fact the model decided to keep. lifetime_days makes it expire."""
        ...

    async def list_memories(self, profile: str, user_id: str | None = None, page_size: int = 100) -> list[dict]:
        """List all memories for a scope."""
        ...

    async def delete_memory(self, memory_name: str) -> bool:
        """Delete a single memory by full resource name."""
        ...

    async def purge_expired(self, profile: str, user_id: str | None = None) -> int:
        """Sweep ttl_until-tagged memories whose date is past. Returns count deleted."""
        ...

    async def compact_memories(self, profile: str, user_id: str | None = None, dry_run: bool = True, max_actions: int = 25) -> dict:
        """LLM-driven dedup/cleanup pass. Returns {analyzed, proposed, applied, summary}."""
        ...


# ── Vertex AI Memory Bank ─────────────────────────────────────────────────────

_credentials: google.auth.credentials.Credentials | None = None
_http_request = google.auth.transport.requests.Request()


def _get_token() -> str:
    """Return a valid OAuth2 bearer token via google-auth (no gcloud CLI required).

    Reads credentials from, in priority order:
      1. GOOGLE_APPLICATION_CREDENTIALS env var (SA JSON or ADC JSON)
      2. Well-known ADC file (~/.config/gcloud/application_default_credentials.json)
    Handles both 'service_account' and 'authorized_user' credential types.
    """
    global _credentials
    if _credentials is None:
        _credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    if not _credentials.valid:
        try:
            _credentials.refresh(_http_request)
        except Exception:
            # Re-initialize on next call so fresh ADC is picked up after re-auth.
            _credentials = None
            raise
    return _credentials.token  # type: ignore[return-value]


def _api(method: str, url: str, body: dict | None = None) -> dict:
    token = _get_token()
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


class VertexMemoryBackend:
    """Memory backed by Vertex AI Memory Bank REST API.

    Requires:
      GCP_PROJECT_ID, GCP_LOCATION, VERTEX_REASONING_ENGINE_ID env vars
      gcloud CLI authenticated (ADC)
      Reasoning Engine service account: roles/aiplatform.user
    """

    def __init__(self) -> None:
        self.project = os.environ["GCP_PROJECT_ID"]
        self.location = os.environ.get("GCP_LOCATION", "us-central1")
        self.engine_id = os.environ["VERTEX_REASONING_ENGINE_ID"]
        self._base = f"https://{self.location}-aiplatform.googleapis.com/v1beta1"
        self._parent = (
            f"projects/{self.project}/locations/{self.location}"
            f"/reasoningEngines/{self.engine_id}"
        )

    def _url(self, path: str) -> str:
        return f"{self._base}/{self._parent}/{path}"

    def _scope(self, profile: str, user_id: str | None = None) -> dict:
        return {"user_id": user_id or get_active_user(), "profile": profile}

    async def _retrieve(
        self,
        profile: str,
        query: str,
        user_id: str | None = None,
        fetch_k: int = DEFAULT_FETCH_K,
        top_k: int = DEFAULT_TOP_K,
        with_ids: bool = False,
        max_distance: float | None = None,
    ) -> list:
        """Retrieve facts for a query, distance-filter, recency-boost, return top facts.

        Vertex Memory Bank :retrieve only ranks by similarity. We over-fetch
        and then re-rank with: score = (1 - distance) + α * exp(-age_days / τ).
        Recent facts surface naturally even when an older fact is slightly
        more similar; old facts still win when nothing recent matches.

        If with_ids=True, returns list of dicts {memory_id, fact, age_days}.
        Otherwise returns plain list of fact strings.
        """
        scope = self._scope(profile, user_id)
        try:
            resp = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _api("POST", self._url("memories:retrieve"), {
                    "scope": scope,
                    "similarity_search_params": {
                        "search_query": query,
                        "top_k": fetch_k,
                    },
                }),
            )
        except Exception as exc:
            logger.warning(
                "Memory: retrieve failed (user=%r profile=%r query=%r): %s",
                scope["user_id"], profile, query[:60], exc,
            )
            return []

        all_memories = resp.get("retrievedMemories", [])
        cutoff = max_distance if max_distance is not None else MAX_DISTANCE
        kept = [m for m in all_memories if m.get("distance") is None or m.get("distance") <= cutoff]
        if len(kept) < len(all_memories):
            logger.info(
                "Memory: filtered %d low-quality results (maxDistance=%.1f)",
                len(all_memories) - len(kept), MAX_DISTANCE,
            )
        if not kept:
            return []

        ranked = self._rerank_with_recency(kept)[:top_k]
        if not with_ids:
            return [m["memory"]["fact"] for m in ranked if m.get("memory", {}).get("fact")]
        now = datetime.now(timezone.utc)
        out: list[dict] = []
        for m in ranked:
            mem = m.get("memory") or {}
            fact = mem.get("fact")
            name = mem.get("name")
            if not fact or not name:
                continue
            ts = mem.get("createTime") or mem.get("updateTime")
            age_days: float | None = None
            if ts:
                try:
                    age_days = round((now - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() / 86400.0, 1)
                except Exception:
                    pass
            out.append({"memory_id": name, "fact": fact, "age_days": age_days})
        return out

    @staticmethod
    def _rerank_with_recency(memories: list[dict]) -> list[dict]:
        """Re-sort by (1 - distance) + RECENCY_ALPHA * exp(-age_days / τ). Newest first on ties."""
        now = datetime.now(timezone.utc)

        def _age_days(m: dict) -> float:
            ts = m.get("memory", {}).get("createTime") or m.get("memory", {}).get("updateTime")
            if not ts:
                return 365.0  # treat unknown as a year old
            try:
                # Vertex returns RFC3339 like "2026-04-15T10:30:00.123456Z"; fromisoformat needs +00:00
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                return max(0.0, (now - dt).total_seconds() / 86400.0)
            except Exception:
                return 365.0

        def _score(m: dict) -> float:
            sim = 1.0 - float(m.get("distance", 0.0))  # higher is better
            age = _age_days(m)
            recency_bonus = RECENCY_ALPHA * math.exp(-age / RECENCY_TAU_DAYS)
            return sim + recency_bonus

        return sorted(memories, key=_score, reverse=True)

    async def load_context(self, profile: str, user_id: str | None = None) -> str:
        """Retrieve relevant memories at session start and format as bullet list for system prompt injection."""
        facts = await self._retrieve(profile, MEMORY_SEARCH_QUERY, user_id=user_id)
        scope_user = user_id or get_active_user()
        if not facts:
            logger.info("Memory: no stored memories for user=%r profile=%r", scope_user, profile)
            return ""
        logger.info("Memory: injecting %d facts (user=%r profile=%r)", len(facts), scope_user, profile)
        numbered = "\n".join(f"{i+1}. {f}" for i, f in enumerate(facts))
        return f"<vertex_memory_bank>\nRelevant memories from prior sessions:\n{numbered}\n</vertex_memory_bank>"

    async def search(
        self,
        profile: str,
        query: str,
        user_id: str | None = None,
        top_k: int = 10,
        with_ids: bool = False,
    ) -> list:
        """Mid-conversation recall. Returns up to `top_k` facts matching `query`.

        Used by the `recall` tool — Gemini can request additional memories
        when the initial session-start dump didn't include something it needs.
        Pass with_ids=True to get [{memory_id, fact, age_days}, ...] instead of
        plain strings — used by the `forget` tool to delete specific entries.
        """
        results = await self._retrieve(
            profile, query, user_id=user_id,
            fetch_k=max(top_k * 2, 20),
            top_k=top_k,
            with_ids=with_ids,
        )
        scope_user = user_id or get_active_user()
        logger.info(
            "Memory: search returned %d facts (user=%r profile=%r query=%r)",
            len(results), scope_user, profile, query[:80],
        )
        return results

    @staticmethod
    def _is_meaningful_turn(turn: dict) -> bool:
        """Filter out noise before sending to Memory Bank extraction.

        Skips:
        - Tool call logs (🛠️ prefix) and tool result JSON
        - Very short user turns (< 20 chars) — "okay", "yes", "what?"
        - Turns that are just JSON objects (tool responses)
        Matches OpenClaw thresholds: ≥20 chars for user, ≥100 chars total across the pair.
        """
        content = turn.get("content", "").strip()
        if not content:
            return False
        if content.startswith("🛠️"):
            return False
        if content.startswith("{") and content.endswith("}"):
            return False
        if turn.get("role") == "user" and len(content) < 20:
            return False
        return True

    async def save_session(self, profile: str, transcript: list[dict], user_id: str | None = None) -> None:
        """Ingest transcript into Memory Bank at session close.

        Sends the full meaningful conversation (noise-filtered) for extraction.
        OpenClaw sends only the last pair per turn; we send the full session since
        Gemini Live is a continuous voice session, not a turn-by-turn chat.
        """
        if not transcript:
            return
        meaningful = [t for t in transcript if self._is_meaningful_turn(t)]

        # Apply OpenClaw total-length noise filter
        user_turns = [t for t in meaningful if t.get("role") == "user"]
        assistant_turns = [t for t in meaningful if t.get("role") != "user"]
        user_len = sum(len(t.get("content", "")) for t in user_turns)
        total_len = sum(len(t.get("content", "")) for t in meaningful)
        if user_len < 20 or total_len < 100:
            logger.info("Memory: skipped — too short (user=%d, total=%d)", user_len, total_len)
            return

        # Truncate each turn to 4000 chars (OpenClaw pattern)
        events = [
            {"content": {"role": t["role"], "parts": [{"text": t["content"][:4000]}]}}
            for t in meaningful
        ]
        logger.info("Memory: sending %d/%d turns to extraction (user=%d chars, total=%d chars)",
                    len(events), len(transcript), user_len, total_len)
        scope = self._scope(profile, user_id)
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._generate_memories(scope, {
                "direct_contents_source": {"events": events},
                "revision_labels": {"source": "session"},
            }),
        )

    async def remember(
        self,
        profile: str,
        fact: str,
        user_id: str | None = None,
        lifetime_days: int | None = None,
    ) -> None:
        """Store an explicit fact. Awaited so the call completes.

        If lifetime_days is set, the memory is tagged with a `ttl_until` label
        (UTC ISO date). The session-start sweep deletes any memory whose
        `ttl_until` is in the past — so use this for facts that are intentionally
        ephemeral (e.g. "user is at the airport", lifetime_days=1).
        """
        scope = self._scope(profile, user_id)
        labels = {"source": "remember-tool"}
        if lifetime_days is not None and lifetime_days > 0:
            ttl_until = (datetime.now(timezone.utc) + _td(days=lifetime_days)).strftime("%Y-%m-%d")
            labels["ttl_until"] = ttl_until
            labels["ephemeral"] = "true"
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._generate_memories(scope, {
                "direct_memories_source": {"direct_memories": [{"fact": fact}]},
                "revision_labels": labels,
            }),
        )

    # ── Hygiene primitives ─────────────────────────────────────────────────

    async def list_memories(
        self,
        profile: str,
        user_id: str | None = None,
        page_size: int = 100,
        profile_wide: bool = False,
    ) -> list[dict]:
        """Return memories for a scope (paginated). Each entry has name,
        fact, createTime, updateTime, scope, and optional revision labels.

        profile_wide=True searches all users in the profile — useful for
        keyword search where a family fact may be stored under a different
        user_id (e.g. Mia's soccer stored under user_id=mia but Nick
        is asking about it).
        """
        scope = self._scope(profile, user_id)
        if profile_wide:
            flt = f'scope.profile="{profile}"'
        else:
            flt = f'scope.user_id="{scope["user_id"]}" AND scope.profile="{profile}"'
        all_items: list[dict] = []
        page_token: str | None = None
        for _ in range(20):  # cap pagination at 2000 results to be safe
            params = f"?pageSize={page_size}&filter={urllib.parse.quote(flt)}"
            if page_token:
                params += f"&pageToken={urllib.parse.quote(page_token)}"
            try:
                resp = await asyncio.get_event_loop().run_in_executor(
                    None, lambda p=params: _api("GET", f"{self._base}/{self._parent}/memories{p}", None),
                )
            except Exception as exc:
                logger.warning("Memory: list failed (scope=%r): %s", scope, exc)
                break
            all_items.extend(resp.get("memories", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return all_items

    async def delete_memory(self, memory_name: str) -> bool:
        """Delete a single memory by its full resource name. Returns True on success."""
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: _api("DELETE", f"{self._base}/{memory_name}", None),
            )
            return True
        except Exception as exc:
            logger.warning("Memory: delete failed (name=%s): %s", memory_name, exc)
            return False

    async def purge_expired(self, profile: str, user_id: str | None = None) -> int:
        """Delete all memories whose `ttl_until` revision label is in the past.

        Runs at session start. Returns the count purged.
        """
        items = await self.list_memories(profile, user_id)
        if not items:
            return 0
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        expired = []
        for m in items:
            # Latest revision's labels carry the ttl_until tag (set at remember time)
            labels = (m.get("revisionLabels") or {})
            ttl_until = labels.get("ttl_until")
            if ttl_until and ttl_until < today:
                expired.append(m)
        if not expired:
            return 0
        deleted = 0
        for m in expired:
            ok = await self.delete_memory(m["name"])
            if ok:
                deleted += 1
        scope_user = user_id or get_active_user()
        logger.info(
            "Memory: purged %d expired memories (user=%r profile=%r)",
            deleted, scope_user, profile,
        )
        return deleted

    async def compact_memories(
        self,
        profile: str,
        user_id: str | None = None,
        dry_run: bool = True,
        max_actions: int = 25,
        plan: list[dict] | None = None,
    ) -> dict:
        """LLM-driven cleanup pass over all stored memories.

        Lists every memory, sends them to Gemini for triage (duplicates,
        contradictions, stale facts), then either previews the proposed
        actions (dry_run=True) or applies them. Returns a summary dict:
            {
              "analyzed": int,
              "proposed": [{action, indexes/ids, reason, ...}, ...],
              "applied": int (only when dry_run=False),
              "summary": "...",
              "model": "..."
            }
        """
        # If a pre-computed plan is passed (from a prior dry-run), skip the
        # LLM call and just execute it. Avoids the race where Apply re-runs
        # analysis after the previous Apply already cleaned things up.
        if plan is not None and not dry_run:
            applied = 0
            for a in plan:
                if applied >= max_actions:
                    break
                if a.get("type") == "merge" and a.get("new_fact"):
                    await self.remember(profile, a["new_fact"], user_id=user_id)
                for mid in a.get("memory_ids") or []:
                    if mid and await self.delete_memory(mid):
                        applied += 1
                        if applied >= max_actions:
                            break
            scope_user = user_id or get_active_user()
            logger.info(
                "Memory: compact applied %d items from supplied plan (user=%r profile=%r)",
                applied, scope_user, profile,
            )
            return {
                "dry_run": False,
                "applied": applied,
                "summary": f"Applied {applied} deletion(s) from previously-shown plan",
            }

        items = await self.list_memories(profile, user_id)
        scope_user = user_id or get_active_user()
        if len(items) < 5:
            return {
                "analyzed": len(items),
                "proposed": [],
                "applied": 0,
                "summary": f"Only {len(items)} memories — nothing to compact.",
            }

        # Build the analysis prompt
        memory_lines = []
        for i, m in enumerate(items):
            fact = (m.get("fact") or "").replace("\n", " ").strip()
            ts = (m.get("createTime") or "")[:10] or "unknown"
            memory_lines.append(f"[{i}] ({ts}) {fact}")

        prompt = (
            f"You are reviewing {len(items)} stored memories for user '{scope_user}'.\n"
            "Identify cleanup opportunities. Return STRICT JSON only — no prose, no markdown.\n\n"
            "MEMORIES:\n"
            + "\n".join(memory_lines)
            + "\n\n"
            "Return this exact shape:\n"
            "{\n"
            '  "summary": "<one-sentence overall assessment>",\n'
            '  "actions": [\n'
            '    {"type":"delete", "indexes":[7], "reason":"exact duplicate of [3]"},\n'
            '    {"type":"delete", "indexes":[12], "reason":"superseded by [15] (user moved)"},\n'
            '    {"type":"merge", "indexes":[5,8], "new_fact":"User has two kids: Maya (8) and Leo (5).", "reason":"two facts about same kids"}\n'
            "  ]\n"
            "}\n\n"
            "RULES:\n"
            "- Only flag HIGH-CONFIDENCE issues. When in doubt, leave it alone.\n"
            "- 'delete' permanently removes the listed memories.\n"
            "- 'merge' deletes the listed and creates a single combined fact.\n"
            "- Index numbers must come from the [N] markers above. Do not invent.\n"
            f"- Total deletions across all actions must not exceed {max_actions}.\n"
            "- If nothing needs cleanup, return {\"summary\":\"clean\",\"actions\":[]}.\n"
        )

        # Call Gemini for triage
        from google import genai as _genai
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            return {"error": "GEMINI_API_KEY not set", "analyzed": len(items), "applied": 0}
        try:
            client = _genai.Client(api_key=api_key)
            model_name = "gemini-3.1-pro-preview"
            from google.genai import types as _genai_types
            resp = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=_genai_types.GenerateContentConfig(
                        thinking_config=_genai_types.ThinkingConfig(
                            thinking_level="high",
                        ),
                    ),
                ),
            )
            text = (resp.text or "").strip()
            # Strip markdown fences if present
            if text.startswith("```"):
                text = text.split("```", 2)[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            plan = json.loads(text)
        except Exception as exc:
            logger.warning("Memory: compact LLM call/parse failed: %s", exc)
            return {"error": f"Analysis failed: {exc}", "analyzed": len(items), "applied": 0}

        actions = plan.get("actions") or []
        # Annotate actions with the actual fact texts so the UI can display them
        annotated = []
        for a in actions:
            indexes = a.get("indexes") or []
            facts = []
            ids = []
            for i in indexes:
                if isinstance(i, int) and 0 <= i < len(items):
                    facts.append(items[i].get("fact", "")[:160])
                    ids.append(items[i].get("name"))
            annotated.append({
                "type": a.get("type"),
                "reason": a.get("reason", ""),
                "facts": facts,
                "memory_ids": ids,
                "new_fact": a.get("new_fact"),
            })

        if dry_run:
            return {
                "dry_run": True,
                "analyzed": len(items),
                "proposed": annotated,
                "summary": plan.get("summary", ""),
                "model": model_name,
            }

        # Apply
        applied = 0
        for a in annotated:
            if applied >= max_actions:
                break
            if a["type"] == "merge" and a.get("new_fact"):
                await self.remember(profile, a["new_fact"], user_id=user_id)
            for mid in a.get("memory_ids") or []:
                if mid and await self.delete_memory(mid):
                    applied += 1
                    if applied >= max_actions:
                        break

        logger.info(
            "Memory: compact applied %d/%d action-items (user=%r profile=%r)",
            applied, sum(len(a.get("memory_ids") or []) for a in annotated), scope_user, profile,
        )
        return {
            "dry_run": False,
            "analyzed": len(items),
            "applied": applied,
            "proposed": annotated,
            "summary": plan.get("summary", ""),
            "model": model_name,
        }

    def _generate_memories(self, scope: dict, source: dict) -> None:
        try:
            resp = _api("POST", self._url("memories:generate"), {
                "scope": scope,
                **source,
            })
            op = resp.get("name", "?").split("/")[-1]
            logger.info("Memory: generate LRO started (op=%s, user=%r profile=%r)",
                        op, scope.get("user_id"), scope.get("profile"))
        except Exception as exc:
            logger.warning("Memory: generate failed (user=%r profile=%r): %s",
                           scope.get("user_id"), scope.get("profile"), exc)


# ── File-based fallback ───────────────────────────────────────────────────────

class FileMemoryBackend:
    """Local Markdown file memory for development without GCP.

    Layout:
      ~/.reachy_mini_gemini_live/memory/{user_id}/{profile}/MEMORY.md  — durable facts
      ~/.reachy_mini_gemini_live/memory/{user_id}/{profile}/last.md    — most recent session summary
    """

    def __init__(self, base_dir: Path = _DEFAULT_MEMORY_DIR) -> None:
        self._base = base_dir

    def _profile_dir(self, profile: str, user_id: str | None = None) -> Path:
        u = user_id or get_active_user()
        d = self._base / u / profile
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def load_context(self, profile: str, user_id: str | None = None) -> str:
        parts: list[str] = []
        d = self._profile_dir(profile, user_id)
        for fname in ("MEMORY.md", "last.md"):
            f = d / fname
            if f.exists():
                text = f.read_text(encoding="utf-8").strip()
                if text:
                    parts.append(text)
        return "\n\n".join(parts)

    async def search(self, profile: str, query: str, user_id: str | None = None, top_k: int = 10, with_ids: bool = False) -> list:
        """Naive substring search over the file-backed MEMORY.md as a dev fallback."""
        d = self._profile_dir(profile, user_id)
        f = d / "MEMORY.md"
        if not f.exists():
            return []
        q = query.lower()
        hits = [line.strip().lstrip("- ") for line in f.read_text(encoding="utf-8").splitlines()
                if line.strip() and q in line.lower()]
        results = hits[:top_k]
        if with_ids:
            # File backend has no real IDs — fabricate from line position so forget can work
            return [{"memory_id": f"file://{f}#{i}", "fact": fact, "age_days": None} for i, fact in enumerate(results)]
        return results

    async def list_memories(self, profile: str, user_id: str | None = None, page_size: int = 100) -> list[dict]:
        return []

    async def delete_memory(self, memory_name: str) -> bool:
        logger.info("FileMemoryBackend: delete_memory is a no-op (file backend)")
        return False

    async def purge_expired(self, profile: str, user_id: str | None = None) -> int:
        return 0

    async def compact_memories(self, profile: str, user_id: str | None = None, dry_run: bool = True, max_actions: int = 25) -> dict:
        return {"analyzed": 0, "proposed": [], "applied": 0, "summary": "FileMemoryBackend does not support compaction"}

    async def save_session(self, profile: str, transcript: list[dict], user_id: str | None = None) -> None:
        if not transcript:
            return
        d = self._profile_dir(profile, user_id)
        lines = [f"## Session — {date.today().isoformat()}"]
        for t in transcript:
            role = t.get("role", "?")
            content = t.get("content", "").strip()
            if content:
                lines.append(f"**{role}:** {content}")
        (d / "last.md").write_text("\n\n".join(lines) + "\n", encoding="utf-8")

    async def remember(self, profile: str, fact: str, user_id: str | None = None, lifetime_days: int | None = None) -> None:
        d = self._profile_dir(profile, user_id)
        memory_file = d / "MEMORY.md"
        existing = memory_file.read_text(encoding="utf-8") if memory_file.exists() else ""
        if fact not in existing:
            suffix = ""
            if lifetime_days and lifetime_days > 0:
                suffix = f"  <!-- ttl_until={(datetime.now(timezone.utc) + _td(days=lifetime_days)).strftime('%Y-%m-%d')} -->"
            with memory_file.open("a", encoding="utf-8") as f:
                if not existing:
                    f.write("## What I remember\n")
                f.write(f"- {fact}{suffix}\n")


# ── Factory ───────────────────────────────────────────────────────────────────

def create_memory_backend() -> MemoryBackend:
    """Return VertexMemoryBackend if GCP env vars are set, else FileMemoryBackend."""
    if os.environ.get("GCP_PROJECT_ID") and os.environ.get("VERTEX_REASONING_ENGINE_ID"):
        try:
            backend = VertexMemoryBackend()
            logger.info(
                "Memory: Vertex AI Memory Bank (project=%s, engine=%s)",
                backend.project, backend.engine_id,
            )
            return backend
        except Exception as exc:
            logger.warning("Vertex Memory Bank init failed (%s) — falling back to files", exc)
    logger.info("Memory: file-based fallback (~/.reachy_mini_gemini_live/memory/)")
    return FileMemoryBackend()
