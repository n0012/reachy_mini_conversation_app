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
from datetime import datetime, timezone


logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

MEMORY_SEARCH_QUERY = "What do I know about this user, their preferences, and our recent conversations?"
_DEFAULT_MEMORY_DIR = Path.home() / ".reachy_mini" / "memory"
_ACTIVE_USER_FILE = Path.home() / ".reachy_mini" / "active_user.json"
DEFAULT_USER_ID = "unknown"

# Recency-boost re-ranking parameters. Vertex Memory Bank's :retrieve API
# only ranks by similarity (no native time-weighting), so we fetch a wider
# candidate pool and re-score client-side combining similarity + recency:
#   score = similarity + RECENCY_ALPHA * exp(-age_days / RECENCY_TAU_DAYS)
# Tuned so a fact from yesterday outranks a slightly more similar fact from
# 6 months ago, but old facts still surface when nothing recent matches.
RECENCY_ALPHA = 0.15        # weight of recency bonus relative to similarity (0..1)
RECENCY_TAU_DAYS = 30.0     # half-life-ish: bonus decays to ~37% at this age
MAX_DISTANCE = 1.5          # client-side filter on similarity distance
DEFAULT_FETCH_K = 50        # over-fetch this many before re-rank
DEFAULT_TOP_K = 20          # return this many after re-rank


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

    async def search(self, profile: str, query: str, user_id: str | None = None, top_k: int = 10) -> list[str]:
        """Mid-conversation recall — return up to top_k facts matching the query."""
        ...

    async def save_session(self, profile: str, transcript: list[dict], user_id: str | None = None) -> None:
        """Persist a completed session transcript (fire-and-forget is fine)."""
        ...

    async def remember(self, profile: str, fact: str, user_id: str | None = None) -> None:
        """Store an explicit fact the model decided to keep."""
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
    ) -> list[str]:
        """Retrieve facts for a query, distance-filter, recency-boost, return top facts.

        Vertex Memory Bank :retrieve only ranks by similarity. We over-fetch
        and then re-rank with: score = (1 - distance) + α * exp(-age_days / τ).
        Recent facts surface naturally even when an older fact is slightly
        more similar; old facts still win when nothing recent matches.
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
        kept = [m for m in all_memories if m.get("distance") is None or m.get("distance") <= MAX_DISTANCE]
        if len(kept) < len(all_memories):
            logger.info(
                "Memory: filtered %d low-quality results (maxDistance=%.1f)",
                len(all_memories) - len(kept), MAX_DISTANCE,
            )
        if not kept:
            return []

        ranked = self._rerank_with_recency(kept)[:top_k]
        return [m["memory"]["fact"] for m in ranked if m.get("memory", {}).get("fact")]

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
    ) -> list[str]:
        """Mid-conversation recall. Returns up to `top_k` facts matching `query`.

        Used by the `recall` tool — Gemini can request additional memories
        when the initial session-start dump didn't include something it needs.
        """
        facts = await self._retrieve(
            profile, query, user_id=user_id,
            fetch_k=max(top_k * 2, 20),
            top_k=top_k,
        )
        scope_user = user_id or get_active_user()
        logger.info(
            "Memory: search returned %d facts (user=%r profile=%r query=%r)",
            len(facts), scope_user, profile, query[:80],
        )
        return facts

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

    async def remember(self, profile: str, fact: str, user_id: str | None = None) -> None:
        """Store an explicit fact. Awaited so the call completes."""
        scope = self._scope(profile, user_id)
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._generate_memories(scope, {
                "direct_memories_source": {"direct_memories": [{"fact": fact}]},
                "revision_labels": {"source": "remember-tool"},
            }),
        )

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
      ~/.reachy_mini/memory/{user_id}/{profile}/MEMORY.md  — durable facts
      ~/.reachy_mini/memory/{user_id}/{profile}/last.md    — most recent session summary
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

    async def search(self, profile: str, query: str, user_id: str | None = None, top_k: int = 10) -> list[str]:
        """Naive substring search over the file-backed MEMORY.md as a dev fallback."""
        d = self._profile_dir(profile, user_id)
        f = d / "MEMORY.md"
        if not f.exists():
            return []
        q = query.lower()
        hits = [line.strip().lstrip("- ") for line in f.read_text(encoding="utf-8").splitlines()
                if line.strip() and q in line.lower()]
        return hits[:top_k]

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

    async def remember(self, profile: str, fact: str, user_id: str | None = None) -> None:
        d = self._profile_dir(profile, user_id)
        memory_file = d / "MEMORY.md"
        existing = memory_file.read_text(encoding="utf-8") if memory_file.exists() else ""
        if fact not in existing:
            with memory_file.open("a", encoding="utf-8") as f:
                if not existing:
                    f.write("## What I remember\n")
                f.write(f"- {fact}\n")


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
    logger.info("Memory: file-based fallback (~/.reachy_mini/memory/)")
    return FileMemoryBackend()
