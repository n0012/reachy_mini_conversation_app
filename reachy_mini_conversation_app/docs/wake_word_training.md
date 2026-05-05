# Wake Word Training — Hey Reachy

This guide covers training a custom "Hey Reachy" wake word model using [OpenWakeWord](https://github.com/dscripka/openWakeWord) and deploying it to the robot. The default model ships with `hey_jarvis_v0.1.onnx` — follow this guide to replace it with a "Hey Reachy" model trained on Gemini's own voices.

---

## How it works

During sleep mode, Reachy uses OpenWakeWord for offline wake word detection — no cloud API calls, zero Gemini billing. Audio is processed locally on the robot's ARM64 CPU.

A **VAD pre-filter** skips neural network inference during silence, reducing idle CPU load by ~33%. OWW only runs when audio energy exceeds a threshold.

```
Microphone → VAD check → (silent? skip) → OWW inference → wake?
                ↓ (speech detected)              ↓ yes
           Run OWW model              asyncio.create_task(wake())
```

---

## Default model

`hey_jarvis_v0.1.onnx` ships with OWW and is ready to use with no training required. It's the default in `gemini_live.py`:

```python
self._OWW_MODEL_PATH = "/venvs/apps_venv/lib/python3.12/site-packages/openwakeword/resources/models/hey_jarvis_v0.1.onnx"
```

If you're happy with "Hey Jarvis", skip this guide.

---

## Training "Hey Reachy"

### What you need

| Requirement | Details |
|---|---|
| GPU | A100 recommended (~60-90 min). L4 works (~2-3 hrs). Free T4 Colab works (~5-6 hrs). |
| Disk | 200 GB minimum (ACAV100M features dataset ~15 GB, clips ~5 GB) |
| Google Colab | [colab.research.google.com](https://colab.research.google.com) or Colab Enterprise |
| Gemini API key | For generating voice samples via Gemini 2.5 Flash TTS |

### Step 1 — Open the training notebook

The notebook is at [`oww_training/hey_reachy_training.ipynb`](../oww_training/hey_reachy_training.ipynb) in this repo.

Upload it to Google Colab:
- **Colab**: File → Upload notebook
- **Colab Enterprise**: open directly from your connected repository

Set the runtime to **A100 GPU** (Runtime → Change runtime type → A100).

### Step 2 — Generate voice samples with Gemini TTS

The notebook includes a cell that generates ~120 "Hey Reachy" samples across all 20 Gemini Live voices with 6 phrase variations each:

```
"Hey Reachy" / "Hey Reachy!" / "Hey, Reachy" / "Hey Reachy?" / "hey reachy" / "Hey Reachy, wake up"
```

These supplement the 25,000 synthetic Piper TTS clips with high-quality, voice-diverse samples — improving detection accuracy for people who've heard Reachy speak (which shapes how they say its name).

Set your project ID in the Gemini TTS cell:
```python
PROJECT_ID = "your-gcp-project-id"
LOCATION = "us-central1"
```

The cell uses Vertex AI ADC (authenticated automatically in Colab Enterprise) or a standard Gemini API key.

### Step 3 — Run the notebook top to bottom

Expected times on A100:

| Step | What happens | Time |
|---|---|---|
| 1. Environment setup | Install dependencies, download backbone models | ~5 min |
| 2. Download data | MIT RIRs, AudioSet, ACAV100M features (~15 GB) | ~15 min |
| 3. Configure | Set target phrase, sample counts, training steps | <1 min |
| 4. Gemini TTS samples | Generate 120 voice samples across Gemini voices | ~2 min |
| 5. Generate clips | Piper TTS generates 25k+ synthetic "Hey Reachy" clips | ~20 min |
| 6. Augment clips | Add room acoustics and background noise | ~10 min |
| 7. Train | 50,000 steps on A100 | ~35 min |

**Total: ~90 minutes on A100.**

### Step 4 — Download the model

The final cell downloads `hey_reachy.onnx` (~400 KB). In Colab Enterprise it copies to a GCS bucket — set your bucket name in the download cell.

---

## Deploying to the robot

### Copy the model
```bash
sshpass -p 'root' scp -o StrictHostKeyChecking=no -o PreferredAuthentications=password \
    hey_reachy.onnx \
    pollen@ROBOT_IP:/venvs/apps_venv/lib/python3.12/site-packages/openwakeword/resources/models/hey_reachy.onnx
```

### Update the model path in `gemini_live.py`

Change `_OWW_MODEL_PATH` in `GeminiLiveHandler.__init__`:

```python
# Before
self._OWW_MODEL_PATH = "/venvs/apps_venv/lib/python3.12/site-packages/openwakeword/resources/models/hey_jarvis_v0.1.onnx"

# After
self._OWW_MODEL_PATH = "/venvs/apps_venv/lib/python3.12/site-packages/openwakeword/resources/models/hey_reachy.onnx"
```

### Deploy and restart
```bash
./deploy.sh --rsync --restart
```

### Verify
Put Reachy to sleep (let it auto-sleep after 10 min, or press Sleep in the web UI), then say "Hey Reachy". The app should restart and Reachy should greet you.

Check logs if it doesn't respond:
```bash
sshpass -p 'root' ssh pollen@ROBOT_IP \
  'journalctl -u reachy-mini-daemon.service --no-pager -n 50 | grep -i "wake\|OWW\|jarvis\|reachy"'
```

---

## Tuning the detection threshold

The default threshold is `0.5` — lower means more sensitive (more false positives), higher means less sensitive (may miss detections).

```python
self._OWW_THRESHOLD = 0.5  # in gemini_live.py __init__
```

Test in a real environment before adjusting. A new model may need a threshold calibration pass.

---

## Re-training tips

- **More phrase variations** — add more entries to `PHRASES` in the Gemini TTS cell
- **More speakers** — the libritts_r ONNX model has 900+ speakers; 25k clips covers many
- **Your own voice** — record 20-50 WAVs of yourself saying "Hey Reachy" at 16kHz, add them to `real_samples_hey_reachy/` before running Step 5
- **False positives** — reduce by increasing `n_samples` for adversarial (confusable) phrases in the config cell

---

## Resources

- [OpenWakeWord GitHub](https://github.com/dscripka/openWakeWord) — model architecture, training docs
- [OWW automatic training notebook](https://github.com/dscripka/openWakeWord/blob/main/notebooks/automatic_model_training.ipynb) — original upstream notebook this is based on
- [Piper TTS voices](https://github.com/rhasspy/piper-voices) — voice models for synthetic clip generation
- [Gemini 2.5 Flash TTS](https://ai.google.dev/gemini-api/docs/speech-generation) — used for Gemini-voice samples
