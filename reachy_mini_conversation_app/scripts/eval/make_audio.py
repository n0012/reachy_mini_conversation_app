"""Synthesize the eval scenario audio once (Gemini TTS on Vertex) and cache it locally.

Usage: uv run --no-sync python scripts/eval/make_audio.py [--eval-dir ~/.reachy_eval]

Writes 16 kHz mono WAVs to <eval-dir>/audio/ plus manifest.json (step id -> file).
Lines are cached by voice+text, so editing one line only re-synthesizes that line.
The audio contains personal details from expectations.json, so it stays out of git.
"""

import io
import os
import json
import wave
import base64
import hashlib
import argparse
import urllib.request
from pathlib import Path

import numpy as np
import google.auth
import google.auth.transport.requests
from scipy.signal import resample_poly


HERE = Path(__file__).parent
TTS_MODEL = "gemini-2.5-flash-tts"
PROJECT = os.environ.get("REACHY_EVAL_TTS_PROJECT", "losiern-vibe")
RATE = 16000
LINE_GAP_S = 0.6
PEAK = 16000


def _token() -> str:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def _tts(text: str, voice: str) -> np.ndarray:
    """Return 16 kHz int16 speech for one line."""
    url = (
        f"https://aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/global/"
        f"publishers/google/models/{TTS_MODEL}:generateContent"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        part = json.loads(resp.read())["candidates"][0]["content"]["parts"][0]["inlineData"]
    raw = base64.b64decode(part["data"])
    if part.get("mimeType", "").startswith("audio/wav"):
        with wave.open(io.BytesIO(raw)) as wf:
            raw = wf.readframes(wf.getnframes())
    pcm24 = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    pcm = resample_poly(pcm24, 2, 3)
    # Normalize to about -6 dBFS: TTS output can clip, and a room mic hears speech quieter.
    pcm *= PEAK / max(1.0, float(np.abs(pcm).max()))
    return pcm.astype(np.int16)


def _write(path: Path, pcm: np.ndarray) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(pcm.tobytes())


def main() -> None:
    """Build or refresh the audio cache and manifest."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-dir", default=os.path.expanduser(os.environ.get("REACHY_EVAL_DIR", "~/.reachy_eval")))
    args = ap.parse_args()
    eval_dir = Path(args.eval_dir)
    audio_dir = eval_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    scenarios = json.loads((HERE / "scenarios.json").read_text())
    expectations = json.loads((eval_dir / "expectations.json").read_text())
    voices = scenarios["voices"]

    def line(voice_key: str, text: str) -> np.ndarray:
        voice = voices[voice_key]
        cached = audio_dir / f"line-{hashlib.sha1(f'{voice}|{text}'.encode()).hexdigest()[:16]}.wav"
        if not cached.exists():
            print(f"  tts [{voice}] {text[:60]}")
            _write(cached, _tts(text, voice))
        with wave.open(str(cached)) as wf:
            return np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)

    manifest: dict[str, dict] = {}
    gap = np.zeros(int(LINE_GAP_S * RATE), dtype=np.int16)
    for step in scenarios["steps"]:
        if step["kind"] in ("prompted", "conditional"):
            text = step.get("say") or expectations[step["say_from"]]
            lines = [("user", text)]
        elif step["kind"] == "ambient":
            lines = [tuple(x) for x in step["lines"]]
        else:
            continue
        pcm = np.concatenate([p for k, t in lines for p in (line(k, t), gap)][:-1])
        name = f"step-{step['id']}.wav"
        _write(audio_dir / name, pcm)
        manifest[step["id"]] = {"file": name, "text": " / ".join(t for _, t in lines), "seconds": len(pcm) / RATE}
    (audio_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"audio ready: {len(manifest)} steps in {audio_dir}")


if __name__ == "__main__":
    main()
