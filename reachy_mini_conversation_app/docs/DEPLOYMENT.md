# Deployment Guide

## Robot info

| | |
|---|---|
| **SSH user** | `pollen` |
| **SSH password** | `root` |
| **Current IP** | `10.0.0.137` |
| **Target IP** | `10.0.0.60` (pending DHCP fix — update wlan0 MAC `88:a2:9e:8c:bb:e8` in router reservation, not eth0) |
| **OS** | Linux aarch64 (Reachy Mini Pollen image) |

## Repo layout

```
reachymini/
  docs/                           ← planning docs
  reachy_mini_conversation_app/   ← the app
    src/
    tests/
    profiles/
    pyproject.toml
    ...
```

---

## How the Reachy Mini app store works

The robot's launcher discovers apps via the Python entry point group `reachy_mini_apps`. Any installed package registering under this group appears in the on-robot UI:

```toml
# project/pyproject.toml — already set
[project.entry-points."reachy_mini_apps"]
reachy_mini_conversation_app = "reachy_mini_conversation_app.main:ReachyMiniConversationApp"
```

Installing the package is all it takes — no separate registration step.

---

## One-time setup on the robot

```bash
ssh pollen@10.0.0.60   # password: root

# Clone the app repo
cd ~/dev
git clone https://github.com/n0012/reachy_mini_conversation_app.git

# Install into the launcher's venv (apps_venv) — NOT uv sync
/venvs/apps_venv/bin/pip install -e ~/dev/reachy_mini_conversation_app/reachy_mini_conversation_app/

# Copy ADC credentials from Mac (run this on Mac)
scp ~/.config/gcloud/application_default_credentials.json \
  pollen@10.0.0.60:/home/pollen/.config/gcloud/application_default_credentials.json

# Create venv and install
uv venv --python python3.12 .venv
source .venv/bin/activate
uv sync

# Configure
cp .env.example .env
nano .env
```

---

## Environment variables

Copy `.env.example` to `.env`:

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | ✅ | Google AI Studio key — [aistudio.google.com](https://aistudio.google.com) |
| `GCP_PROJECT_ID` | ✅ (Phase 4) | GCP project for Vertex AI Memory Bank |
| `GCP_LOCATION` | ✅ (Phase 4) | Default: `us-central1` |
| `VERTEX_REASONING_ENGINE_ID` | ✅ (Phase 4) | Agent Engine instance ID |
| `MODEL_NAME` | optional | Default: `gemini-3.1-flash-live-preview` |
| `DEFAULT_VOICE` | optional | Default: `Kore`. Options: `Puck`, `Charon`, `Kore`, `Fenrir`, `Aoede` |
| `GOOGLE_SEARCH_GROUNDING` | optional | Set `1` to enable Google Search globally |
| `REACHY_MINI_CUSTOM_PROFILE` | optional | Profile name to load (default: `default`) |
| `REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY` | optional | Path to additional profiles |
| `REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY` | optional | Path to additional tool `.py` files |
| `AUTOLOAD_EXTERNAL_TOOLS` | optional | Set `1` to auto-load all tools in external tools dir |

---

## GCP setup for Vertex AI Memory Bank (one-time)

```bash
# Authenticate
gcloud auth application-default login

# Enable APIs
gcloud services enable aiplatform.googleapis.com --project=YOUR_PROJECT_ID

# Create a Reasoning Engine instance (Memory Bank lives here)
# Exact CLI TBD — confirm in Phase 0 spike
gcloud ai reasoning-engines create \
  --display-name="reachy-memory" \
  --location=us-central1 \
  --project=YOUR_PROJECT_ID

# Note the ID from the output — add to .env as VERTEX_REASONING_ENGINE_ID
```

On the robot, authenticate ADC:
```bash
gcloud auth application-default login
# Or use a service account key file:
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

---

## Updating the robot

```bash
# One command from Mac (skips LFS GIF, reinstalls our package files only)
sshpass -p 'root' ssh pollen@10.0.0.60 \
  "cd ~/dev/reachy_mini_conversation_app && \
   GIT_LFS_SKIP_SMUDGE=1 git pull && \
   /venvs/apps_venv/bin/pip install --force-reinstall --no-deps reachy_mini_conversation_app/"
```

**Why `--force-reinstall --no-deps` and not `-e`:** The editable install (`-e`) uses setuptools legacy mode which physically copies files to `site-packages` only at install time. New files added later (like `memory.py`, `location.py`) won't appear until you reinstall. `--force-reinstall --no-deps` refreshes only our package files without touching the heavy deps (torch, gradio, etc.).

Pull upstream bug fixes:
```bash
git fetch upstream
git merge upstream/main
uv sync
```

---

## Running manually (development / debugging)

```bash
# On the robot
ssh pollen@10.0.0.137
cd ~/n0012-monorepo/reachymini/reachy_mini_conversation_app
source .venv/bin/activate
reachy-mini-conversation-app
reachy-mini-conversation-app --profile noir_detective
```

---

## Local testing (Mac — no robot required)

The robot SDK (`reachy-mini`) requires native cairo unavailable on Mac. Run tests against the importable subset:

```bash
cd /Users/nick/dev/github/n0012/personal/n0012-monorepo/reachymini

# One-time: install testable deps (skips robot SDK)
uv pip install python-dotenv google-genai aiosqlite mcp pytest pytest-asyncio

# Run tests
PYTHONPATH=reachy_mini_conversation_app/src uv run --no-project pytest \
  reachy_mini_conversation_app/tests/test_gemini_only_config.py \
  reachy_mini_conversation_app/tests/test_voices.py \
  reachy_mini_conversation_app/tests/test_config_name_collisions.py -v
```

---

## Profiles

Each profile is a folder under `profiles/`:

```
profiles/my_profile/
  instructions.txt    — system prompt
  tools.txt           — enabled tools (comma or newline separated)
  voice.txt           — Gemini voice (Puck, Charon, Kore, Fenrir, Aoede)
  grounding.txt       — optional: google_search=true
  mcp_servers.json    — optional: MCP server list
```

```bash
mkdir reachy_mini_conversation_app/profiles/my_profile
echo "You are a helpful robot named Cosmo." > reachy_mini_conversation_app/profiles/my_profile/instructions.txt
echo "dance, move_head" > reachy_mini_conversation_app/profiles/my_profile/tools.txt
echo "Puck" > reachy_mini_conversation_app/profiles/my_profile/voice.txt

reachy-mini-conversation-app --profile my_profile
```

---

## Publishing to HF Space (app store distribution)

```bash
cd /Users/nick/dev/github/n0012/personal/n0012-monorepo/reachymini/reachy_mini_conversation_app
git remote add hf https://huggingface.co/spaces/n0012/reachy_mini_conversation_app_gemini
git push hf main
```

Tag the Space with `reachy-mini` for launcher discovery.

---

## Upstream sync

```bash
git fetch upstream
git merge upstream/main
# Resolve conflicts (mainly gemini_live.py, config.py)
uv sync
```

---

## Phase 0 spike

Before Phase 4, run the Memory Bank compatibility spike:

```bash
PYTHONPATH=reachy_mini_conversation_app/src uv run --no-project \
  python reachy_mini_conversation_app/spikes/memory_bank_spike.py
```

Records findings in `reachy_mini_conversation_app/spikes/FINDINGS.md`.
