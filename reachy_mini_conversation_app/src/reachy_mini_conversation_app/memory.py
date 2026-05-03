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
import subprocess
import urllib.error
import urllib.request
import asyncio
from datetime import date
from pathlib import Path
from typing import Protocol, runtime_checkable


logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

MEMORY_SEARCH_QUERY = "What do I know about this user, their preferences, and our recent conversations?"
_DEFAULT_MEMORY_DIR = Path.home() / ".reachy_mini" / "memory"


# ── Protocol ─────────────────────────────────────────────────────────────────

@runtime_checkable
class MemoryBackend(Protocol):
    """Interface all memory backends must satisfy."""

    async def load_context(self, profile: str) -> str:
        """Return a text block injected into system_instruction at session start."""
        ...

    async def save_session(self, profile: str, transcript: list[dict]) -> None:
        """Persist a completed session transcript (fire-and-forget is fine)."""
        ...

    async def remember(self, profile: str, fact: str) -> None:
        """Store an explicit fact the model decided to keep."""
        ...


# ── Vertex AI Memory Bank ─────────────────────────────────────────────────────

def _get_token() -> str:
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


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

    def _scope(self, profile: str) -> dict:
        return {"user_id": "default", "profile": profile}

    async def load_context(self, profile: str) -> str:
        """Retrieve relevant memories and format as bullet list."""
        try:
            resp = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _api("POST", self._url("memories:retrieve"), {
                    "scope": self._scope(profile),
                    "similarity_search_params": {
                        "search_query": MEMORY_SEARCH_QUERY,
                        "top_k": 15,
                    },
                }),
            )
            memories = resp.get("retrievedMemories", [])
            if not memories:
                return ""
            facts = [m["memory"]["fact"] for m in memories if m.get("memory", {}).get("fact")]
            if not facts:
                return ""
            bullet_list = "\n".join(f"- {f}" for f in facts)
            return f"## What I remember\n{bullet_list}"
        except Exception as exc:
            logger.warning("Memory load failed: %s", exc)
            return ""

    async def save_session(self, profile: str, transcript: list[dict]) -> None:
        """Fire-and-forget: ingest transcript events. Returns immediately."""
        if not transcript:
            return
        events = [
            {"content": {"role": t["role"], "parts": [{"text": t["content"]}]}}
            for t in transcript
            if t.get("content", "").strip()
        ]
        if not events:
            return
        asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._generate_memories(profile, {"direct_contents_source": {"events": events}}),
        )

    async def remember(self, profile: str, fact: str) -> None:
        """Fire-and-forget: store an explicit fact."""
        asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._generate_memories(profile, {
                "direct_memories_source": {"direct_memories": [{"fact": fact}]}
            }),
        )

    def _generate_memories(self, profile: str, source: dict) -> None:
        try:
            _api("POST", self._url("memories:generate"), {
                "scope": self._scope(profile),
                **source,
            })
        except Exception as exc:
            logger.warning("Memory save failed: %s", exc)


# ── File-based fallback ───────────────────────────────────────────────────────

class FileMemoryBackend:
    """Local Markdown file memory for development without GCP.

    Layout:
      ~/.reachy_mini/memory/{profile}/MEMORY.md   — durable facts
      ~/.reachy_mini/memory/{profile}/last.md     — most recent session summary
    """

    def __init__(self, base_dir: Path = _DEFAULT_MEMORY_DIR) -> None:
        self._base = base_dir

    def _profile_dir(self, profile: str) -> Path:
        d = self._base / profile
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def load_context(self, profile: str) -> str:
        parts: list[str] = []
        d = self._profile_dir(profile)
        for fname in ("MEMORY.md", "last.md"):
            f = d / fname
            if f.exists():
                text = f.read_text(encoding="utf-8").strip()
                if text:
                    parts.append(text)
        return "\n\n".join(parts)

    async def save_session(self, profile: str, transcript: list[dict]) -> None:
        if not transcript:
            return
        d = self._profile_dir(profile)
        lines = [f"## Session — {date.today().isoformat()}"]
        for t in transcript:
            role = t.get("role", "?")
            content = t.get("content", "").strip()
            if content:
                lines.append(f"**{role}:** {content}")
        (d / "last.md").write_text("\n\n".join(lines) + "\n", encoding="utf-8")

    async def remember(self, profile: str, fact: str) -> None:
        d = self._profile_dir(profile)
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
