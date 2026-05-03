"""Phase 0 spike: Vertex AI Memory Bank REST API.

Tests:
1. Create a Reasoning Engine (one-time — skipped if VERTEX_REASONING_ENGINE_ID is set)
2. Ingest conversation events via memories:generate
3. Store a direct fact via memories:generate (the 'remember' tool path)
4. Retrieve memories via memories:retrieve
5. Delete a memory
6. List all memories

Records findings in spikes/FINDINGS.md.

Usage:
    export GCP_PROJECT_ID=losiern-vibe
    export GCP_LOCATION=us-central1
    python spikes/memory_bank_spike.py

    # Or with existing engine:
    VERTEX_REASONING_ENGINE_ID=123456789 python spikes/memory_bank_spike.py
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path


# ── Config ─────────────────────────────────────────────────────────────────

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "losiern-vibe")
LOCATION   = os.environ.get("GCP_LOCATION", "us-central1")
ENGINE_ID  = os.environ.get("VERTEX_REASONING_ENGINE_ID", "")  # set to skip create

BASE = f"https://{LOCATION}-aiplatform.googleapis.com/v1beta1"
PARENT_PROJECT = f"projects/{PROJECT_ID}/locations/{LOCATION}"


# ── Auth ────────────────────────────────────────────────────────────────────

def get_token() -> str:
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def api(method: str, path: str, body: dict | None = None) -> dict:
    token = get_token()
    url = f"{BASE}/{path}"
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
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        print(f"  HTTP {e.code}: {body_text[:500]}")
        raise


# ── Helpers ─────────────────────────────────────────────────────────────────

findings: list[str] = []

def log(msg: str) -> None:
    print(msg)
    findings.append(msg)

def section(title: str) -> None:
    line = f"\n{'='*60}\n{title}\n{'='*60}"
    log(line)


# ── Steps ───────────────────────────────────────────────────────────────────

def create_reasoning_engine() -> str:
    log("Creating Reasoning Engine (Memory Bank host)...")
    resp = api("POST", f"{PARENT_PROJECT}/reasoningEngines", {
        "display_name": "reachy-memory-spike",
        "spec": {
            "memoryBankConfig": {}
        }
    })
    log(f"  Response: {json.dumps(resp, indent=2)[:500]}")

    # May return an LRO — poll for it
    if "name" in resp and "operations" in resp["name"]:
        op_name = resp["name"].split("/")[-1]
        log(f"  LRO detected: {op_name} — polling...")
        for _ in range(30):
            time.sleep(5)
            op = api("GET", resp["name"].lstrip("/"))
            if op.get("done"):
                engine = op.get("response", op)
                log(f"  Engine ready: {json.dumps(engine, indent=2)[:300]}")
                return engine.get("name", "").split("/")[-1]
            log(f"  Still creating... ({op.get('metadata', {}).get('progressPercentage', '?')}%)")
        raise RuntimeError("Timed out waiting for Reasoning Engine")

    # Synchronous response
    name = resp.get("name", "")
    engine_id = name.split("/")[-1]
    log(f"  Engine ID: {engine_id}")
    return engine_id


def test_ingest_conversation(parent: str) -> None:
    section("TEST 1: Ingest conversation events (direct_contents_source)")
    body = {
        "scope": {"user_id": "nick", "profile": "default"},
        "direct_contents_source": {
            "events": [
                {"content": {"role": "user",  "parts": [{"text": "My name is Nick and I work at Google."}]}},
                {"content": {"role": "model", "parts": [{"text": "Nice to meet you Nick! I'll remember that."}]}},
                {"content": {"role": "user",  "parts": [{"text": "I have a son named Zach and I prefer concise answers."}]}},
                {"content": {"role": "model", "parts": [{"text": "Got it — Zach, and concise. Noted!"}]}},
            ]
        },
    }
    resp = api("POST", f"{parent}/memories:generate", body)
    log(f"  Result: {json.dumps(resp, indent=2)[:400]}")
    log("  ✓ Conversation ingested")


def test_store_direct_fact(parent: str) -> str:
    section("TEST 2: Store direct fact (remember tool path)")
    body = {
        "scope": {"user_id": "nick", "profile": "default"},
        "direct_memories_source": {
            "direct_memories": [
                {"fact": "Nick drinks black coffee, no sugar."}
            ]
        },
    }
    resp = api("POST", f"{parent}/memories:generate", body)
    log(f"  Result: {json.dumps(resp, indent=2)[:400]}")
    log("  ✓ Direct fact stored")
    # Try to get the memory ID from the response for later deletion test
    memories = resp.get("memories", [])
    if memories:
        return memories[0].get("name", "").split("/")[-1]
    return ""


def test_retrieve(parent: str) -> None:
    section("TEST 3: Retrieve memories (session-start path)")
    # Wait a moment for ingestion to complete server-side
    log("  Waiting 3s for ingestion to settle...")
    time.sleep(3)

    body = {
        "scope": {"user_id": "nick", "profile": "default"},
        "similarity_search_params": {"top_k": 10},
    }
    resp = api("POST", f"{parent}/memories:retrieve", body)
    memories = resp.get("memories", [])
    log(f"  Retrieved {len(memories)} memories:")
    for m in memories:
        fact = m.get("memory", {}).get("fact", m.get("fact", "?"))
        log(f"    - {fact}")
    log("  ✓ Retrieval works" if memories else "  ✗ No memories returned — check ingestion")


def test_list(parent: str) -> None:
    section("TEST 4: List all memories")
    resp = api("GET", f"{parent}/memories?pageSize=20")
    memories = resp.get("memories", [])
    log(f"  Total stored: {len(memories)}")
    for m in memories:
        log(f"    {m.get('name','?').split('/')[-1]}: {str(m)[:100]}")


def test_delete(parent: str, memory_id: str) -> None:
    section("TEST 5: Delete a memory")
    if not memory_id:
        log("  Skipped — no memory ID from direct fact test")
        return
    resp = api("DELETE", f"{parent}/memories/{memory_id}")
    log(f"  Result: {resp}")
    log("  ✓ Delete works")


def write_findings(engine_id: str, success: bool) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    status = "✅ PASSED" if success else "❌ FAILED"

    content = f"""# Phase 0 Spike Findings — Memory Bank
