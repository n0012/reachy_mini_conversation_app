# Deployment Guide

## Robot info

| | |
|---|---|
| **SSH user** | `pollen` |
| **SSH password** | `root` |
| **Current IP** | `10.0.0.137` |
| **Target IP** | `10.0.0.60` (pending DHCP fix — update wlan0 MAC `88:a2:9e:8c:bb:e8` in router reservation) |
| **OS** | Linux aarch64 (Reachy Mini Pollen image) |

---

## How the Reachy Mini app store works

The robot's launcher discovers apps via the standard Python entry point group `reachy_mini_apps`. Any installed package that registers an entry point under this group appears in the on-robot UI automatically:

```toml
# pyproject.toml — this is what registers us in the app store
[project.entry-points."reachy_mini_apps"]
reachy_mini_conversation_app_gemini = "reachy_mini_conversation_app.main:ReachyMiniConversationApp"
```

Installing the package (any method — `pip install -e .`, `uv sync`, or cloning this repo) is all it takes to appear in the launcher. No separate registration step.

---

## One-time setup on the robot

```bash
ssh pollen@10.0.0.137   # password: root

# Install uv if not already present
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone this monorepo subtree (or clone the full monorepo and cd in)
cd ~
git clone https://github.com/n0012/n0012-monorepo.git  # adjust URL once subtree is published
cd n0012-monorepo/reachymini

# OR — if cloning directly from the HF Space fork (after Phase 1)
git clone https://huggingface.co/spaces/<your-hf-username>/reachy_mini_conversation_app_gemini .

# Create venv and install
uv venv --python python3.12 .venv
source .venv/bin/activate
uv sync

# Set your API key
cp .env.example .env
nano .env   # add GEMINI_API_KEY=...
```

---

## Updating the robot

```bash
ssh pollen@10.0.0.137
cd ~/n0012-monorepo/reachymini   # or wherever you cloned
git pull                          # gets latest from n0012-monorepo
source .venv/bin/activate
uv sync                           # installs any new/changed deps
```

To pull upstream bug fixes from the Pollen Robotics fork:
```bash
git fetch upstream
git merge upstream/main
uv sync
```

---

## Running manually (development / debugging)

```bash
ssh pollen@10.0.0.137
cd ~/reachymini
source .venv/bin/activate
reachy-mini-conversation-app          # uses default profile
reachy-mini-conversation-app --profile noir_detective
reachy-mini-conversation-app --help   # full options
```

Or from your Mac (app connects to robot over the network):
```bash
cd /Users/nick/dev/github/n0012/personal/n0012-monorepo/reachymini
source .venv/bin/activate
REACHY_MINI_IP=10.0.0.137 reachy-mini-conversation-app
```

---

## Environment variables

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | ✅ | Google AI Studio key — get from [aistudio.google.com](https://aistudio.google.com) |
| `MODEL_NAME` | optional | Default: `gemini-3.1-flash-live-preview` |
| `DEFAULT_VOICE` | optional | Default: `Kore`. Options: `Puck`, `Charon`, `Kore`, `Fenrir`, `Aoede` |
| `GOOGLE_SEARCH_GROUNDING` | optional | Set `1` to enable Google Search globally (per-profile `grounding.txt` overrides) |
| `MEMORY_DB_PATH` | optional | Default: `~/.reachy_mini/memory.sqlite` |
| `MCP_SERVERS_CONFIG` | optional | Path to a global `mcp_servers.json` (per-profile configs take priority) |
| `REACHY_MINI_CUSTOM_PROFILE` | optional | Profile name to load (default: `default`) |
| `REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY` | optional | Path to additional profiles outside the repo |
| `REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY` | optional | Path to additional tool `.py` files |
| `AUTOLOAD_EXTERNAL_TOOLS` | optional | Set `1` to auto-load all tools in external tools dir |

---

## Publishing to the Reachy Mini app store (HF Space)

The robot's launcher can install apps directly from Hugging Face Spaces. To make this fork discoverable the same way as the upstream:

1. Create a Hugging Face account (or use the existing `n0012` org)
2. Create a new Space: `n0012/reachy_mini_conversation_app_gemini`
3. Push from this folder:
   ```bash
   cd /Users/nick/dev/github/n0012/personal/n0012-monorepo/reachymini
   git remote add hf https://huggingface.co/spaces/n0012/reachy_mini_conversation_app_gemini
   git push hf main
   ```
4. Tag the Space with `reachy-mini` so the robot's launcher picks it up in discovery
5. Robot install:
   ```bash
   # From robot or from Mac connected to robot
   uv pip install git+https://huggingface.co/spaces/n0012/reachy_mini_conversation_app_gemini
   ```

The `reachy_mini_apps` entry point (in `pyproject.toml`) registers it with the launcher automatically on install.

---

## Profiles

Profiles live in `profiles/`. Each is a folder with:
- `instructions.txt` — system prompt (supports `[system/base_behavior]` template includes)
- `tools.txt` — enabled tools, comma or newline separated
- `voice.txt` — Gemini voice name (Puck, Charon, Kore, Fenrir, Aoede)
- `grounding.txt` — optional; add `google_search=true` to enable web search for this persona
- `mcp_servers.json` — optional; list of MCP servers available to this persona

Create a custom profile:
```bash
mkdir profiles/my_custom_profile
echo "Your custom system prompt here." > profiles/my_custom_profile/instructions.txt
echo "dance, move_head" > profiles/my_custom_profile/tools.txt
echo "Puck" > profiles/my_custom_profile/voice.txt

# Launch with it
reachy-mini-conversation-app --profile my_custom_profile
```

---

## Upstream sync

This fork tracks `upstream` (the original Pollen Robotics HF Space):

```bash
git fetch upstream
git merge upstream/main
# Resolve any conflicts (mainly in gemini_live.py and config.py)
uv sync
```

Significant upstream changes to watch for: updates to `console.py` (shared abstraction), `tools/core_tools.py` (Tool ABC), and the profile system.

---

## Local testing (Mac — no robot required)

The robot SDK (`reachy-mini`) pulls in `pygobject`/`pycairo` which need a native cairo library unavailable on Mac. Run tests against just the importable subset:

```bash
cd /Users/nick/dev/github/n0012/personal/n0012-monorepo/reachymini

# One-time: install testable deps (skips robot SDK)
uv pip install python-dotenv google-genai aiosqlite mcp pytest pytest-asyncio

# Run tests
PYTHONPATH=src uv run --no-project pytest tests/test_gemini_only_config.py tests/test_voices.py tests/test_config_name_collisions.py -v
```

Robot-hardware tests (audio, camera, moves) can only run on the robot itself. The unit tests above cover config, voices, and profile logic — everything you can validate before deploying.

---

## Phase 0 spike

Before implementing Phase 4 (memory), run the spike to determine which memory architecture to use:

```bash
cd /Users/nick/dev/github/n0012/personal/n0012-monorepo/reachymini
source .venv/bin/activate
python spikes/interactions_live.py
```

See `spikes/interactions_live.py` for setup instructions. Records its finding in `spikes/FINDINGS.md`.
