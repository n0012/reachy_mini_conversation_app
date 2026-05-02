# Reachy Mini Conversation App — Fork & Enhancement Plan

Fork of [`pollen-robotics/reachy_mini_conversation_app`](https://huggingface.co/spaces/pollen-robotics/reachy_mini_conversation_app), targeting the Reachy Mini robot at `10.0.0.60` (currently `10.0.0.137` pending DHCP fix).

## Goals

1. **Gemini-only** — strip OpenAI entirely
2. **Better voices** — Gemini Live's 5 prebuilt voices (Puck, Charon, Kore, Fenrir, Aoede)
3. **Google Search grounding** — real-time facts via Live API tool config
4. **MCP tool support** — Model Context Protocol servers per-profile
5. **Conversation memory** — persistent via [Gemini Interactions API](https://ai.google.dev/gemini-api/docs/interactions) (native, no Mem0/Qdrant)

---

## Architecture Overview

The upstream is cleanly separated — two handler classes (`openai_realtime.py`, `gemini_live.py`) with a common interface; we delete one and extend the other.

Key files in upstream:
| File | Role |
|---|---|
| `src/reachy_mini_conversation_app/main.py` | Entrypoint, picks handler via `is_gemini_model()` |
| `src/reachy_mini_conversation_app/config.py` | `BACKEND_PROVIDER`, `MODEL_NAME`, voice defaults |
| `src/reachy_mini_conversation_app/openai_realtime.py` | **Delete in Phase 1** |
| `src/reachy_mini_conversation_app/gemini_live.py` | Gemini handler; `_build_live_config()` builds `LiveConnectConfig` |
| `src/reachy_mini_conversation_app/tools/core_tools.py` | `Tool` ABC — reuse for MCP adapters |
| `src/reachy_mini_conversation_app/tools/__init__.py` | 3-tier loader: profile → core → external dir |
| `profiles/{name}/{instructions,tools,voice}.txt` | Profile bundles; `voice.txt` already accepts Gemini names |
| `src/reachy_mini_conversation_app/prompts.py` | `_expand_prompt_includes()` — reuse for memory injection |
| `src/reachy_mini_conversation_app/console.py` | Shared handler abstraction; transcripts in-memory only |

SDK: `google-genai >= 1.0.0` (new SDK, not legacy `google-generativeai`).

---

## Phase 0 — Spike: Interactions API + Live API compatibility (1–2 hrs)

**One architectural unknown before Phase 4 can be designed:** does `previous_interaction_id` work inside `LiveConnectConfig`, or only with the standard text API?

Throwaway script: `spikes/interactions_live.py`
1. Create an Interaction via `client.interactions.create()` with text turns
2. Open `client.aio.live.connect(...)`, try `previous_interaction_id` in `LiveConnectConfig`
3. Verify model recalls prior context; check if Live session emits an `interaction_id` on close
4. Also test: does `google_search` coexist with `function_declarations` in the same `tools` list?

**Do not start Phase 4 until this resolves.** Outcome selects B1 or B2 (see Phase 4).

---

## Phase 1 — Fork & Strip OpenAI

### Setup
```bash
cd /Users/nick/dev/github/n0012/personal/n0012-monorepo/reachymini
git clone https://huggingface.co/spaces/pollen-robotics/reachy_mini_conversation_app .
git remote rename origin upstream
# Pull upstream bug fixes later: git fetch upstream && git merge upstream/main
```

### Edits
- **Delete** `src/reachy_mini_conversation_app/openai_realtime.py`
- `main.py` — drop `is_gemini_model()` branch; always instantiate `GeminiLiveHandler`
- `config.py` — remove `BACKEND_PROVIDER`, `OPENAI_API_KEY`, `DEFAULT_MODEL_NAME_BY_BACKEND`, `DEFAULT_VOICE_BY_BACKEND`; collapse to single `DEFAULT_MODEL` (env-overridable) and `DEFAULT_VOICE`; add stubs: `GOOGLE_SEARCH_GROUNDING`, `MCP_SERVERS_CONFIG`, `MEMORY_DB_PATH` (default `~/.reachy_mini/memory.sqlite`)
- `pyproject.toml` — remove `openai >= 2.30.0`; add `mcp >= 1.0`, `aiosqlite`
- `.env.example` — remove `BACKEND_PROVIDER`, `OPENAI_API_KEY`; add `GEMINI_API_KEY` explanation
- `gradio_personality.py` — remove backend switcher UI

**Ship checkpoint:** deploy to robot, verify 30s voice conversation with Gemini.

---

## Phase 2 — Voice Selection

Voice selection is mostly done (profiles already support `voice.txt`). Just add validation + UI.

**New file:** `src/reachy_mini_conversation_app/voices.py`
```python
SUPPORTED_GEMINI_VOICES = {"Puck", "Charon", "Kore", "Fenrir", "Aoede"}

def validate_voice(name: str) -> str:
    if name not in SUPPORTED_GEMINI_VOICES:
        raise ValueError(f"Unknown voice {name!r}. Valid: {sorted(SUPPORTED_GEMINI_VOICES)}")
    return name
```

**Edits:**
- Profile loader — call `validate_voice()` when reading `voice.txt`; fail at load time (not mid-conversation)
- `gradio_personality.py` — replace text field with `gr.Dropdown(choices=sorted(SUPPORTED_GEMINI_VOICES))`
- `README.md` — voice persona table:

| Voice | Character |
|---|---|
| Puck | Playful, energetic |
| Charon | Deep, measured |
| Kore | Warm, friendly |
| Fenrir | Strong, assertive |
| Aoede | Melodic, expressive |

---

## Phase 3 — Google Search Grounding

Per-profile flag: `profiles/{name}/grounding.txt` containing `google_search=true`. Global fallback: `GOOGLE_SEARCH_GROUNDING=1` env var. Per-profile is preferred — bedtime-story and role-play profiles should not search the web.

**Edit in `gemini_live.py::_build_live_config()`:**
```python
tools_config = [{"function_declarations": function_declarations}]
if profile.grounding_google_search:
    tools_config.append({"google_search": {}})
```

The Phase 0 spike verifies `google_search` coexists with `function_declarations`. If not, fall back to two separate `LiveConnectConfig` modes selected per-session.

---

## Phase 4 — Conversation Memory (depends on Phase 0 spike)

**New file:** `src/reachy_mini_conversation_app/memory.py` — `MemoryStore` backed by aiosqlite at `~/.reachy_mini/memory.sqlite`.

### B1 — If Interactions API integrates natively with Live API
Schema: `interactions(profile TEXT, interaction_id TEXT, created_at, last_used)`

- Session start: look up most recent `interaction_id` for active profile within a 7-day rolling window; pass via `LiveConnectConfig`
- Session end: insert the new `interaction_id` returned by Live
- One rolling interaction per profile (simple, gives "Reachy remembers yesterday" without topic-clustering)

### B2 — If native integration doesn't work
Additional table: `transcripts(interaction_id, role, text, ts)`

- Hook into the existing transcript debounce buffer in `gemini_live.py` (already exists for VAD); flush completed turns to `client.interactions.append()`
- Session start: fetch last interaction summary via Interactions API; inject via a new `[system/recent_memory]` template token in `prompts.py` (reuses `_expand_prompt_includes()`)

### Either branch
- `gradio_personality.py` gets a "Forget last conversation" button that nulls the latest row for the active profile
- Memory is scoped per-profile — the `noir_detective` profile has separate memory from `bedtime_story`

---

## Phase 5 — MCP Tool Support

**Approach: adapter-at-startup** (not a 4th loader tier). Wrap each MCP tool as a `Tool` subclass, register into the existing 3-tier loader. MCP tools are indistinguishable from native tools at runtime.

**New files:**
- `src/reachy_mini_conversation_app/tools/mcp_adapter.py` — `MCPToolAdapter(Tool)` wrapping one remote MCP tool
- `src/reachy_mini_conversation_app/tools/mcp_client.py` — `MCPClientPool`: one persistent `mcp.ClientSession` per server, lazy-connect, auto-reconnect

**Per-profile config:** `profiles/{name}/mcp_servers.json`
```json
[
  {"name": "filesystem", "transport": "stdio", "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]},
  {"name": "my-robot-tools", "transport": "sse", "url": "http://localhost:8765"}
]
```

**Wire into `tools/__init__.py`:** after existing 3 tiers, iterate `MCPClientPool.list_tools()` and register `MCPToolAdapter` instances. MCP schemas are JSON Schema — extend `_openai_tool_specs_to_gemini()` in `gemini_live.py` to handle them.

**Lifecycle:** pool starts in `main.py` before handler instantiation; closes on shutdown signal. Background task refreshes tool list every 60s.

---

## Risks & Unknowns

| Risk | Mitigation |
|---|---|
| Live + Interactions API compatibility | Phase 0 spike resolves before Phase 4 |
| `google_search` + `function_declarations` coexistence | Phase 0 spike tests this too |
| Preview model voice restrictions | Verify all 5 voices work with `gemini-3.1-flash-live-preview` |
| MCP stdio servers on aarch64 Linux | Use SSE transport for Node-based servers if arm64 binaries unavailable |
| DHCP reservation (robot at .137 not .60) | eth0 MAC (`88:a2:9e:8c:bb:e7`) was used; should be wlan0 (`88:a2:9e:8c:bb:e8`); fix router config |