Generated: {ts}
Status: {status}

## Config used
- Project: {PROJECT_ID}
- Location: {LOCATION}
- Reasoning Engine ID: {engine_id}

## Results

"""
    content += "\n".join(findings)
    content += f"""

## Architecture decision

Based on this spike:
- Memory Bank Reasoning Engine: **{"works" if success else "blocked"}**
- REST API path: `{PARENT_PROJECT}/reasoningEngines/{engine_id}/memories`
- Auth: gcloud ADC (Bearer token)
- Python integration: stdlib `urllib.request` + `subprocess gcloud auth print-access-token`
  OR `google-auth` library for production (no CLI dependency)

## Phase 4 implementation path

1. Add `google-auth` + `httpx` to `pyproject.toml`
2. Implement `VertexMemoryBackend` in `memory.py` using the REST endpoints above
3. Ingest transcripts after each Live session via `memories:generate`
4. Load context at session start via `memories:retrieve` → inject into `system_instruction`
5. Expose `remember` tool using `direct_memories_source` path

## env vars for .env
```
GCP_PROJECT_ID={PROJECT_ID}
GCP_LOCATION={LOCATION}
VERTEX_REASONING_ENGINE_ID={engine_id}
```
"""
    out = Path(__file__).parent / "FINDINGS.md"
    out.write_text(content)
    print(f"\nFindings written to {out}")


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    section("Phase 0 Spike: Vertex AI Memory Bank")
    log(f"Project: {PROJECT_ID}  Location: {LOCATION}")

    # Step 1: get or create engine
    engine_id = ENGINE_ID
    if not engine_id:
        engine_id = create_reasoning_engine()
        log(f"\n⚠️  Add to .env: VERTEX_REASONING_ENGINE_ID={engine_id}")
    else:
        log(f"Using existing engine: {engine_id}")

    parent = f"{PARENT_PROJECT}/reasoningEngines/{engine_id}"
    log(f"Parent path: {parent}")

    success = True
    memory_id = ""
    try:
        test_ingest_conversation(parent)
        memory_id = test_store_direct_fact(parent)
        test_retrieve(parent)
        test_list(parent)
        test_delete(parent, memory_id)
        section("ALL TESTS PASSED ✅")
        log(f"Add to .env:\n  GCP_PROJECT_ID={PROJECT_ID}\n  GCP_LOCATION={LOCATION}\n  VERTEX_REASONING_ENGINE_ID={engine_id}")
    except Exception as e:
        success = False
        section(f"SPIKE FAILED ❌: {e}")
        import traceback
        log(traceback.format_exc())

    write_findings(engine_id, success)


if __name__ == "__main__":
    main()
