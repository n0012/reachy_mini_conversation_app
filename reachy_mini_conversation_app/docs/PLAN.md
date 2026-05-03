# Reachy Mini Conversation App — Fork & Enhancement Plan

Fork of [`pollen-robotics/reachy_mini_conversation_app`](https://huggingface.co/spaces/pollen-robotics/reachy_mini_conversation_app), targeting the Reachy Mini robot at `10.0.0.60` (currently `10.0.0.137` pending DHCP fix — needs wlan0 MAC `88:a2:9e:8c:bb:e8` in router reservation, not eth0).

## Inspiration & Sources

| Source | What we took from it |
|---|---|
| [pollen-robotics/reachy_mini_conversation_app](https://huggingface.co/spaces/pollen-robotics/reachy_mini_conversation_app) | Base fork — robot integration, profile system, tool plugin loader, Gemini Live handler |
| [SJang1/ha-gemini-live](https://github.com/SJang1/ha-gemini-live) | Google Search grounding pattern, voice selection (Puck/Charon/Kore/Fenrir/Aoede), native Gemini Live speech-to-speech architecture |
| [Shubhamsaboo/openclaw-vertexai-memorybank](https://github.com/Shubhamsaboo/openclaw-vertexai-memorybank) | Vertex AI Memory Bank REST API surface (`memories:generate`, `memories:retrieve`), scoping model, auto-capture/auto-recall pattern |
| [philschmid.de/gemini-with-memory](https://www.philschmid.de/gemini-with-memory) | Semantic fact extraction vs transcript storage — informed the decision to store extracted facts, not raw logs |
| [docs.openclaw.ai/concepts/memory](https://docs.openclaw.ai/concepts/memory) | `MEMORY.md` durable facts + daily notes structure — inspired the `FileMemoryBackend` fallback and the `remember` tool concept |
| [ai.google.dev/gemini-api/docs/interactions](https://ai.google.dev/gemini-api/docs/interactions) | Researched as primary memory approach; ruled out — Interactions API is incompatible with the Live WebSocket API |
| [google-gemini/cookbook: Get_started_LiveAPI_tools.ipynb](https://github.com/google-gemini/cookbook/blob/main/quickstarts/Get_started_LiveAPI_tools.ipynb) | Official Google example for Live API tools — confirms raw dict format for `google_search`, `function_declarations`, and `code_execution`; shows `google_search` triggers automatically on time-sensitive queries without explicit system prompt |
| [google-gemini/gemini-skills: gemini-live-api-dev SKILL.md](https://github.com/google-gemini/gemini-skills/blob/main/skills/gemini-live-api-dev/SKILL.md) | Official Live API skill — recommends `gemini-3.1-flash-live-preview`, 128k context, 10-minute session limit, enable context compression for sessions >15min, use `send_realtime_input` not `send_client_content`, send `audioStreamEnd` when mic pauses |
| [ai.google.dev/gemini-api/docs/live-tools](https://ai.google.dev/gemini-api/docs/live-tools.md.txt) | Live tools reference — confirms `{"google_search": {}}` prevents hallucinations; function calling is **synchronous only** in Live API (model blocks until tool result returned); `NON_BLOCKING` behavior available for async; Google Maps, code execution, URL context not supported |

## Repo layout

```
reachymini/
  docs/           ← planning docs (this file)
  project/        ← the app (forked from upstream)
    src/
    tests/
    profiles/
    pyproject.toml
    ...
```

## Goals

1. ✅ **Gemini-only** — strip OpenAI entirely *(Phase 1 complete)*
2. **Better voices** — Gemini Live's 5 prebuilt voices (Puck, Charon, Kore, Fenrir, Aoede)
3. **Google Search grounding** — real-time facts via Live API tool config
4. **MCP tool support** — Model Context Protocol servers per-profile
5. **Conversation memory** — persistent via **Vertex AI Memory Bank** (server-side, semantic, no local state)

---

## Architecture Overview

The upstream is cleanly separated — two handler classes with a common interface; we deleted the OpenAI one and extend the Gemini one.

Key files (all paths relative to `project/`):

| File | Role |
|---|---|
| `src/reachy_mini_conversation_app/main.py` | Entrypoint — always instantiates `GeminiLiveHandler` |
| `src/reachy_mini_conversation_app/config.py` | Constants: `AVAILABLE_VOICES`, `DEFAULT_MODEL_NAME`, `DEFAULT_VOICE` |
| `src/reachy_mini_conversation_app/gemini_live.py` | Gemini handler; `_build_live_config()` builds `LiveConnectConfig` |
| `src/reachy_mini_conversation_app/tools/core_tools.py` | `Tool` ABC — reuse for MCP adapters and memory tools |
| `src/reachy_mini_conversation_app/tools/__init__.py` | 3-tier loader: profile → core → external dir |
| `profiles/{name}/{instructions,tools,voice}.txt` | Profile bundles |
| `src/reachy_mini_conversation_app/prompts.py` | `_expand_prompt_includes()` — reuse for memory injection |

SDK: `google-genai >= 1.0.0`.

---

## Phase 0 — Spike (do before Phase 4)

Verify two things before implementing Phase 4:

1. **Google Search + function_declarations coexistence** — historically Gemini restricted mixing built-in and custom tools in the same `tools` list. Spike confirms it works in Live API.
2. **Memory Bank Reasoning Engine setup** — confirm `POST /memories:generate` and `POST /memories:retrieve` work against a freshly created Agent Engine instance.

Script: `project/spikes/memory_bank_spike.py`

---

## Phase 1 — Fork & Strip OpenAI ✅ Complete

- Deleted `openai_realtime.py` and `test_openai_realtime.py`
- Simplified `config.py` to Gemini-only constants
- Removed `BACKEND_PROVIDER`, `OPENAI_API_KEY`, OpenAI voice list
- Added `aiosqlite` and `mcp` deps, removed `openai`
- Added `test_gemini_only_config.py` and `test_voices.py`

**Test:** `PYTHONPATH=project/src uv run --no-project pytest project/tests/test_gemini_only_config.py project/tests/test_voices.py project/tests/test_config_name_collisions.py -v`

---

## Phase 2 — Voice Selection

Voice selection is mostly done (profiles already support `voice.txt`). Add validation + UI.

**New file:** `project/src/reachy_mini_conversation_app/voices.py`
```python
SUPPORTED_GEMINI_VOICES = {"Puck", "Charon", "Kore", "Fenrir", "Aoede"}

def validate_voice(name: str) -> str:
    if name not in SUPPORTED_GEMINI_VOICES:
        raise ValueError(f"Unknown voice {name!r}. Valid: {sorted(SUPPORTED_GEMINI_VOICES)}")
    return name
```

**Edits:**
- Profile loader — call `validate_voice()` at profile load time (fail before WebSocket opens)
- `gradio_personality.py` — replace text field with `gr.Dropdown(choices=sorted(SUPPORTED_GEMINI_VOICES))`
- `README.md` (upstream) — voice persona table

**Tests:** `test_voices.py` has 4 `xfail` stubs that flip to passing once `voices.py` exists.

---

## Phase 3 — Google Search Grounding

Per-profile flag: `profiles/{name}/grounding.txt` containing `google_search=true`. Global fallback: `GOOGLE_SEARCH_GROUNDING=1` env var.

**Edit in `gemini_live.py::_build_live_config()`:**
```python
tools_config = [{"function_declarations": function_declarations}]
if profile.grounding_google_search:
    tools_config.append({"google_search": {}})
```

Phase 0 spike verifies coexistence. If it fails, use two separate `LiveConnectConfig` modes selected per-session.

---

## Phase 4 — Conversation Memory via Vertex AI Memory Bank

### Why Vertex AI Memory Bank

The whole app requires internet (Gemini Live API), so server-side memory adds no new dependency. Benefits:
- Survives robot disk wipes and OS updates
- Semantic similarity retrieval (finds relevant facts without exact match)
- Server-side consolidation and deduplication — no local state to manage
- Same GCP ecosystem as Gemini

### REST API (no Python SDK required — use `google-auth` + `httpx`)

```
Base: https://{location}-aiplatform.googleapis.com/v1beta1
Parent: projects/{project}/locations/{location}/reasoningEngines/{engine_id}
```

| Operation | Call |
|---|---|
| Ingest transcript after session | `POST /{parent}/memories:generate` with `direct_contents_source` |
| Store explicit fact (remember tool) | `POST /{parent}/memories:generate` with `direct_memories_source` |
| Retrieve at session start | `POST /{parent}/memories:retrieve` with `similaritySearchParams` |
| Delete a memory | `DELETE /{parent}/memories/{id}` |

**Auth:** `google.auth` ADC — same credentials already used for Gemini.

### Memory backend abstraction

New file: `project/src/reachy_mini_conversation_app/memory.py`

```python
class MemoryBackend(Protocol):
    async def load_context(self, profile: str) -> str: ...   # injected into system_instruction
    async def save_session(self, profile: str, transcript: list[dict]) -> None: ...
    async def remember(self, profile: str, fact: str) -> None: ...
    async def forget(self, profile: str, memory_id: str) -> None: ...

class VertexMemoryBackend:
    """Calls Vertex AI Memory Bank REST API."""
    ...

class FileMemoryBackend:
    """Fallback: MEMORY.md + last.md for local dev without GCP."""
    ...
```

### What gets stored

**After each Live session ends** (background task, one standard Gemini API call):
- Extract turns from the transcript debounce buffer (already captured in `gemini_live.py`)
- `POST /memories:generate` with `direct_contents_source` → Memory Bank extracts salient facts server-side

**The `remember` tool** (exposed via existing tool plugin system):
- `POST /memories:generate` with `direct_memories_source: {direct_memories: [{fact: "..."}]}`
- Reachy calls this when it learns something worth keeping mid-conversation

**At session start** (`system_instruction` injection):
- `POST /memories:retrieve` with query "What do I know about the user and this robot?" + `topK: 10`
- Format retrieved facts as bullet points, inject into `system_instruction`

### Memory scoping

Scope by profile to isolate personas:
```json
{"scope": {"profile": "default", "user_id": "nick"}}
```

### Required GCP setup (one-time)

```bash
# Enable APIs
gcloud services enable aiplatform.googleapis.com

# Create a Reasoning Engine (Memory Bank lives here)
# Note exact CLI TBD — confirm via spike
gcloud ai reasoning-engines create --display-name="reachy-memory" --location=us-central1

# Note the reasoning engine ID
```

Add to `project/.env.example`:
```
GCP_PROJECT_ID=your-project-id
GCP_LOCATION=us-central1
VERTEX_REASONING_ENGINE_ID=your-engine-id
```

### Session-start injection example

```
[system_instruction]
...existing profile instructions...

## What I remember
- Nick's name is Nick. He works in tech at Google.
- Nick has a son named Zach.
- Nick prefers concise answers.
- Last session: We discussed the backyard renovation starting Friday.
```

### Gradio UI addition
- "Forget last conversation" button → calls `DELETE` on recent memory IDs for the active profile

### Estimated cost
~$8/month at ~50 turns/day (single user). Free tier covers 1,000 retrieval calls/month.

---

## Phase 5 — MCP Tool Support

**Approach: adapter-at-startup.** Wrap each MCP tool as a `Tool` subclass, register into the existing 3-tier loader. MCP tools are indistinguishable from native ones at runtime.

**New files:**
- `project/src/reachy_mini_conversation_app/tools/mcp_adapter.py` — `MCPToolAdapter(Tool)`
- `project/src/reachy_mini_conversation_app/tools/mcp_client.py` — `MCPClientPool` with lazy-connect, auto-reconnect

**Per-profile config:** `profiles/{name}/mcp_servers.json`
```json
[
  {"name": "filesystem", "transport": "stdio", "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]},
  {"name": "custom", "transport": "sse", "url": "http://localhost:8765"}
]
```

Wire into `project/src/reachy_mini_conversation_app/tools/__init__.py`: after existing 3 tiers, register `MCPToolAdapter` instances from `MCPClientPool.list_tools()`.

**Lifecycle:** Pool starts in `main.py` before handler instantiation. Background task refreshes tool list every 60s.

---

## Critical Files to Modify

| File | Phase | What changes |
|---|---|---|
| `project/src/.../main.py` | 4, 5 | Memory backend init, MCP pool start |
| `project/src/.../config.py` | 2, 4 | Voice validation, memory/GCP config |
| `project/src/.../gemini_live.py` | 3, 4 | Grounding in `_build_live_config()`, transcript capture for memory |
| `project/src/.../tools/__init__.py` | 5 | Register MCP adapters |
| `project/src/.../gradio_personality.py` | 2, 4 | Voice dropdown, forget button |
| `project/src/.../prompts.py` | 4 | `[system/recent_memory]` token |
| `project/pyproject.toml` | 4, 5 | Add `google-auth`, `httpx` |

## Existing Code to Reuse

- `Tool` ABC at `project/src/.../tools/core_tools.py` — base for `MCPToolAdapter` and memory tools
- `_expand_prompt_includes()` in `prompts.py` — memory context injection
- 3-tier tool loader in `tools/__init__.py` — register MCP as registry injection, not new tier
- Transcript debounce buffer in `gemini_live.py` — tap for memory ingestion after session

## Verification

```bash
# Local tests (Mac — no robot required)
cd /Users/nick/dev/github/n0012/personal/n0012-monorepo/reachymini
PYTHONPATH=project/src uv run --no-project pytest project/tests/test_gemini_only_config.py project/tests/test_voices.py project/tests/test_config_name_collisions.py -v

# Deploy to robot
ssh pollen@10.0.0.137 "cd ~/reachymini/project && git pull && uv sync"
ssh pollen@10.0.0.137 "cd ~/reachymini/project && reachy-mini-conversation-app"
```

Phase-specific checks:
- **Phase 2**: switch profiles; confirm voice changes; bad `voice.txt` raises at profile load
- **Phase 3**: ask "what's the weather in Denver right now?"; expect grounded answer with citation
- **Phase 4**: have a conversation; restart; ask "what did we talk about?"; expect recall from Memory Bank
- **Phase 5**: configure `mcp_servers.json` with filesystem server; ask Reachy to read a file

## Known Limitations & Future Work

| Issue | Root cause | Potential fix |
|---|---|---|
| Live sports scores go stale on re-ask | `google_search` grounding caches results within a session — re-checking doesn't re-fetch | Phase 6: MCP sports API tool (MLB/ESPN) that hits a live endpoint on every call |
| Session ends after ~10 minutes | Live API hard limit | Session restart is automatic; memory save on close handles continuity |
| `google_search` results not re-fetched mid-session | By design — grounding fetches once per topic per session | For real-time monitoring, use function-calling MCP tool instead of grounding |
| Model version | `gemini-3.1-flash-live-preview` — `gemini-2.5-flash-preview-native-audio-dialog` has richer native audio | Test by setting `MODEL_NAME` env var |
| Function calling is synchronous | Live API limitation — model blocks until tool result returned | Use `NON_BLOCKING` behavior for long-running tools |

## Risks

| Risk | Status |
|---|---|
| Google Search + function_declarations coexistence in Live API | ✅ Works — confirmed in production |
| Vertex AI Memory Bank Reasoning Engine setup / exact CLI | ✅ Resolved — engine `YOUR_ENGINE_ID` on `losiern-vibe` |
| MCP stdio servers on aarch64 Linux (arm64 binaries) | Use SSE transport for Node-based servers |
| DHCP fix (.137 → .60) | ✅ Fixed — wlan0 MAC `88:a2:9e:8c:bb:e8` in router |
