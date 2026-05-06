---
title: Reachy Mini Gemini Live Edition
emoji: 🎤
colorFrom: blue
colorTo: purple
sdk: static
app_file: index.html
pinned: false
short_description: Gemini Live robot with memory, MCP tools, home control
suggested_storage: large
tags:
 - reachy_mini
 - reachy_mini_python_app
 - gemini
 - vertex-ai
---

# Reachy Mini — Gemini Live Edition

**This app runs directly on your Reachy Mini robot.** Install it from the Pollen launcher on your robot's dashboard, or follow the [quick start](#quick-start) below. This HuggingFace Space is the distribution point — the code executes on the robot, not in the cloud.

![Reachy Mini Dance](docs/assets/reachy_mini_dance.gif)

A fork of the [Pollen Robotics conversation app](https://huggingface.co/spaces/pollen-robotics/reachy_mini_conversation_app) rebuilt around the Gemini Live API with persistent per-user memory, profile-scoped MCP tools, and an offline wake word — all managed from a headless browser UI on the robot.

---

## What's new vs the base app

| Feature | Base app | This fork |
|---|---|---|
| AI backend | HF / OpenAI / Gemini | Gemini Live only (`gemini-3.1-flash-live-preview`) |
| Persistent memory | ✗ | ✅ Vertex AI Agent Engine — per-user, cross-session |
| MCP tool ecosystem | ✗ | ✅ Per-profile servers with secure credential injection |
| Home Assistant | ✗ | ✅ Full home control via HA MCP (lights, climate, media, locks) |
| Wake word | ✗ | ✅ Edge Impulse "Hey Reachy" — offline, on-device, with rooster crow ack on detect |
| Sleep mode | ✗ | ✅ Voice ("good night"), button, or auto-sleep after configurable idle (1–60 min, default 10) — zero API cost |
| Live monitor | ✗ | ✅ Browser camera feed + real-time transcript stream |
| Settings UI | Basic | ✅ Full headless web app — setup wizard, profile editor, env vars |
| Google Search | Global flag | ✅ Per-profile toggle |
| Calendar | ✗ | ✅ Read + create via Home Assistant Google Calendar |

---

## How it works

Four Google Cloud pillars power this app:

```
┌─────────────────────────────────────────────────────┐
│                  Reachy Mini Robot                  │
│                                                     │
│  Microphone → Gemini Live API → Speaker             │
│               (gemini-3.1-flash-live-preview)       │
│               ↕                                     │
│  ┌────────────────────┐  ┌─────────────────────┐   │
│  │  Vertex AI Agent   │  │   MCP Tool Servers  │   │
│  │  Engine Memory     │  │  Home Assistant     │   │
│  │  (per-user facts)  │  │  Weather, Calendar  │   │
│  └────────────────────┘  └─────────────────────┘   │
│               ↕                                     │
│  ┌────────────────────┐                             │
│  │  Google Search     │                             │
│  │  Grounding         │                             │
│  │  (per-profile)     │                             │
│  └────────────────────┘                             │
└─────────────────────────────────────────────────────┘
```

---

## Quick start

The easiest path is the Reachy Mini launcher dashboard — install this Space directly without any SSH:

1. Open the launcher at `http://reachy-mini.local` (or the robot's IP)
2. Apps → Available → install **`reachy_mini_conversation_app_gemini`**
3. Click Launch → the in-browser setup wizard at `http://ROBOT_IP:7860` collects your Gemini API key, GCP project, Agent Engine ID, and SA JSON. Stored at `~/.reachy_mini/.env` (survives reinstalls).

For local development with rsync deploys, see the `Deploying` section below.

### System requirements

Reachy Mini OS already ships with portaudio (`portaudio19-dev`, `libportaudio2`, `python3-pyaudio`). The pip deps (`PyAudio`, `sounddevice`, `scipy`, `edge-impulse-linux`) install automatically from `pyproject.toml`.

`espeak-ng` (used for the spoken setup-URL announcement on first launch) is **auto-installed via `sudo apt-get install`** on first startup if missing — the `pollen` user on Reachy Mini OS has passwordless sudo so this is silent. On other Linux hosts where sudo prompts, the announcement gracefully no-ops and the wizard still works normally.

For non-Reachy hosts (or if you prefer explicit control):
```bash
sudo apt-get install -y portaudio19-dev libportaudio2 espeak-ng
```

`packages.txt` lists the same apt deps for HuggingFace Spaces' build pipeline — note that **it's only honored for Gradio/Streamlit SDKs**, not `sdk: static`. The Reachy Mini launcher uses `static`, hence the runtime auto-install fallback.

---

## Authentication — two keys required

This app uses two separate Google authentication paths:

| | **Gemini API Key** | **Vertex AI (Agent Engine)** |
|---|---|---|
| What it's for | Gemini Live API (voice/video streaming) | Memory Bank read/write |
| Where to get it | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | GCP service account key |
| Why separate? | `gemini-3.1-flash-live-preview` is only available via AI Studio endpoint — not Vertex AI |
| Auth method | `GEMINI_API_KEY` env var | Service account JSON (pasted in setup wizard) |

> **Note on auth roadmap:** The service account key approach is functional but requires manual rotation. OAuth / ADC is being considered as a future alternative — contributions welcome.

### GCP setup (one-time)

**1. Enable APIs**
```bash
gcloud services enable aiplatform.googleapis.com --project=YOUR_PROJECT_ID
```

**2. Create service account**
```bash
export PROJECT_ID=YOUR_PROJECT_ID

gcloud iam service-accounts create reachy-mini \
  --display-name="Reachy Mini" \
  --project=$PROJECT_ID

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:reachy-mini@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user" \
  --condition=None
```

**3. Download the key**
```bash
gcloud iam service-accounts keys create reachy-mini-sa-key.json \
  --iam-account=reachy-mini@${PROJECT_ID}.iam.gserviceaccount.com \
  --project=$PROJECT_ID
```

> If key creation is blocked by an org policy, see [docs/GCP_ORG_POLICY.md](docs/GCP_ORG_POLICY.md).

**4. Create a Reasoning Engine instance (Memory Bank)**
```bash
curl -X POST \
  "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/${PROJECT_ID}/locations/us-central1/reasoningEngines" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -d '{"displayName": "reachy-memory", "spec": {}}'
```

Note the `name` field in the response — the numeric ID at the end is your `VERTEX_REASONING_ENGINE_ID`.

Paste the service account key JSON into the setup wizard at `http://ROBOT_IP:7860`.

---

## Persistent Memory — Vertex AI Agent Engine

Memory is backed by [Vertex AI Agent Engine](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview) and follows a three-stage flow on every session:

```
Session start          During session         Session end
──────────────         ──────────────         ───────────
Auto-recall            Auto-capture           Auto-save
─────────────          ────────────           ──────────
Semantic search        Model calls            Full transcript
retrieves relevant →   remember() to   →      ingested and
facts for this user    store new facts         deduplicated
and injects them       silently mid-           into memory bank
into system prompt     conversation
```

**Retrieval is semantic, not keyword-based.** At session start, the memory bank runs a vector similarity search across everything known about the current user. Facts that are contextually relevant to recent conversations surface first — the model isn't just reciting a list, it's recalling what matters right now.

**Key properties:**
- **Per-user × per-profile scope** — memories partition by both `user_id` and `profile`. A user's memories under a "home assistant" profile are completely separate from their memories under a "work assistant" profile. `identify_user` switches scope mid-conversation so the right context is always active.
- **Facts, not transcripts** — the memory bank stores extracted facts ("prefers oat milk", "working on bathroom renovation", "daughter's recital is Friday"), not raw conversation logs. Storage is efficient; retrieval is precise.
- **Enables multi-user assistants** — every household member or team member gets their own persistent context. Reachy greets each person by name, picks up where you left off, and keeps each person's preferences private from others.
- **Opt-in per profile** — add `enabled=true` to `profiles/{name}/memory.txt` to enable. Profiles without this file run stateless (useful for demo, kiosk, or shared-screen contexts).
- **Mid-session saves** — every 20 turns, a background save runs so long sessions aren't lost if the robot is powered off.
- **Explicit `remember` tool** — the model can call `remember("fact")` at any point to explicitly store something, giving it agency over its own long-term knowledge.

### Use cases unlocked by per-user × per-profile memory

| Deployment | How memory helps |
|---|---|
| **Family home** | Each family member gets their own preferences, schedule awareness, and conversation continuity. The robot knows who it's talking to from the camera, loads the right memories, and picks up mid-conversation. |
| **Small business** | Different staff profiles (sales, support, ops) with different tool access. Each employee's context is private and persistent — the robot remembers their ongoing projects. |
| **Reception / lobby** | A "visitor" profile with memory disabled for privacy. A "staff" profile with full memory for recurring colleagues. |
| **Personal productivity** | One profile for work (calendar, task tools, no home control), one for home (HA, music, family context). Same robot, different persona, separate memory. |

Memory is enabled for the included `reachy-gemini` reference profile.

---

## MCP Tool Ecosystem

Each profile can define its own set of MCP servers in `profiles/{name}/mcp_servers.json`. Three transports are supported:

| Transport | Use case |
|---|---|
| `stdio` | Local scripts (weather, calendar) |
| `streamablehttp` | Remote services (Home Assistant) |
| `sse` | SSE-based MCP servers |

**Credential injection** — use `${ENV_VAR_NAME}` in header values. The app substitutes environment variables at connection time. Store secrets via the **Environment Variables** panel in the settings UI at `http://ROBOT_IP:7860`.

Example `mcp_servers.json`:
```json
[
  {
    "name": "weather",
    "transport": "stdio",
    "command": ["/venvs/apps_venv/bin/python3", "/path/to/weather_server.py"]
  },
  {
    "name": "home_assistant",
    "transport": "streamablehttp",
    "url": "http://homeassistant.local:8123/api/mcp",
    "headers": {
      "Authorization": "Bearer ${HA_MCP_TOKEN}"
    }
  }
]
```

---

## Google Search Grounding

Gemini Live supports native Google Search grounding — when enabled, the model retrieves live search results and cites them inline, rather than relying on training data that may be months out of date.

**Why this matters for a physical assistant:**
- Weather, sports scores, news, stock prices, flight status — all require real-time data
- A robot that confidently gives stale answers is worse than one that says "let me check"
- With grounding enabled, the model is instructed to search before answering any time-sensitive question and briefly signals when it does: "Let me check that." → answer

**Per-profile toggle** — grounding is configured per profile in `profiles/{name}/grounding.txt`:
```
google_search=true
```

This means you can have one profile with grounding on (a real-time assistant that searches everything) and another with it off (a focused persona that stays on-topic without web distractions). A demo profile for a trade show might disable it entirely; a personal assistant profile for daily use should always have it on.

**What it grounds:** Any query the model determines is time-sensitive or factual — news, weather, sports, current prices, local business hours, transit status. The model decides when to invoke search, not a keyword filter.

> **Limitation:** Search results are cached per topic within a session. If you ask the same question twice, the model uses the cached grounding data. For endpoints that change faster than a session (e.g. live stock ticks), use an MCP function tool instead.

---

## Home Assistant Integration

The `reachy-gemini` reference profile includes a full Home Assistant integration — the strongest consumer use case for a robot assistant in a smart home.

**What it can control:**
- 💡 Lights (individual rooms, scenes, schedules)
- 🌡️ Climate (thermostats, temperature sensors by room)
- 🎵 Music (Music Assistant + Sonos, with Plex / Apple Music support)
- 🔒 Doors and locks (with confirmation before operating)
- 🪟 Shades and covers
- 🌿 Sprinklers
- 📅 Google Calendar (read + create — edit/delete not supported by HA)
- 📺 Apple TV control

**Setup:**
1. Enable the [Home Assistant MCP Server](https://www.home-assistant.io/integrations/mcp_server/) in HA (`Settings → Devices & Services → Add Integration → Model Context Protocol`)
2. Create a Long-Lived Access Token in HA (`Profile → Security → Long-Lived Access Tokens`)
3. Add `HA_MCP_TOKEN=your_token` in the **Environment Variables** panel at `http://ROBOT_IP:7860`
4. The `reachy-gemini` profile's `mcp_servers.json` handles the rest

The system prompt in `reachy-gemini` shows the full pattern for natural language home control — describing entity IDs, scene mappings, and multi-room audio routing in a way the model handles cleanly without ever exposing entity IDs to the user.

---

## Profiles

Each profile is a folder under `profiles/`:

```
profiles/reachy-gemini/          ← included reference profile
  instructions.txt               — system prompt
  tools.txt                      — enabled tools (one per line)
  voice.txt                      — Gemini voice name
  grounding.txt                  — optional: google_search=true
  memory.txt                     — optional: enabled=true
  mcp_servers.json               — optional: MCP server list
```

Set `REACHY_MINI_CUSTOM_PROFILE=reachy-gemini` in the setup wizard to activate.

The included `reachy-gemini` profile demonstrates all features: identity-first camera flow, multi-user memory scope switching, Home Assistant control, weather, calendar, and Google Search grounding.

---

## Web UI

The app runs a headless settings server at `http://ROBOT_IP:7860` — no Gradio mode needed. All management is done from a browser.

### Setup wizard (first run)
Guides through API key entry, GCP credentials, voice selection, and Agent Engine ID. Stores everything in `~/.reachy_mini/.env`.

### Personality Studio
- Profile picker with live apply and startup-profile persistence
- Full system prompt editor
- Voice selector with audio preview
- Tool checkboxes, MCP server editor, Google Search toggle

### Live Monitor *(new — not in base app)*
Real-time view of what Reachy sees and hears:
- **Camera feed** — 1fps MJPEG stream from the robot's camera, displayed live in the browser
- **Conversation transcript** — SSE stream of everything said (user and Reachy), replays from session start on reconnect
- **Activity log** — tool calls, memory events, search queries in a terminal-style log
- **Wake / Sleep buttons** — manually trigger sleep mode or wake the robot

### Configuration panel
- Update GCP credentials without restarting
- **Behavior** — adjust the auto-sleep idle window (1–60 min, persisted as `REACHY_MINI_AUTO_SLEEP_MINUTES` in `~/.reachy_mini/.env`)
- Environment variable manager — add/edit/delete secrets used by MCP servers (e.g. `HA_MCP_TOKEN`)

---

## Wake Word & Sleep Mode

### Three ways to sleep
1. **Voice** — say "good night", "you can rest now", "I'm done", etc. The `go_to_sleep` tool fires automatically after Gemini speaks a brief goodbye.
2. **Auto-sleep** — after the configured idle window (default 10 min, range 1–60 min, set in the settings UI under **Behavior**).
3. **Button** — POST `/session/sleep` from the live monitor UI.

In all three cases Reachy:
1. Closes the Gemini Live WebSocket — billing stops immediately
2. Moves to a sleep pose and holds it
3. Switches to offline wake word detection (zero cloud cost)

### Wake word
Uses an [Edge Impulse](https://edgeimpulse.com) model trained on real "Hey Reachy" voice samples, from [luisomoreau/hey_reachy_wake_word_detection](https://huggingface.co/spaces/luisomoreau/hey_reachy_wake_word_detection). The `linux-aarch64` binary is bundled — no training required.

The `AudioImpulseRunner` captures audio directly from the robot's microphone (16 kHz native) in a background thread, completely independent of the Gemini audio pipeline. The capture is resampled 16 → 24 kHz to match the model rate via `scipy.signal.resample_poly`. During sleep, `receive()` returns immediately — zero fastrtc processing overhead.

### Wake acknowledgement sound
On detection, Reachy plays a quick rooster crow before reconnecting — instant audible confirmation that "Hey Reachy" was heard. Source: ["Young rooster crowing"](https://commons.wikimedia.org/wiki/File:Young_rooster_crowing.ogg) on Wikimedia Commons, [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) (trimmed, normalized, resampled — see `src/reachy_mini_conversation_app/sounds/CREDITS.md`).

---

## Deploying

### SSH direct deploy
```bash
# Push source from Mac
rsync -avz --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' \
  reachy_mini_conversation_app/src/reachy_mini_conversation_app/ \
  pollen@ROBOT_IP:~/dev/reachy_mini_conversation_app/reachy_mini_conversation_app/src/reachy_mini_conversation_app/

# Sync profiles
rsync -avz \
  reachy_mini_conversation_app/profiles/ \
  pollen@ROBOT_IP:~/dev/reachy_mini_conversation_app/reachy_mini_conversation_app/profiles/
```

### Git pull deploy
```bash
sshpass -p 'root' ssh pollen@ROBOT_IP \
  'cd ~/dev/reachy_mini_conversation_app/reachy_mini_conversation_app && git pull && /venvs/apps_venv/bin/pip install -e . -q'
```

### Publishing to HuggingFace
```bash
git remote add hf https://huggingface.co/spaces/YOUR_HF_USERNAME/YOUR_SPACE_NAME
git push hf main
```

Files excluded from HuggingFace uploads are listed in `.hfignore` (private profiles, local dev artifacts).

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | ✅ | Google AI Studio key — [aistudio.google.com](https://aistudio.google.com/apikey) |
| `GCP_PROJECT_ID` | ✅ | GCP project for Vertex AI Memory Bank |
| `GCP_LOCATION` | ✅ | Default: `us-central1` |
| `VERTEX_REASONING_ENGINE_ID` | ✅ | Agent Engine instance ID |
| `GOOGLE_APPLICATION_CREDENTIALS` | ✅ | Path to service account key JSON |
| `MODEL_NAME` | optional | Default: `gemini-3.1-flash-live-preview` |
| `REACHY_MINI_CUSTOM_PROFILE` | optional | Profile to load on startup |
| `REACHY_MINI_PROFILES_OVERRIDE` | optional | Absolute path to profiles directory |
| `REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY` | optional | Path to additional tool `.py` files |
| `AUTOLOAD_EXTERNAL_TOOLS` | optional | Set `1` to auto-load all tools in external dir |

---

## Tools

| Tool | Description |
|---|---|
| `dance` | Play a named or random dance move |
| `stop_dance` | Stop current dance |
| `play_emotion` | Play a pre-recorded emotion clip |
| `stop_emotion` | Stop current emotion |
| `camera` | Capture a frame and send to Gemini for visual reasoning |
| `head_tracking` | Toggle face-tracking head movement |
| `move_head` | Move head left/right/up/down/front |
| `remember` | Store a fact in long-term memory (Agent Engine) |
| `identify_user` | Switch active user — reloads memory scope |
| `do_nothing` | Explicitly stay still and silent |

Enable/disable tools per profile in `profiles/{name}/tools.txt`.

---

## Voices

All 30 Gemini Live voices. Source: [configure-language-voice](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/live-api/configure-language-voice)

| Voice | Character | Voice | Character |
|---|---|---|---|
| Achird | Friendly | Laomedeia | Upbeat |
| Achernar | Soft | Leda | Youthful |
| Algenib | Gravelly | Alnilam | Firm |
| Algieba | Smooth | Orus | Firm |
| Aoede | Breezy | Pulcherrima | Forward |
| Autonoe | Bright | Puck | Upbeat |
| Callirrhoe | Easy-going | Rasalgethi | Informative |
| Charon | Informative | Sadachbia | Lively |
| Despina | Smooth | Sadaltager | Knowledgeable |
| Enceladus | Breathy | Schedar | Even |
| Erinome | Clear | Sulafat | Warm |
| Fenrir | Excitable | Umbriel | Easy-going |
| Gacrux | Mature | Vindemiatrix | Gentle |
| Iapetus | Clear | Zephyr | Bright |
| Kore | Firm | Zubenelgenubi | Casual |

Set voice per profile in `profiles/{name}/voice.txt`. Reference profile default: **Sadachbia** (Lively). Preview any voice from the Personality Studio UI.

---

## Architecture

Key files (paths relative to `src/reachy_mini_conversation_app/`):

| File | Role |
|---|---|
| `main.py` | Entrypoint — `GeminiLiveHandler`, memory backend, MCP pool |
| `config.py` | Constants: `AVAILABLE_VOICES`, `DEFAULT_MODEL_NAME`, `DEFAULT_VOICE` |
| `gemini_live.py` | Gemini handler, sleep/wake state machine, Edge Impulse wake-word detection |
| `memory.py` | `VertexMemoryBackend`; `_get_token()` for ADC/SA auth |
| `console.py` | FastAPI settings server — SSE streams, camera endpoint, REST API |
| `tools/core_tools.py` | `Tool` ABC — base class for all tools including MCP adapters |
| `tools/mcp_client.py` | MCP pool with stdio/streamablehttp/sse transport + `${VAR}` substitution |
| `prompts.py` | Memory injection and prompt assembly |
| `profiles/{name}/` | Profile bundles: `instructions.txt`, `tools.txt`, `voice.txt`, `mcp_servers.json` |

---

## Live API tool behavior

### Support matrix (`gemini-3.1-flash-live-preview`)

| Feature | Supported |
|---|---|
| Google Search grounding | ✅ |
| Function calling (synchronous) | ✅ |
| Function calling (async / NON_BLOCKING) | ❌ Gemini 2.5 Flash only |
| Google Maps, code execution, URL context | ❌ |

### Scheduling hints

Applied in `gemini_live.py:_handle_tool_result()` to control how the model announces tool results:

| Scheduling | Behaviour | Used for |
|---|---|---|
| `SILENT` | Use result silently | `remember`, `identify_user`, `do_nothing`, motion tools |
| `WHEN_IDLE` | Wait for natural pause | `camera`, `task_status` |
| `INTERRUPT` | Report immediately | explicit user-requested tools |

---

## Known limitations

| Issue | Root cause | Workaround |
|---|---|---|
| Session ends after ~10 min | Live API hard limit | Automatic restart; memory saves on close |
| Function calling blocks audio | `gemini-3.1-flash-live-preview` synchronous only | Scheduling hints already set; `NON_BLOCKING` staged for when 3.1 supports async |
| Google Search results stale on re-ask | Grounding caches per topic per session | Use MCP function tools for real-time endpoints |

---

## Sources

| Source | What we took from it |
|---|---|
| [pollen-robotics/reachy_mini_conversation_app](https://huggingface.co/spaces/pollen-robotics/reachy_mini_conversation_app) | Base fork — robot integration, profile system, tool loader, Gemini Live handler |
| [SJang1/ha-gemini-live](https://github.com/SJang1/ha-gemini-live) | Google Search grounding pattern |
| [Shubhamsaboo/openclaw-vertexai-memorybank](https://github.com/Shubhamsaboo/openclaw-vertexai-memorybank) | Vertex AI Agent Engine REST surface, memory scoping model |
| [google-gemini/cookbook: Live API tools](https://github.com/google-gemini/cookbook/blob/main/quickstarts/Get_started_LiveAPI_tools.ipynb) | `google_search` + `function_declarations` format |
| [ai.google.dev: Live API tools](https://ai.google.dev/gemini-api/docs/live-api/tools) | Tool support matrix, scheduling hints |
| [music-assistant/voice-support](https://github.com/music-assistant/voice-support) | Music Assistant `HassMediaSearchAndPlay` interface |
| [luisomoreau/hey_reachy](https://huggingface.co/spaces/luisomoreau/hey_reachy_wake_word_detection) | Edge Impulse "Hey Reachy" model trained on real voice samples — linux-aarch64 binary bundled |
| [Model Context Protocol](https://modelcontextprotocol.io) | MCP spec and Python SDK — protocol layer for external tool servers |
| [Home Assistant MCP Server](https://www.home-assistant.io/integrations/mcp_server/) | HA native MCP integration — streamablehttp transport, entity control |

---

## License

Apache 2.0
