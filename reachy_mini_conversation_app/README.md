---
title: Reachy Mini Conversation App — Gemini Edition
emoji: 🎤
colorFrom: blue
colorTo: purple
sdk: static
pinned: false
short_description: Gemini Live voice assistant with memory, MCP tools, and home control
suggested_storage: large
tags:
 - reachy_mini
 - reachy_mini_python_app
 - gemini
 - vertex-ai
---

# Reachy Mini — Gemini Live Edition

A fork of the [Pollen Robotics conversation app](https://huggingface.co/spaces/pollen-robotics/reachy_mini_conversation_app) rebuilt around the Gemini Live API with persistent per-user memory, profile-scoped MCP tools, and an offline wake word — all managed from a headless browser UI on the robot.

---

## What's new vs the base app

| Feature | Base app | This fork |
|---|---|---|
| AI backend | HF / OpenAI / Gemini | Gemini Live only (`gemini-3.1-flash-live-preview`) |
| Persistent memory | ✗ | ✅ Vertex AI Agent Engine — per-user, cross-session |
| MCP tool ecosystem | ✗ | ✅ Per-profile servers with secure credential injection |
| Home Assistant | ✗ | ✅ Full home control via HA MCP (lights, climate, media, locks) |
| Wake word | ✗ | ✅ OpenWakeWord — Hey Jarvis built-in, Hey Reachy trainable |
| Sleep mode | ✗ | ✅ Auto-sleep after 10 min idle — zero API cost |
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

```bash
# 1. SSH to robot
ssh pollen@ROBOT_IP   # password: root

# 2. Clone and install
cd ~/dev
git clone https://github.com/n0012/reachy_mini_conversation_app.git
/venvs/apps_venv/bin/pip install -e ~/dev/reachy_mini_conversation_app/reachy_mini_conversation_app/

# 3. Open the setup wizard
# On your computer, navigate to: http://ROBOT_IP:7860
# Complete the one-time setup (API keys, GCP project, Agent Engine ID)
```

The setup wizard stores everything in `~/.reachy_mini/.env` on the robot. No manual `.env` editing required.

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
Vector search          Model calls            Full transcript
retrieves facts  →     remember() to   →      ingested and
for this user          store new facts         deduplicated
and injects them       silently mid-           into memory bank
into system prompt     conversation
```

**Key properties:**
- **Per-user scope** — memories are isolated by `user_id` + `profile`. `identify_user` switches the active scope mid-conversation so multiple household members each have their own persistent context.
- **Facts, not transcripts** — the memory bank stores extracted facts ("prefers oat milk", "working on bathroom renovation"), not raw conversation logs. Each session adds to and refines what's known.
- **Opt-in per profile** — add `enabled=true` to `profiles/{name}/memory.txt` to enable. Profiles without this file run without memory (useful for public kiosk or demo profiles).
- **Mid-session saves** — every 20 turns, a background save is triggered so long sessions aren't lost if the robot is powered off.

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
1. Enable the [Home Assistant MCP Server](https://developers.home-assistant.io/blog/2025/04/09/mcp-server/) in HA (`Settings → Devices & Services → Add Integration → Model Context Protocol`)
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
- Environment variable manager — add/edit/delete secrets used by MCP servers (e.g. `HA_MCP_TOKEN`)

---

## Wake Word & Sleep Mode

### Sleep mode
After 10 minutes of no conversation, Reachy automatically:
1. Closes the Gemini Live WebSocket — billing stops immediately
2. Moves to a sleep pose
3. Switches to offline wake word detection (zero cloud cost)

### Wake word
Uses [OpenWakeWord](https://github.com/dscripka/openWakeWord) for offline detection. Default model: `hey_jarvis_v0.1.onnx` (ships with OWW).

**CPU optimization** — a VAD pre-filter skips neural network inference during silence, reducing idle CPU load by ~33% on the robot's ARM64 processor.

### Training a custom "Hey Reachy" wake word

A Colab training notebook is included: [`oww_training/hey_reachy_training.ipynb`](oww_training/hey_reachy_training.ipynb)

See **[docs/wake_word_training.md](docs/wake_word_training.md)** for the full guide, including:
- A100 GPU setup on Google Colab Enterprise
- Generating synthetic training data with Piper TTS
- Generating voice samples with Gemini 2.5 Flash TTS across all 20 Gemini voices
- Deploying the trained ONNX model to the robot

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
| `gemini_live.py` | Gemini handler, sleep/wake state machine, OWW detection |
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
| [Home Assistant MCP Server](https://developers.home-assistant.io/blog/2025/04/09/mcp-server/) | HA native MCP integration — streamablehttp transport, entity control |

---

## License

Apache 2.0
