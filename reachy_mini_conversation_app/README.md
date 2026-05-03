---
title: Reachy Mini Conversation App
emoji: 🎤
colorFrom: red
colorTo: blue
sdk: static
pinned: false
short_description: Talk with Reachy Mini!
suggested_storage: large
tags:
 - reachy_mini
 - reachy_mini_python_app
---

# Reachy Mini Conversation App

Gemini Live voice assistant running on the Reachy Mini robot. Supports real-time conversation, Google Search grounding, per-profile personalities, and persistent memory via Vertex AI Memory Bank.

Fork of [`pollen-robotics/reachy_mini_conversation_app`](https://huggingface.co/spaces/pollen-robotics/reachy_mini_conversation_app).

---

## Robot

| | |
|---|---|
| **SSH user** | `pollen` |
| **SSH password** | `root` |
| **IP** | `10.0.0.60` |
| **OS** | Linux aarch64 (Reachy Mini Pollen image) |
| **Model** | `gemini-3.1-flash-live-preview` |

---

## One-time setup on the robot

```bash
ssh pollen@10.0.0.60   # password: root

# Clone and install
cd ~/dev
git clone https://github.com/n0012/reachy_mini_conversation_app.git
/venvs/apps_venv/bin/pip install -e ~/dev/reachy_mini_conversation_app/reachy_mini_conversation_app/

# Copy .env into place (must be here for find_dotenv to find it — launcher CWD is under /venvs/mini_daemon)
cp ~/dev/reachy_mini_conversation_app/reachy_mini_conversation_app/.env /venvs/mini_daemon/.env

# Copy ADC credentials from Mac
sshpass -p 'root' scp ~/.config/gcloud/application_default_credentials.json \
  pollen@10.0.0.60:/home/pollen/.config/gcloud/application_default_credentials.json
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | ✅ | Google AI Studio key — [aistudio.google.com](https://aistudio.google.com) |
| `GCP_PROJECT_ID` | ✅ | GCP project for Vertex AI Memory Bank |
| `GCP_LOCATION` | ✅ | Default: `us-central1` |
| `VERTEX_REASONING_ENGINE_ID` | ✅ | Agent Engine instance ID |
| `GOOGLE_APPLICATION_CREDENTIALS` | ✅ | Path to ADC or service account key |
| `MODEL_NAME` | optional | Default: `gemini-3.1-flash-live-preview` |
| `GOOGLE_SEARCH_GROUNDING` | optional | Set `1` to enable Google Search globally |
| `REACHY_MINI_CUSTOM_PROFILE` | optional | Profile name to load (default: `default`) |
| `REACHY_MINI_PROFILES_OVERRIDE` | optional | Absolute path to profiles directory |
| `REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY` | optional | Path to additional profiles |
| `REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY` | optional | Path to additional tool `.py` files |
| `AUTOLOAD_EXTERNAL_TOOLS` | optional | Set `1` to auto-load all tools in external tools dir |

---

## Deploying

### SSH direct deploy (recommended while developing locally)

Pushes local source directly to the robot — no GitHub intermediary.

```bash
# From the repo root on Mac
rsync -avz --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' \
  reachy_mini_conversation_app/src/reachy_mini_conversation_app/ \
  pollen@10.0.0.60:~/dev/reachy_mini_conversation_app/reachy_mini_conversation_app/src/reachy_mini_conversation_app/

# Sync profiles if changed
rsync -avz \
  reachy_mini_conversation_app/profiles/ \
  pollen@10.0.0.60:~/dev/reachy_mini_conversation_app/reachy_mini_conversation_app/profiles/
```

The install is editable so rsync'd files are live immediately. Restart the app on the robot to pick up changes.

### Git pull deploy (when pushing to GitHub first)

```bash
sshpass -p 'root' ssh pollen@10.0.0.60 \
  'cd ~/dev/reachy_mini_conversation_app/reachy_mini_conversation_app && git pull'
```

### Refreshing ADC credentials

ADC from `gcloud auth application-default login` expires periodically. When it does, Vertex AI Memory Bank reads and writes silently fail.

```bash
# 1. Re-authenticate on Mac
gcloud auth application-default login

# 2. Copy to robot
sshpass -p 'root' scp \
  ~/.config/gcloud/application_default_credentials.json \
  pollen@10.0.0.60:/home/pollen/.config/gcloud/application_default_credentials.json
```

Long-term fix: use a service account key (doesn't expire). Set `GOOGLE_APPLICATION_CREDENTIALS` in `.env`.

### Restore `.env` after reboot

The `.env` at `/venvs/mini_daemon/.env` doesn't survive reboots:

```bash
sshpass -p 'root' ssh pollen@10.0.0.60 \
  'cp ~/dev/reachy_mini_conversation_app/reachy_mini_conversation_app/.env /venvs/mini_daemon/.env'
```

### Publishing to HuggingFace Space (app store distribution)

```bash
git remote add hf https://huggingface.co/spaces/n0012/reachy_mini_conversation_app_gemini
git push hf main
```

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

Set voice per profile in `profiles/{name}/voice.txt`. Current default: **Sadachbia** (Lively).

---

## Profiles

Each profile is a folder under `profiles/`:

```
profiles/my_profile/
  instructions.txt    — system prompt
  tools.txt           — enabled tools (one per line)
  voice.txt           — Gemini voice name
  grounding.txt       — optional: google_search=true
  mcp_servers.json    — optional: MCP server list
```

Set `REACHY_MINI_CUSTOM_PROFILE=my_profile` in `.env` to activate.

---

## Memory (Vertex AI Memory Bank)

Persistent per-user memory backed by Agent Engine instance `YOUR_ENGINE_ID` on project `losiern-vibe`.

- **Load**: memories retrieved at session start and injected into the system prompt
- **Save**: transcript ingested after session ends
- **Remember tool**: model can explicitly store facts mid-conversation
- **Multi-user**: `identify_user` tool scopes memories per person; active user persisted in `~/.reachy_mini/active_user.json`

### GCP setup (one-time)

The app authenticates to both Gemini and the Memory Bank via a single service account key — no Gemini API key needed.

**1. Enable APIs**

```bash
gcloud services enable aiplatform.googleapis.com --project=YOUR_PROJECT_ID
```

**2. Create service account and grant permissions**

```bash
export PROJECT_ID=YOUR_PROJECT_ID

gcloud iam service-accounts create reachy-mini \
  --display-name="Reachy Mini" \
  --project=$PROJECT_ID

# roles/aiplatform.user covers Gemini Live API + Agent Engine memory
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

> If key creation is blocked by an org policy, see [docs/GCP_ORG_POLICY.md](docs/GCP_ORG_POLICY.md) for the override steps.

**4. Create a Reasoning Engine instance (Memory Bank)**

```bash
# Via REST — note the ID returned → VERTEX_REASONING_ENGINE_ID in setup wizard
curl -X POST \
  "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/${PROJECT_ID}/locations/us-central1/reasoningEngines" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -d '{"displayName": "reachy-memory", "spec": {}}'
```

Paste the key JSON into the setup wizard at `http://ROBOT_IP:7860` — it handles everything from there.

---

## Tools

| Tool | Description |
|---|---|
| `dance` | Play a named or random dance move |
| `stop_dance` | Stop current dance |
| `play_emotion` | Play a pre-recorded emotion clip |
| `stop_emotion` | Stop current emotion |
| `camera` | Capture a frame and ask Gemini about it |
| `head_tracking` | Toggle face-tracking head movement |
| `move_head` | Move head left/right/up/down/front |
| `remember` | Store a fact in long-term memory |
| `identify_user` | Switch active user for memory scoping |
| `do_nothing` | Explicitly stay still and silent |

---

## Local testing (Mac — no robot required)

```bash
cd /Users/nick/dev/github/n0012/personal/n0012-monorepo/reachymini
PYTHONPATH=reachy_mini_conversation_app/src uv run --no-project pytest \
  reachy_mini_conversation_app/tests/test_gemini_only_config.py \
  reachy_mini_conversation_app/tests/test_voices.py \
  reachy_mini_conversation_app/tests/test_config_name_collisions.py -v
```

---

## Architecture

Key files (paths relative to `src/reachy_mini_conversation_app/`):

| File | Role |
|---|---|
| `main.py` | Entrypoint — instantiates `GeminiLiveHandler`, memory backend, MCP pool |
| `config.py` | Constants: `AVAILABLE_VOICES`, `DEFAULT_MODEL_NAME`, `DEFAULT_VOICE` |
| `gemini_live.py` | Gemini handler; `_build_live_config()` builds `LiveConnectConfig` |
| `memory.py` | `VertexMemoryBackend` and `FileMemoryBackend`; `_get_token()` for ADC/SA auth |
| `tools/core_tools.py` | `Tool` ABC — base class for all tools including MCP adapters |
| `tools/__init__.py` | 3-tier loader: profile tools → core tools → external dir |
| `prompts.py` | `_expand_prompt_includes()` — handles memory injection and prompt includes |
| `profiles/{name}/` | Profile bundles: `instructions.txt`, `tools.txt`, `voice.txt`, `mcp_servers.json` |

SDK: `google-genai >= 1.0.0`. Auth: `google-auth` (ADC or service account key).

---

## Live API tool behavior

Source: [ai.google.dev/gemini-api/docs/live-api/tools](https://ai.google.dev/gemini-api/docs/live-api/tools)

### What's supported on `gemini-3.1-flash-live-preview`

| Feature | Supported |
|---|---|
| Google Search grounding | ✅ |
| Function calling (synchronous) | ✅ |
| Function calling (async / NON_BLOCKING) | ❌ not yet — Gemini 2.5 Flash only |
| Google Maps, code execution, URL context | ❌ |

### Scheduling (response-side hints)

Even with synchronous calls, responses can include a `scheduling` field to tell the model *how* to announce results. Applied in `gemini_live.py:_handle_tool_result()`:

| Scheduling | Behaviour | Used for |
|---|---|---|
| `SILENT` | Use result later without speaking | `remember`, `identify_user`, `do_nothing`, motion tools |
| `WHEN_IDLE` | Wait for a natural conversational pause | `camera`, `task_status`, `task_cancel` |
| `INTERRUPT` | Report immediately | explicit user-requested tools |

### When NON_BLOCKING lands on 3.1

Add `"behavior": "NON_BLOCKING"` to function declarations in `_tool_specs_to_gemini_declarations()`. The `BackgroundToolManager` and scheduling hints are already staged for this.

---

## Known limitations

| Issue | Root cause | Workaround |
|---|---|---|
| Session ends after ~10 min | Live API hard limit | Automatic restart; memory saves on close |
| `google_search` results stale on re-ask | Grounding caches per topic per session | Use an MCP function tool for real-time endpoints instead |
| Function calling blocks audio by default | `gemini-3.1-flash-live-preview` only supports synchronous tool calls | Scheduling hints (`SILENT`/`WHEN_IDLE`/`INTERRUPT`) already set on responses. `NON_BLOCKING` declarations staged for when 3.1 supports async (currently Gemini 2.5 Flash only) |
| `.env` lost on reboot | Launcher CWD quirk — must be at `/venvs/mini_daemon/.env` | Re-run the cp command after reboot (see Deploying section) |

---

## Sources

| Source | What we took from it |
|---|---|
| [pollen-robotics/reachy_mini_conversation_app](https://huggingface.co/spaces/pollen-robotics/reachy_mini_conversation_app) | Base fork — robot integration, profile system, tool plugin loader, Gemini Live handler |
| [SJang1/ha-gemini-live](https://github.com/SJang1/ha-gemini-live) | Google Search grounding pattern, native Gemini Live speech-to-speech architecture |
| [Shubhamsaboo/openclaw-vertexai-memorybank](https://github.com/Shubhamsaboo/openclaw-vertexai-memorybank) | Vertex AI Memory Bank REST API surface, scoping model, noise filter thresholds |
| [google-gemini/cookbook: Get_started_LiveAPI_tools.ipynb](https://github.com/google-gemini/cookbook/blob/main/quickstarts/Get_started_LiveAPI_tools.ipynb) | Confirms raw dict format for `google_search` + `function_declarations` together |
| [google-gemini/gemini-skills: gemini-live-api-dev](https://github.com/google-gemini/gemini-skills/blob/main/skills/gemini-live-api-dev/SKILL.md) | Context compression for long sessions, `send_realtime_input` vs `send_client_content` |
| [ai.google.dev: Live API tools](https://ai.google.dev/gemini-api/docs/live-api/tools) | Tool support matrix per model; `NON_BLOCKING` + scheduling (`INTERRUPT`/`WHEN_IDLE`/`SILENT`) — async calls only on Gemini 2.5 Flash, not yet on 3.1; Google Maps/code execution/URL context unsupported |

---

## License

Apache 2.0
