"""Spoken setup-URL announcement for first-launch UX.

When the app starts with no `~/.reachy_mini_gemini_live/.env` (setup mode), Reachy speaks
its IP and port through the speaker so the user knows where to point a browser.

Uses espeak-ng (apt: `sudo apt-get install -y espeak-ng`) since it's tiny,
offline, and we don't have GCP credentials yet during setup. Gracefully no-ops
if espeak-ng isn't installed.
"""
from __future__ import annotations

import io
import logging
import shutil
import socket
import subprocess
import tempfile
import wave
from math import gcd
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


def _get_local_ip() -> str:
    """Best-effort detection of the LAN IP the launcher serves on."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))  # no packet sent, just resolves the route
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "the robot's IP"


def _ensure_espeak_ng() -> bool:
    """Ensure espeak-ng is installed. Returns True if available, False otherwise.

    HF Spaces' `packages.txt` is not honored for `sdk: static` apps, so the
    Reachy Mini launcher's pip-only install can't pre-install apt deps. As a
    workaround we one-shot `sudo apt-get install` here. The pollen user on
    Reachy Mini OS has passwordless sudo (factory default), so this is silent
    and takes ~10s. On systems where sudo prompts for a password, `sudo -n`
    fails fast and we gracefully no-op.
    """
    if shutil.which("espeak-ng") is not None:
        return True
    if shutil.which("sudo") is None or shutil.which("apt-get") is None:
        logger.info("espeak-ng missing and no sudo/apt-get to install it — announcement silent.")
        return False
    logger.info("espeak-ng missing — one-shot install via sudo apt-get...")
    try:
        result = subprocess.run(
            ["sudo", "-n", "apt-get", "install", "-y", "-q", "espeak-ng"],
            capture_output=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        logger.warning("espeak-ng install timed out — likely sudo password prompt; staying silent.")
        return False
    except Exception as e:
        logger.warning("espeak-ng install errored: %s", e)
        return False
    if result.returncode == 0 and shutil.which("espeak-ng") is not None:
        logger.info("espeak-ng installed.")
        return True
    logger.warning(
        "espeak-ng install failed (rc=%d): %s",
        result.returncode,
        result.stderr.decode("utf-8", errors="ignore")[:300],
    )
    return False


def _synthesize_to_wav(text: str) -> bytes | None:
    """Return WAV bytes for `text` via espeak-ng, or None if unavailable."""
    if not _ensure_espeak_ng():
        return None
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    try:
        result = subprocess.run(
            ["espeak-ng", "-s", "150", "-w", wav_path, text],
            capture_output=True, timeout=10,
        )
        if result.returncode != 0:
            logger.warning(
                "espeak-ng failed (rc=%d): %s",
                result.returncode,
                result.stderr.decode("utf-8", errors="ignore"),
            )
            return None
        return Path(wav_path).read_bytes()
    finally:
        Path(wav_path).unlink(missing_ok=True)


def _play_wav(wav_bytes: bytes) -> None:
    """Play raw WAV bytes through reachymini_audio_sink (or default output)."""
    import sounddevice as sd

    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        if sampwidth != 2:
            logger.warning("WAV must be 16-bit PCM (got %d bytes/sample)", sampwidth)
            return
        pcm_bytes = wf.readframes(wf.getnframes())

    mono = np.frombuffer(pcm_bytes, dtype=np.int16)
    if channels > 1:
        mono = mono.reshape(-1, channels).mean(axis=1).astype(np.int16)

    device_id = None
    device_rate = 0
    device_channels = 1
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0 and "reachymini_audio_sink" in d["name"].lower():
            device_id = i
            device_rate = int(d["default_samplerate"])
            device_channels = min(2, int(d["max_output_channels"]))
            break
    if device_id is None:
        try:
            out = sd.default.device[1]
            if out is not None and out >= 0:
                d = sd.query_devices(out)
                device_id = out
                device_rate = int(d["default_samplerate"])
                device_channels = min(2, int(d["max_output_channels"]))
        except Exception:
            pass
    if device_id is None or device_rate <= 0:
        logger.warning("No usable output device for setup announcement")
        return

    if rate != device_rate:
        from scipy.signal import resample_poly
        g = gcd(rate, device_rate)
        up, down = device_rate // g, rate // g
        mono = np.clip(
            resample_poly(mono.astype(np.float32), up, down), -32768, 32767
        ).astype(np.int16)

    audio = np.repeat(mono[:, None], device_channels, axis=1) if device_channels > 1 else mono
    sd.play(audio, samplerate=device_rate, device=device_id, blocking=True)


def announce_setup_url(port: int = 7860) -> None:
    """Speak 'Hello! Set me up at <IP> port <port>' through the robot's speaker.

    Safe to call from a background thread. Errors are logged and swallowed —
    failure to speak should never block the setup wizard from working.
    """
    try:
        ip = _get_local_ip()
        # espeak-ng pronounces "192.168.0.60" as "one ninety two point one
        # sixty eight point zero point sixty" — clearer to insert short pauses
        # between octets so each digit group is parsed individually.
        ip_spoken = ".  ".join(ip.split("."))
        text = (
            f"Hello! Please set me up. "
            f"Open me in a browser at {ip_spoken}, port {port}."
        )
        logger.info("Setup announcement: ip=%s port=%d", ip, port)
        wav = _synthesize_to_wav(text)
        if wav is not None:
            _play_wav(wav)
    except Exception as e:
        logger.warning("Setup announcement failed: %s", e)
