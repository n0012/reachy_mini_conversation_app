"""Gemini Live API handler for real-time audio conversation.

Uses the google-genai SDK's Live API for bidirectional audio streaming with
function calling support.

Audio formats (per Gemini Live API spec):
  Input:  16-bit PCM, 16 kHz, mono
  Output: 16-bit PCM, 24 kHz, mono
"""

import os
import re
import json
import stat
import uuid
import base64
import asyncio
import logging
import threading
from typing import Any, Dict, List, Final, Tuple, Literal, Optional
from datetime import datetime

import numpy as np
from google import genai
from google.genai import types
from numpy.typing import NDArray
from scipy.signal import resample

from reachy_mini_gemini_live.config import config
from reachy_mini_gemini_live.memory import DEFAULT_USER_ID, get_active_user, create_memory_backend
from reachy_mini_gemini_live.voices import resolve_voice
from reachy_mini_gemini_live.prompts import get_session_voice, get_session_grounding, get_session_instructions
from reachy_mini_gemini_live.location import get_location_context
from reachy_mini_gemini_live.streaming import AdditionalOutputs, AsyncStreamHandler, wait_for_item, audio_to_int16
from reachy_mini_gemini_live.robot_health import CERT_WARN_DAYS, wif_cert_days_left
from reachy_mini_gemini_live.tools.core_tools import (
    ToolDependencies,
    get_tool_specs,
)
from reachy_mini_gemini_live.tools.mcp_client import MCPClientPool
from reachy_mini_gemini_live.tools.mcp_adapter import register_mcp_tools, deregister_mcp_tools
from reachy_mini_gemini_live.camera_frame_encoding import encode_bgr_frame_as_jpeg
from reachy_mini_gemini_live.tools.background_tool_manager import (
    ToolCallRoutine,
    ToolNotification,
    BackgroundToolManager,
)


logger = logging.getLogger(__name__)

GEMINI_INPUT_SAMPLE_RATE: Final[int] = 16000
GEMINI_OUTPUT_SAMPLE_RATE: Final[int] = 24000
_MID_SESSION_SAVE_EVERY_N_TURNS: Final[int] = 20
# With no face in view (dark room, nobody there) the identification instructions make the
# model call the camera over and over — 104 calls in a minute in one eval. The live video
# already streams at 1 FPS, so repeat snapshots within this window are answered, not taken.
CAMERA_COOLDOWN_S: Final[float] = 30.0
# Pin speech and input transcription to one language so TV audio or accents don't flip the
# reply language mid-conversation. REACHY_MINI_LANGUAGE="" leaves it to auto-detection.
DEFAULT_LANGUAGE: Final[str] = "en-US"


def _profile_memory_enabled(profile: str) -> bool:
    """Return True if the profile opts in to memory via memory.txt with enabled=true."""
    try:
        f = config.PROFILES_DIRECTORY / profile / "memory.txt"
        if f.exists():
            return f.read_text(encoding="utf-8").strip().lower() in {"enabled=true", "true", "1", "yes"}
    except Exception:
        pass
    return False


def _tool_specs_to_gemini(specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert internal tool specs to Gemini function_declarations format.

    Internal tool specs use a JSON-Schema-flavored layout:
        {"name": "...", "description": "...", "parameters": {...}}
    Gemini accepts the same shape but requires uppercase JSON Schema type
    names (STRING, NUMBER, OBJECT, ARRAY, BOOLEAN, INTEGER).
    """
    declarations = []
    for spec in specs:
        decl: Dict[str, Any] = {
            "name": spec["name"],
        }
        if "description" in spec:
            decl["description"] = spec["description"]
        if "parameters" in spec and spec["parameters"]:
            decl["parameters"] = _convert_schema_types(spec["parameters"])
        declarations.append(decl)
    return declarations


def _convert_schema_types(schema: Any) -> Any:
    """Recursively convert JSON Schema type strings to Gemini uppercase format."""
    if not isinstance(schema, dict):
        return schema

    result = dict(schema)

    # Convert type field to uppercase
    if "type" in result:
        type_map = {
            "string": "STRING",
            "number": "NUMBER",
            "integer": "INTEGER",
            "boolean": "BOOLEAN",
            "array": "ARRAY",
            "object": "OBJECT",
        }
        t = result["type"]
        if isinstance(t, str):
            result["type"] = type_map.get(t.lower(), t.upper())

    # Recurse into properties
    if "properties" in result and isinstance(result["properties"], dict):
        result["properties"] = {k: _convert_schema_types(v) for k, v in result["properties"].items()}

    # Recurse into items (for arrays)
    if "items" in result:
        result["items"] = _convert_schema_types(result["items"])

    # Remove fields not supported by Gemini
    for unsupported_key in ("additionalProperties",):
        result.pop(unsupported_key, None)

    return result


# Non-answers Gemini Live speaks or transcribes instead of staying silent: "---", "・",
# "<!-- No response required -->", "(No response needed)", "<no speech detected>",
# "<no speech>{pause}". Markup is stripped first; nothing alphanumeric left = not speech.
_PLACEHOLDER_MARKUP_RE = re.compile(
    r"<!--.*?-->|<[^<>]*>|\{[^{}]*\}|\(\s*(no response|no reply|silence|silent)[^)]*\)", re.I | re.S
)


def _is_placeholder(text: str) -> bool:
    """Return True if an assistant transcript is a non-answer rather than real speech."""
    return not any(c.isalnum() for c in _PLACEHOLDER_MARKUP_RE.sub("", text))


def _request_app_restart() -> None:
    """Ask the daemon's launcher to restart this app (blocking)."""
    try:
        import urllib.request as _ur

        req = _ur.Request("http://localhost:8000/api/apps/restart-current-app", data=b"", method="POST")
        _ur.urlopen(req, timeout=1)
    except Exception:
        pass  # expected — launcher kills us before responding


def _env_flag(name: str, default: bool) -> bool:
    """Read a boolean env var ("1/true/yes/on" or "0/false/no/off")."""
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _resolve_gemini_voice(profile_voice: str) -> str:
    """Return a valid Gemini voice name, falling back to the default."""
    return resolve_voice(profile_voice)


class GeminiLiveHandler(AsyncStreamHandler):
    """Gemini Live API handler (headless LocalStream or fastrtc Stream in --gradio mode)."""

    def __init__(self, deps: ToolDependencies, gradio_mode: bool = False, instance_path: Optional[str] = None):
        """Initialize the handler."""
        super().__init__(
            expected_layout="mono",
            output_sample_rate=GEMINI_OUTPUT_SAMPLE_RATE,
            input_sample_rate=GEMINI_INPUT_SAMPLE_RATE,
        )

        self.deps = deps
        # Wire the sleep callback so the go_to_sleep tool can request it
        self.deps.request_sleep = self.sleep
        self.gradio_mode = gradio_mode
        self.instance_path = instance_path
        self._voice_override: str | None = None

        self.session: Any = None  # google.genai live session
        self.output_queue: "asyncio.Queue[Tuple[int, NDArray[np.int16]] | AdditionalOutputs]" = asyncio.Queue()

        self.last_activity_time = asyncio.get_event_loop().time()
        self.start_time = asyncio.get_event_loop().time()
        self._idle_nudges = _env_flag("REACHY_MINI_IDLE_NUDGES", False)
        self.is_idle_tool_call = False

        # Track API key source (env vs textbox)
        self._key_source: Literal["env", "textbox"] = "env"
        self._provided_api_key: str | None = None

        # Internal lifecycle flags
        self._connected_event: asyncio.Event = asyncio.Event()
        # Session resumption: the latest handle from SessionResumptionUpdate lets an
        # unexpected disconnect reconnect into the same conversation (transcript and
        # model context intact). Cleared on deliberate restarts so those start fresh.
        self._resume_handle: str | None = None
        self._session_connected: bool = False

        # Background tool manager
        self.tool_manager = BackgroundToolManager()

        # Stop event for the receive loop
        self._stop_event: asyncio.Event = asyncio.Event()
        # Sleep/wake state — when sleeping the Live WebSocket is closed to stop billing
        self._sleeping: bool = False
        self._wake_event: asyncio.Event = asyncio.Event()
        self._wake_event.set()  # start awake
        # Edge Impulse wake word detection (luisomoreau/hey_reachy_wake_word_detection)
        self._ei_thread: object | None = None
        self._ei_stop: "threading.Event | None" = None
        self._ei_last_activity: float = 0.0
        # Consecutive-window confirmation is available but off by default: measured on the
        # robot, a clear "Hey Reachy" usually peaks in a single 2s window (0.5s slide), just
        # like TV false triggers do — so the false-wake guard below is the main defence.
        self._ei_threshold: float = float(os.environ.get("REACHY_MINI_WAKE_THRESHOLD", "0.7"))
        self._ei_confirm_windows: int = max(1, int(os.environ.get("REACHY_MINI_WAKE_CONFIRM_WINDOWS", "1")))
        # After a wake, go back to sleep if nobody speaks within this many seconds (0 = off).
        self._false_wake_timeout_s: float = float(os.environ.get("REACHY_MINI_FALSE_WAKE_SECONDS", "20"))
        self._user_spoke_since_wake: bool = True
        from reachy_mini_gemini_live.config import REACHY_MINI_DIR
        from reachy_mini_gemini_live.wake_clips import recorder_from_env

        self._wake_clips = recorder_from_env(REACHY_MINI_DIR)
        self._last_wake_clip: Any = None
        import pathlib as _pl

        self._ei_model_path = str(
            _pl.Path(__file__).parent / "models" / "hey-reachy-wake-word-detection-linux-aarch64.eim"
        )
        self._pending_user_transcript_chunks: list[str] = []
        self._pending_assistant_transcript_chunks: list[str] = []
        self._listening_state = False

        # Canned-refusal loop detection. When Gemini's safety filter trips during a
        # Live session it streams the same stock refusal repeatedly without recovery.
        # Track consecutive identical assistant utterances and force-restart the
        # session before the user gives up.
        self._last_assistant_text: str = ""
        self._last_camera_call: float = float("-inf")
        self._repeat_assistant_count: int = 0

        # Memory — initialised once, shared via deps so the remember tool can access it
        self._memory = create_memory_backend()
        self._session_transcript: list[dict] = []
        self._turn_count: int = 0
        self._memory_enabled: bool = False  # set per-session from profile's memory.txt
        self.deps.memory_backend = self._memory
        # One MCP pool per profile, reused across sessions: registered MCP tools keep a
        # reference to the pool they were created with, so a fresh pool per session
        # would leave them calling a stale one and leak its connections.
        self._mcp_pool: MCPClientPool | None = None
        self._mcp_pool_profile: str | None = None

    def copy(self) -> "GeminiLiveHandler":
        """Create a copy of the handler."""
        return GeminiLiveHandler(self.deps, self.gradio_mode, self.instance_path)

    def _set_listening_state(self, listening: bool) -> None:
        """Avoid queueing redundant listening-state updates."""
        if self._listening_state == listening:
            return
        self._listening_state = listening
        self.deps.movement_manager.set_listening(listening)

    async def _flush_transcript_chunks(self, role: str, chunks: list[str]) -> None:
        """Emit one finalized transcript message for the current turn."""
        if not chunks:
            return

        transcript = "".join(chunks).strip()
        chunks.clear()
        if not transcript:
            return

        # Drop assistant messages that are raw tool result echoes or memory context dumps.
        # Gemini sometimes outputs tool results or injected context verbatim as text.
        if role == "assistant":
            s = transcript.strip()
            # Full JSON object or array
            if (s.startswith("{") or s.startswith("[{")) and s.endswith("}"):
                logger.debug("Dropping JSON tool echo: %.80s", s)
                return
            # Partial JSON / memory context fragments (starts with quoted key)
            if s.startswith('"') and any(k in s for k in ('"fact":', '"memory_id":', '"status":', '"matches":')):
                logger.debug("Dropping JSON fragment echo: %.80s", s)
                return
            # Placeholders never reach the dashboard or the stuck-response detector:
            # two identical ones used to force a session restart (and a re-greeting).
            if _is_placeholder(s):
                logger.info("Dropping placeholder non-answer: %.80r", s)
                return

        self._session_transcript.append({"role": role, "content": transcript})
        await self.output_queue.put(AdditionalOutputs({"role": role, "content": transcript}))

        if role == "assistant":
            normalized = transcript.strip().lower().rstrip(".!? ")
            CANNED_REFUSAL = "i'm just a language model and can't help with that"
            if normalized == self._last_assistant_text and normalized:
                self._repeat_assistant_count += 1
            else:
                self._last_assistant_text = normalized
                self._repeat_assistant_count = 1
            if self._repeat_assistant_count >= 2 or normalized == CANNED_REFUSAL:
                logger.warning(
                    "Detected stuck assistant response (%dx %r) — restarting Gemini Live session",
                    self._repeat_assistant_count,
                    transcript[:80],
                )
                self._last_assistant_text = ""
                self._repeat_assistant_count = 0
                asyncio.create_task(self._restart_session(), name="gemini-safety-recover")

    async def _mark_model_response_started(self) -> None:
        """Switch out of user-listening mode when the model begins responding."""
        await self._flush_transcript_chunks("user", self._pending_user_transcript_chunks)
        self._set_listening_state(False)

    async def _handle_interruption(self) -> None:
        """Stop current playback and preserve any transcript already spoken."""
        logger.debug("Gemini: user interrupted")
        await self._flush_transcript_chunks("assistant", self._pending_assistant_transcript_chunks)
        if hasattr(self, "_clear_queue") and callable(self._clear_queue):
            self._clear_queue()
        if self.deps.head_wobbler is not None:
            self.deps.head_wobbler.reset()
        self._set_listening_state(True)

    async def _handle_turn_complete(self) -> None:
        """Finalize the current turn and restore post-speech motion state."""
        logger.debug("Gemini turn complete")
        await self._flush_transcript_chunks("user", self._pending_user_transcript_chunks)
        await self._flush_transcript_chunks("assistant", self._pending_assistant_transcript_chunks)
        self._set_listening_state(False)
        if self.deps.head_wobbler is not None:
            self.deps.head_wobbler.request_reset_after_current_audio()
        self._turn_count += 1
        # An idle nudge answered with speech instead of a tool call must not mark the
        # next real tool call as idle.
        self.is_idle_tool_call = False
        if self._memory_enabled and self._turn_count % _MID_SESSION_SAVE_EVERY_N_TURNS == 0:
            asyncio.create_task(self._mid_session_memory_save())

    async def _save_transcript(self, transcript: list[dict], reason: str) -> str:
        """Send a transcript to memory, log the real outcome, and flag failures on the dashboard."""
        profile = self.deps.active_profile or config.REACHY_MINI_CUSTOM_PROFILE or "default"
        user_id = self.deps.active_user or get_active_user()
        try:
            result = await self._memory.save_session(profile, transcript, user_id=user_id)
        except Exception as exc:
            logger.warning("Memory: %s save failed: %s", reason, exc)
            result = "failed"
        if result == "failed":
            logger.warning("Memory: %s save FAILED (user=%r profile=%r)", reason, user_id, profile)
            await self.output_queue.put(AdditionalOutputs({"role": "memory", "content": "⚠️ memory save failed"}))
        else:
            logger.info("Memory: %s save %s (user=%r profile=%r)", reason, result, user_id, profile)
        return result

    async def _session_notes(self, user_id: str, memory_error: str | None) -> str:
        """Session-start guidance: who we're probably talking to, and whether memory works."""
        notes: list[str] = []
        if self._memory_enabled:
            if user_id == DEFAULT_USER_ID:
                notes.append(
                    "You don't know who you're talking to yet. Early on, ask their name "
                    "and call identify_user so you remember the right person."
                )
            else:
                notes.append(
                    f"The last person who talked with you was {user_id}, and any memories above are theirs. "
                    "Other people may use you too, so don't assume. If anything suggests a different "
                    "person (another name, a different voice, or you're unsure), ask who you're "
                    "talking to and call identify_user before relying on those memories."
                )
            if memory_error:
                logger.warning("Memory: unavailable at session start: %s", memory_error)
                notes.append(
                    "Your long-term memory is unavailable right now because of a technical problem. "
                    "Briefly tell the user at the start that you may not remember past conversations "
                    "and that this one may not be saved."
                )
            days = await asyncio.get_running_loop().run_in_executor(None, wif_cert_days_left)
            if days is not None and days <= CERT_WARN_DAYS:
                logger.warning("Memory: Workload Identity certificate expires in %d days — renew it", days)
                await self.output_queue.put(
                    AdditionalOutputs(
                        {"role": "memory", "content": f"⚠️ memory certificate expires in {max(days, 0)} days"}
                    )
                )
                notes.append(
                    f"The certificate your memory uses to sign in expires in {max(days, 0)} days. "
                    "Mention once that it needs renewing so your memory keeps working."
                )
        if not notes:
            return ""
        return "<session_notes>\n" + "\n".join(f"- {n}" for n in notes) + "\n</session_notes>"

    async def _mcp_pool_for(self, profile: str) -> MCPClientPool:
        """Return the MCP pool for this profile, closing the previous one on a profile switch."""
        if self._mcp_pool is not None and self._mcp_pool_profile == profile:
            return self._mcp_pool
        if self._mcp_pool is not None:
            deregister_mcp_tools(self._mcp_pool)
            await self._mcp_pool.close()
        self._mcp_pool = MCPClientPool.from_profile(profile, config.PROFILES_DIRECTORY)
        self._mcp_pool_profile = profile
        return self._mcp_pool

    async def _mid_session_memory_save(self) -> None:
        """Fire-and-forget memory save triggered every N turns for long sessions."""
        profile = self.deps.active_profile or config.REACHY_MINI_CUSTOM_PROFILE or "default"
        user_id = self.deps.active_user or get_active_user()
        snapshot = list(self._session_transcript)
        logger.info("Memory: mid-session save at turn %d (user=%r profile=%r)", self._turn_count, user_id, profile)
        if await self._save_transcript(snapshot, reason="mid-session") == "saved":
            await self.output_queue.put(
                AdditionalOutputs({"role": "memory", "content": "memories saved (mid-session)"})
            )

    async def apply_personality(self, profile: str | None) -> str:
        """Apply a new personality (profile) at runtime.

        For Gemini Live, we must restart the session since there's no
        session.update equivalent.
        """
        try:
            from reachy_mini_gemini_live.config import set_custom_profile

            set_custom_profile(profile)
            self._voice_override = None
            self.deps.active_profile = profile or "default"
            logger.info("Set custom profile to %r", profile)

            try:
                _ = get_session_instructions()
                _ = get_session_voice()
            except BaseException as e:
                logger.error("Failed to resolve personality content: %s", e)
                return f"Failed to apply personality: {e}"

            # Force a restart to apply new config
            if self.session is not None:
                try:
                    await self._restart_session()
                    return "Applied personality and restarted Gemini session."
                except Exception as e:
                    logger.warning("Failed to restart session after apply: %s", e)
                    return "Applied personality. Will take effect on next connection."
            else:
                return "Applied personality. Will take effect on next connection."
        except Exception as e:
            logger.error("Error applying personality '%s': %s", profile, e)
            return f"Failed to apply personality: {e}"

    async def change_voice(self, voice: str) -> str:
        """Change only the voice and restart the session."""
        self._voice_override = voice
        if getattr(self, "client", None) is not None:
            try:
                await self._restart_session()
                return f"Voice changed to {voice}."
            except Exception as e:
                logger.warning("Failed to restart session for voice change: %s", e)
                return "Voice change failed. Will take effect on next connection."
        return "Voice changed. Will take effect on next connection."

    def get_current_voice(self) -> str:
        """Return the resolved Gemini voice currently selected for this handler."""
        return _resolve_gemini_voice(self._voice_override or get_session_voice())

    async def sleep(self) -> None:
        """Enter sleep mode — close the Live WebSocket to stop billing."""
        if self._sleeping:
            return
        logger.info("Entering sleep mode")
        self._sleeping = True
        self._resume_handle = None
        await self.output_queue.put(AdditionalOutputs({"role": "memory", "content": "💤 sleeping"}))
        # Close session first — teardown resets to neutral position
        if self.session:
            try:
                await self.session.close()
            except Exception:
                pass
        # Save the conversation now: waking reconnects in-process with a fresh
        # transcript, so nothing else would persist what was said before sleep.
        if self._session_transcript and self._memory_enabled:
            snapshot, self._session_transcript = self._session_transcript, []
            asyncio.create_task(self._save_transcript(snapshot, reason="sleep"), name="memory-save-on-sleep")
        # Wait for neutral reset to complete, then silently move to sleep position (no sound)
        await asyncio.sleep(3.0)
        robot = getattr(self.deps, "reachy_mini", None)
        if robot is not None and self._sleeping:
            # Silence the movement loop first, otherwise its breathing/neutral targets
            # pull the head back up between gotos and the head bobs.
            self.deps.movement_manager.pause_output()
            try:
                from reachy_mini.reachy_mini import SLEEP_HEAD_POSE, SLEEP_ANTENNAS_JOINT_POSITIONS

                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: robot.goto_target(SLEEP_HEAD_POSE, antennas=SLEEP_ANTENNAS_JOINT_POSITIONS, duration=2),
                )
            except Exception as e:
                logger.debug("sleep pose error: %s", e)
        deep_sleep = getattr(self.deps, "deep_sleep", None)
        if deep_sleep is not None and self._sleeping:
            await asyncio.get_event_loop().run_in_executor(None, deep_sleep.enter)
        # Start Edge Impulse wake word detection + watchdog
        self._start_ei_detection(asyncio.get_event_loop())
        asyncio.create_task(self._ei_watchdog(asyncio.get_event_loop()), name="ei-watchdog")

    async def _ei_watchdog(self, loop: asyncio.AbstractEventLoop) -> None:
        """Restart EI detection if the audio stream freezes (pipewire routing drop).

        The EI thread's stream.read() blocks indefinitely when pipewire loses
        its route. The watchdog detects this via heartbeat staleness and restarts
        the detection thread so wake-word never stops working silently.
        """
        import time as _time

        STALE_THRESHOLD = 90.0  # seconds without audio before assuming freeze
        CHECK_INTERVAL = 30.0
        while self._sleeping and not self._stop_event.is_set():
            await asyncio.sleep(CHECK_INTERVAL)
            if not self._sleeping:
                break
            age = _time.monotonic() - self._ei_last_activity
            if self._ei_last_activity > 0 and age > STALE_THRESHOLD:
                logger.warning("EI watchdog: no audio for %.0fs — pipewire may be frozen, restarting detection", age)
                if self._ei_stop is not None:
                    self._ei_stop.set()
                await asyncio.sleep(6.0)  # give thread time to exit via timeout
                if self._sleeping and not self._stop_event.is_set():
                    self._start_ei_detection(loop)

    async def wake(self, profile: str | None = None) -> None:
        """Exit sleep mode and reconnect in-process; restart the app only on a profile switch."""
        self._stop_ei_detection()
        logger.info("Waking up (profile=%r)", profile)
        profile_switch = bool(profile and profile != self.deps.active_profile)
        if profile_switch:
            from reachy_mini_gemini_live.config import set_custom_profile

            set_custom_profile(profile)
            self.deps.active_profile = profile
        self._sleeping = False
        deep_sleep = getattr(self.deps, "deep_sleep", None)
        if deep_sleep is not None and not await asyncio.get_event_loop().run_in_executor(None, deep_sleep.exit):
            logger.error("Media did not come back after deep sleep — requesting app restart via launcher")
            await asyncio.get_event_loop().run_in_executor(None, _request_app_restart)
            return
        robot = getattr(self.deps, "reachy_mini", None)
        if robot is not None:
            # Movement output is still paused from sleep(), so the wake move owns the motors.
            try:
                await asyncio.get_event_loop().run_in_executor(None, robot.wake_up)
            except Exception as e:
                logger.debug("wake_up move error: %s", e)
        self.deps.movement_manager.resume_output()
        self._user_spoke_since_wake = False
        if self._false_wake_timeout_s > 0:
            asyncio.create_task(self._false_wake_guard(), name="false-wake-guard")
        self._wake_event.set()
        if not profile_switch:
            await self.output_queue.put(AdditionalOutputs({"role": "memory", "content": "▶ awake"}))
            return
        # Profile tools are registered at import, so a different profile needs a fresh process.
        logger.info("Profile switch on wake — requesting app restart via launcher")
        await asyncio.get_event_loop().run_in_executor(None, _request_app_restart)
        await self.output_queue.put(AdditionalOutputs({"role": "memory", "content": "▶ awake"}))

    async def _false_wake_guard(self) -> None:
        """Go back to sleep if the wake word was a false trigger (nobody talks to us)."""
        await asyncio.sleep(self._false_wake_timeout_s)
        if self._wake_clips is not None and self._last_wake_clip is not None:
            self._wake_clips.label(self._last_wake_clip, "real" if self._user_spoke_since_wake else "false")
            self._last_wake_clip = None
        if not self._sleeping and not self._user_spoke_since_wake and not self._stop_event.is_set():
            logger.warning(
                "No speech %.0fs after waking — treating as a false wake, back to sleep", self._false_wake_timeout_s
            )
            await self.sleep()

    async def start_up(self) -> None:
        """Start the handler with retries on unexpected closure."""
        # Live runs on the AI Studio endpoint (generativelanguage.googleapis.com) with
        # GEMINI_API_KEY. SA key handles Agent Engine memory separately.
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set. Complete setup at the web UI.")
        self.client = genai.Client(
            api_key=api_key,
            http_options={"api_version": "v1beta"},
        )

        while not self._stop_event.is_set():
            # Wait until awake
            if self._sleeping:
                logger.info("Sleep mode — waiting for wake signal")
                self._wake_event.clear()
                await self._wake_event.wait()
                if self._stop_event.is_set():
                    break
                logger.info("Waking up — starting new session")

            self._session_connected = False
            try:
                await self._run_live_session()
            except Exception as e:
                if self._stop_event.is_set() or self._sleeping:
                    pass  # expected — don't log as error
                else:
                    if not self._session_connected and self._resume_handle:
                        # Handle expired or was rejected — fall back to a fresh session.
                        logger.warning("Session resume failed; next attempt starts a fresh session")
                        self._resume_handle = None
                    logger.warning("Gemini Live session closed unexpectedly: %s — reconnecting in 2s", e)
                    await asyncio.sleep(2)
            finally:
                self.session = None
                try:
                    self._connected_event.clear()
                except Exception:
                    pass

    async def _restart_session(self) -> None:
        """Force-close the current session and start a fresh one."""
        self._resume_handle = None
        try:
            if self.session is not None:
                try:
                    await self.session.close()
                except Exception:
                    pass
                finally:
                    self.session = None

            if getattr(self, "client", None) is None:
                logger.warning("Cannot restart: Gemini client not initialized yet.")
                return

            try:
                self._connected_event.clear()
            except Exception:
                pass
            self._stop_event.set()  # Signal the old receive loop to stop
            await asyncio.sleep(0.1)
            self._stop_event.clear()
            asyncio.create_task(self.start_up(), name="gemini-live-restart")
            try:
                await asyncio.wait_for(self._connected_event.wait(), timeout=5.0)
                logger.info("Gemini Live session restarted and connected.")
            except asyncio.TimeoutError:
                logger.warning("Gemini Live session restart timed out; continuing in background.")
        except Exception as e:
            logger.warning("_restart_session failed: %s", e)

    def _build_live_config(self, memory_context: str = "") -> types.LiveConnectConfig:
        """Build the LiveConnectConfig for a Gemini Live session."""
        instructions = get_session_instructions()
        if memory_context:
            instructions = f"{instructions}\n\n{memory_context}"
        voice = _resolve_gemini_voice(self._voice_override or get_session_voice())

        # Convert internal tool specs to Gemini function declarations
        tool_specs = get_tool_specs()
        function_declarations = _tool_specs_to_gemini(tool_specs)

        # google_search is correct for current models (gemini-3.1+).
        # google_search_retrieval is for older models only — docs confirm this.
        tools_config: List[Dict[str, Any]] = []
        if get_session_grounding():
            tools_config.append({"google_search": {}})
            logger.warning("Google Search grounding enabled for this session")
        if function_declarations:
            tools_config.append({"function_declarations": function_declarations})

        # Note: safety_settings are not supported by the Gemini Live API
        # (https://firebase.google.com/docs/ai-logic/safety-settings). Sending the field
        # makes the server reject the setup with 1007 and the session reconnect-loops.
        # Family/kid over-refusal is mitigated via the system prompt and the
        # canned-refusal auto-recover in _emit_transcript instead.

        language = os.environ.get("REACHY_MINI_LANGUAGE", DEFAULT_LANGUAGE).strip() or None
        live_config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=types.Content(parts=[types.Part(text=instructions)]),
            speech_config=types.SpeechConfig(
                language_code=language,
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice,
                    ),
                ),
            ),
            tools=tools_config,  # type: ignore[arg-type]
            input_audio_transcription=types.AudioTranscriptionConfig(
                language_codes=[language] if language else None,
            ),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            context_window_compression=types.ContextWindowCompressionConfig(
                trigger_tokens=104857,
                sliding_window=types.SlidingWindow(target_tokens=52428),
            ),
            session_resumption=types.SessionResumptionConfig(handle=self._resume_handle),
        )

        logger.info(
            "Gemini Live config: model=%r voice=%r tools=%d",
            config.MODEL_NAME,
            voice,
            len(function_declarations),
        )
        return live_config

    async def _handle_tool_call(self, response: Any) -> None:
        """Process a tool_call from Gemini and send the response back."""
        if not response.tool_call or not response.tool_call.function_calls:
            return

        for fc in response.tool_call.function_calls:
            tool_name = fc.name
            call_id = fc.id or str(uuid.uuid4())
            args_dict = dict(fc.args) if fc.args else {}
            args_json_str = json.dumps(args_dict)

            logger.info(
                "Gemini tool call: tool_name=%r, call_id=%s, is_idle=%s, args=%s",
                tool_name,
                call_id,
                self.is_idle_tool_call,
                args_json_str,
            )

            if tool_name == "camera":
                now = asyncio.get_event_loop().time()
                if now - self._last_camera_call < CAMERA_COOLDOWN_S:
                    logger.info("Camera call within %.0fs cooldown — not taking another snapshot", CAMERA_COOLDOWN_S)
                    if self.session is not None:
                        await self.session.send_tool_response(
                            function_responses=[
                                types.FunctionResponse(
                                    id=call_id,
                                    name=tool_name,
                                    response={
                                        "status": "skipped",
                                        "reason": "A snapshot was taken seconds ago and the live video shows the "
                                        "scene. Do not call camera again now; if no face is visible, ask by voice.",
                                        "scheduling": "SILENT",
                                    },
                                )
                            ]
                        )
                    continue
                self._last_camera_call = now

            bg_tool = await self.tool_manager.start_tool(
                call_id=call_id,
                tool_call_routine=ToolCallRoutine(
                    tool_name=tool_name,
                    args_json_str=args_json_str,
                    deps=self.deps,
                ),
                is_idle_tool_call=self.is_idle_tool_call,
            )

            # Silent tools don't surface in the monitor
            _SILENT_TOOLS = {"remember", "identify_user", "do_nothing"}
            if tool_name not in _SILENT_TOOLS:
                try:
                    args_display = json.loads(args_json_str) if args_json_str else {}
                    summary = ", ".join(f"{k}={v!r}" for k, v in args_display.items()) if args_display else ""
                    label = f"{tool_name}({summary})" if summary else tool_name
                except Exception:
                    label = tool_name
                await self.output_queue.put(
                    AdditionalOutputs({"role": "tool", "content": label}),
                )

            if self.is_idle_tool_call:
                self.is_idle_tool_call = False

            logger.info("Started background tool: %s (id=%s, call_id=%s)", tool_name, bg_tool.tool_id, call_id)

    async def _handle_tool_result(self, bg_tool: ToolNotification) -> None:
        """Process the result of a completed tool and send it back to Gemini."""
        if bg_tool.error is not None:
            logger.error("Tool '%s' (id=%s) failed: %s", bg_tool.tool_name, bg_tool.id, bg_tool.error)
            tool_result = {"error": bg_tool.error}
        elif bg_tool.result is not None:
            tool_result = bg_tool.result
            logger.info("Tool '%s' (id=%s) succeeded.", bg_tool.tool_name, bg_tool.id)
        else:
            logger.warning("Tool '%s' (id=%s) returned no result and no error", bg_tool.tool_name, bg_tool.id)
            tool_result = {"error": "No result returned from tool execution"}

        if not self.session:
            logger.warning("Connection closed during tool '%s' execution", bg_tool.tool_name)
            return

        try:
            if bg_tool.tool_name == "camera" and isinstance(tool_result, dict) and "b64_im" in tool_result:
                b64_im = tool_result.pop("b64_im")
                if not tool_result:
                    tool_result = {"status": "image_captured"}

                try:
                    if isinstance(b64_im, str):
                        image_bytes = base64.b64decode(b64_im)
                    else:
                        image_bytes = bytes(b64_im)
                    await self.session.send_realtime_input(video=types.Blob(data=image_bytes, mime_type="image/jpeg"))
                    logger.info("Pushed camera snapshot to Gemini via realtime video input")
                except Exception as ve:
                    logger.warning("Failed to push camera snapshot to Gemini: %s", ve)

            # Scheduling hints for when NON_BLOCKING is supported on this model.
            # SILENT: background ops the user doesn't need announced (memory, motion, tracking)
            # WHEN_IDLE: announce at next natural pause (camera result, task status)
            # INTERRUPT: default for explicit user-requested tool calls
            _SILENT_RESPONSE_TOOLS = {
                "remember",
                "identify_user",
                "do_nothing",
                "dance",
                "stop_dance",
                "play_emotion",
                "stop_emotion",
                "head_tracking",
                "move_head",
            }
            _WHEN_IDLE_RESPONSE_TOOLS = {"camera", "task_status", "task_cancel"}
            if bg_tool.tool_name in _SILENT_RESPONSE_TOOLS:
                scheduling = "SILENT"
            elif bg_tool.tool_name in _WHEN_IDLE_RESPONSE_TOOLS:
                scheduling = "WHEN_IDLE"
            else:
                scheduling = "INTERRUPT"

            tool_result_with_scheduling = (
                {**tool_result, "scheduling": scheduling} if isinstance(tool_result, dict) else tool_result
            )

            function_response = types.FunctionResponse(
                id=bg_tool.id if isinstance(bg_tool.id, str) else str(bg_tool.id),
                name=bg_tool.tool_name,
                response=tool_result_with_scheduling,
            )
            await self.session.send_tool_response(function_responses=[function_response])

            if (
                bg_tool.tool_name == "remember"
                and isinstance(tool_result, dict)
                and tool_result.get("status") == "remembered"
            ):
                fact = tool_result.get("fact", "")
                preview = fact[:60] + "…" if len(fact) > 60 else fact
                await self.output_queue.put(AdditionalOutputs({"role": "memory", "content": f"saved: {preview}"}))

            if bg_tool.tool_name == "recall" and isinstance(tool_result, dict):
                matches = tool_result.get("matches", [])
                query = tool_result.get("query", "")
                count = len(matches)
                label = f"🔎 {count} {'match' if count == 1 else 'matches'}"
                lines = []
                for i, m in enumerate(matches, 1):
                    fact = m.get("fact", "")
                    age = m.get("age_days", "")
                    age_str = f" · {age:.0f}d ago" if isinstance(age, (int, float)) else ""
                    lines.append(f"{i}. {fact[:80]}{'…' if len(fact) > 80 else ''}{age_str}")
                detail = f'query: "{query}"\n' + "\n".join(lines) if lines else f'query: "{query}" — no matches'
                await self.output_queue.put(
                    AdditionalOutputs(
                        {
                            "role": "recall_result",
                            "content": label,
                            "detail": detail,
                        }
                    )
                )
            # The raw-result echo renders as a collapsible "Used tool" panel in the Gradio UI;
            # the headless dashboard would show it as JSON next to the tool badge, so skip it there.
            _SUPPRESS_CONSOLE_TOOLS = {
                "remember",
                "identify_user",
                "do_nothing",
                "recall",
                "dance",
                "stop_dance",
                "play_emotion",
                "stop_emotion",
                "head_tracking",
                "move_head",
            }
            if self.gradio_mode and bg_tool.tool_name not in _SUPPRESS_CONSOLE_TOOLS:
                await self.output_queue.put(
                    AdditionalOutputs(
                        {
                            "role": "assistant",
                            "content": json.dumps(tool_result),
                            "metadata": {
                                "title": f"🛠️ Used tool {bg_tool.tool_name}",
                                "status": "done",
                            },
                        },
                    ),
                )

            if self.gradio_mode and bg_tool.tool_name == "camera" and self.deps.camera_worker is not None:
                import gradio as gr

                np_img = self.deps.camera_worker.get_latest_frame()
                if np_img is not None:
                    rgb_frame = np.ascontiguousarray(np_img[..., ::-1])
                else:
                    rgb_frame = None
                img = gr.Image(value=rgb_frame)
                await self.output_queue.put(
                    AdditionalOutputs({"role": "assistant", "content": img}),
                )

        except Exception as e:
            logger.warning("Error sending tool result to Gemini: %s", e)

    async def _auto_sleep_monitor(self) -> None:
        """Auto-sleep after config.AUTO_SLEEP_MINUTES of no user activity (0 = never)."""
        if config.AUTO_SLEEP_MINUTES <= 0:
            return
        while self.session and not self._stop_event.is_set() and not self._sleeping:
            await asyncio.sleep(30)  # check every 30s
            idle = asyncio.get_event_loop().time() - self.last_activity_time
            timeout_s = config.AUTO_SLEEP_MINUTES * 60
            if idle >= timeout_s:
                logger.info("Auto-sleep: %.0fs idle (limit %ds)", idle, timeout_s)
                await self.sleep()
                return

    async def _video_sender_loop(self) -> None:
        """Send camera frames to Gemini Live at ~1 FPS for continuous visual context.

        Only runs when a camera_worker is available. Frames are JPEG-encoded
        and sent via send_realtime_input(video=...).
        """
        logger.info("Video sender loop started (1 FPS)")
        while not self._stop_event.is_set():
            try:
                if self.session and self.deps.camera_worker is not None:
                    frame = self.deps.camera_worker.get_latest_frame()
                    if frame is not None:
                        jpeg_bytes = encode_bgr_frame_as_jpeg(frame)
                        await self.session.send_realtime_input(
                            video=types.Blob(data=jpeg_bytes, mime_type="image/jpeg")
                        )
            except Exception as e:
                if self._stop_event.is_set():
                    break
                logger.debug("Video sender error (will retry): %s", e)

            await asyncio.sleep(1.0)  # 1 FPS

        logger.info("Video sender loop stopped")

    async def _run_live_session(self) -> None:
        """Establish and manage a single Gemini Live session."""
        profile = self.deps.active_profile or config.REACHY_MINI_CUSTOM_PROFILE or "default"
        self.deps.active_profile = profile  # ensure tools (remember, identify_user) see correct profile
        # Initialise active_user from disk (persists across sessions)
        if not self.deps.active_user:
            self.deps.active_user = get_active_user()
        user_id = self.deps.active_user
        resuming = self._resume_handle is not None
        logger.warning("Session starting: profile=%r user=%r resuming=%s", profile, user_id, resuming)
        if not resuming:
            self._session_transcript = []  # reset for new session
            self._turn_count = 0
            self._last_camera_call = float("-inf")  # a new session may identify the person again

        # Check if memory is enabled for this profile (opt-in via memory.txt)
        self._memory_enabled = _profile_memory_enabled(profile)
        self.deps.memory_enabled = self._memory_enabled
        if not self._memory_enabled:
            logger.info("Memory: disabled for profile=%r (no memory.txt with enabled=true)", profile)

        # Load memory, location, and MCP tools concurrently
        mcp_pool = await self._mcp_pool_for(profile)

        async def _no_memory() -> str:
            return ""

        memory_context, location_context, mcp_specs = await asyncio.gather(
            self._memory.load_context(profile, user_id=user_id) if self._memory_enabled else _no_memory(),
            asyncio.get_running_loop().run_in_executor(None, get_location_context),
            mcp_pool.connect_all(),
        )

        # Sweep ttl-tagged memories that have expired. Runs AFTER load_context
        # so credentials are already primed (avoids a 401 race seen when the
        # sweep fires before any other Vertex call has initialized auth).
        # Fire-and-forget — doesn't block session start.
        if self._memory_enabled and hasattr(self._memory, "purge_expired"):
            asyncio.create_task(
                self._memory.purge_expired(profile, user_id=user_id),
                name="memory-purge-expired",
            )
        self._mcp_pool = mcp_pool
        if mcp_specs:
            register_mcp_tools(mcp_pool, mcp_specs)

        memory_error = getattr(self._memory, "last_error", None) if self._memory_enabled else None
        session_notes = "" if resuming else await self._session_notes(user_id, memory_error)

        # Combine contexts: location first (low-confidence defaults), memory second (overrides)
        extra_context = "\n\n".join(filter(None, [location_context, memory_context, session_notes]))
        logger.warning(
            "Session context: location=%s, memory=%s chars, mcp_tools=%d",
            "ok" if location_context else "empty",
            len(memory_context),
            len(mcp_specs),
        )
        if memory_context:
            # Count approximate memory entries (each starts with a digit + period or dash)
            import re as _re

            mem_count = len(_re.findall(r"(?m)^(?:\d+\.|[-•])\s", memory_context)) or 1
            await self.output_queue.put(
                AdditionalOutputs(
                    {
                        "role": "memory",
                        "content": f"🧠 {mem_count} {'memory' if mem_count == 1 else 'memories'} · {user_id}",
                        "detail": memory_context,
                    }
                )
            )
        else:
            await self.output_queue.put(
                AdditionalOutputs(
                    {
                        "role": "memory",
                        "content": f"⚠️ memory offline · {user_id}" if memory_error else f"🧠 no memories · {user_id}",
                        "detail": memory_error or "",
                    }
                )
            )
        live_config = self._build_live_config(memory_context=extra_context)

        async with self.client.aio.live.connect(
            model=config.MODEL_NAME,
            config=live_config,
        ) as session:
            self.session = session
            self._session_connected = True
            try:
                self._connected_event.set()
            except Exception:
                pass

            logger.info("Gemini Live session connected successfully")

            video_task: asyncio.Task[None] | None = None
            try:
                # Start the background tool manager
                self.tool_manager.start_up(tool_callbacks=[self._handle_tool_result])

                # Start video sender if camera is available
                if self.deps.camera_worker is not None:
                    video_task = asyncio.create_task(self._video_sender_loop(), name="gemini-video-sender")
                    asyncio.create_task(self._auto_sleep_monitor(), name="gemini-auto-sleep")

                # session.receive() yields responses for the current turn then completes.
                # We loop so the session stays alive across multiple conversation turns.
                while not self._stop_event.is_set():
                    try:
                        async for response in session.receive():
                            if self._stop_event.is_set():
                                logger.info("Stop event set, breaking receive loop")
                                break

                            update = response.session_resumption_update
                            if update and update.resumable and update.new_handle:
                                self._resume_handle = update.new_handle
                            if response.go_away:
                                logger.info(
                                    "Gemini Live go_away (time_left=%s) — will resume", response.go_away.time_left
                                )

                            # Handle server content (audio, transcription, interruption)
                            if response.server_content:
                                content = response.server_content

                                # Handle interruption / barge-in
                                if content.interrupted is True:
                                    await self._handle_interruption()

                                # Handle audio output from model
                                if content.model_turn and content.model_turn.parts:
                                    has_audio_part = any(
                                        part.inline_data and part.inline_data.data for part in content.model_turn.parts
                                    )
                                    if has_audio_part:
                                        await self._mark_model_response_started()

                                    for part in content.model_turn.parts:
                                        if part.inline_data and part.inline_data.data:
                                            audio_bytes = part.inline_data.data
                                            if isinstance(audio_bytes, str):
                                                audio_bytes = base64.b64decode(audio_bytes)

                                            if len(audio_bytes) == 0:
                                                continue

                                            audio_array = np.frombuffer(audio_bytes, dtype=np.int16)

                                            if len(audio_array) == 0:
                                                continue

                                            if self.gradio_mode and self.deps.head_wobbler is not None:
                                                self.deps.head_wobbler.feed(
                                                    base64.b64encode(audio_bytes).decode("utf-8")
                                                )

                                            self.last_activity_time = asyncio.get_event_loop().time()

                                            await self.output_queue.put(
                                                (GEMINI_OUTPUT_SAMPLE_RATE, audio_array),
                                            )

                                # Handle input transcription (user speech)
                                if content.input_transcription and content.input_transcription.text:
                                    transcript = content.input_transcription.text
                                    logger.debug("User transcript chunk: %s", transcript)
                                    if len(transcript.strip()) >= 3:
                                        self._user_spoke_since_wake = True
                                    self._pending_user_transcript_chunks.append(transcript)
                                    self._set_listening_state(True)

                                # Handle output transcription (model speech)
                                if content.output_transcription and content.output_transcription.text:
                                    transcript = content.output_transcription.text
                                    logger.debug("Assistant transcript chunk: %s", transcript)
                                    await self._mark_model_response_started()
                                    self._pending_assistant_transcript_chunks.append(transcript)

                                # Detect Google Search grounding — try both content and response
                                gm = getattr(content, "grounding_metadata", None) or getattr(
                                    response, "grounding_metadata", None
                                )
                                if gm:
                                    queries = (
                                        getattr(gm, "web_search_queries", None)
                                        or getattr(gm, "search_queries", None)
                                        or []
                                    )
                                    q_str = ", ".join(f'"{q}"' for q in list(queries)[:2])
                                    logger.warning(
                                        "Google Search used — queries=%r gm_type=%s",
                                        list(queries),
                                        type(gm).__name__,
                                    )
                                    await self.output_queue.put(
                                        AdditionalOutputs({"role": "search", "content": q_str or "search"}),
                                    )

                                # Turn complete
                                if content.turn_complete:
                                    await self._handle_turn_complete()

                            # Handle tool calls
                            if response.tool_call:
                                await self._handle_tool_call(response)

                    except Exception as e:
                        if self._stop_event.is_set():
                            break
                        logger.warning("Receive loop error, restarting Gemini session: %s", e)
                        raise

            finally:
                if video_task is not None:
                    video_task.cancel()
                    try:
                        await video_task
                    except asyncio.CancelledError:
                        pass
                await self.tool_manager.shutdown()

    def _start_ei_detection(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start Edge Impulse wake word detection in a background thread.

        Mirrors luisomoreau/hey_reachy_wake_word_detection exactly — uses
        AudioImpulseRunner with device_id passed to classifier() so the SDK
        never prompts interactively. The .eim binary communicates with the SDK
        via a Unix socket, not stdin/stdout.
        """
        if not os.path.exists(self._ei_model_path):
            logger.warning("Edge Impulse model not found at %s — wake word disabled", self._ei_model_path)
            return
        self._ei_stop = threading.Event()
        stop_event = self._ei_stop

        def _run() -> None:
            try:
                import sounddevice as sd
                from edge_impulse_linux.audio import AudioImpulseRunner

                # Ensure model binary is executable
                mode = os.stat(self._ei_model_path).st_mode
                if not (mode & stat.S_IXUSR):
                    os.chmod(self._ei_model_path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

                with AudioImpulseRunner(self._ei_model_path) as runner:
                    info = runner.init()
                    rate = int(info["model_parameters"]["frequency"])
                    window_size = int(info["model_parameters"]["input_features_count"])
                    logger.info(
                        "Edge Impulse runner loaded: %s / %s (rate=%dHz, window=%d)",
                        info["project"]["owner"],
                        info["project"]["name"],
                        rate,
                        window_size,
                    )

                    # Pick input device + native rate. The model wants 24kHz, but
                    # PortAudio/PyAudio enforce strict rate matching against the
                    # device's actual capabilities. reachymini_audio_src runs at
                    # 16kHz natively; we capture there and resample to 24kHz
                    # before classifying.
                    devices = sd.query_devices()
                    device_id = None
                    device_name = None
                    native_rate = 0
                    native_channels = 1
                    for i, d in enumerate(devices):
                        if d["max_input_channels"] > 0 and "reachymini_audio_src" in d["name"].lower():
                            device_id = i
                            device_name = d["name"]
                            native_rate = int(d["default_samplerate"])
                            native_channels = min(2, int(d["max_input_channels"]))
                            break
                    if device_id is None:
                        for i, d in enumerate(devices):
                            if d["max_input_channels"] > 0:
                                device_id = i
                                device_name = d["name"]
                                native_rate = int(d["default_samplerate"])
                                native_channels = 1
                                break
                    if device_id is None:
                        logger.warning("No audio input device available — wake word disabled")
                        return

                    from math import gcd

                    g = gcd(native_rate, rate)
                    up, down = rate // g, native_rate // g
                    logger.info(
                        "Edge Impulse using sounddevice device %d: %s (native=%dHz/%dch, resampling %d/%d to %dHz)",
                        device_id,
                        device_name,
                        native_rate,
                        native_channels,
                        up,
                        down,
                        rate,
                    )

                    from scipy.signal import resample_poly

                    CHUNK_SIZE = 1024
                    OVERLAP = 0.25
                    slide = max(1, int(window_size * OVERLAP))
                    features = np.array([], dtype=np.int16)
                    hits = 0  # consecutive windows at/above threshold

                    import time as _time
                    import queue as _queue

                    audio_q: "_queue.Queue[bytes]" = _queue.Queue(maxsize=32)

                    def _audio_cb(indata: bytes, frames: int, t: object, status: object) -> None:
                        try:
                            audio_q.put_nowait(bytes(indata))
                        except _queue.Full:
                            pass

                    with sd.RawInputStream(
                        samplerate=native_rate,
                        channels=native_channels,
                        dtype="int16",
                        blocksize=CHUNK_SIZE,
                        device=device_id,
                        callback=_audio_cb,
                    ):
                        while not stop_event.is_set() and self._sleeping:
                            try:
                                audio_bytes = audio_q.get(timeout=5.0)
                            except _queue.Empty:
                                continue  # no audio; check stop_event and loop
                            self._ei_last_activity = _time.monotonic()
                            raw = np.frombuffer(audio_bytes, dtype=np.int16)
                            if native_channels > 1:
                                raw = raw.reshape(-1, native_channels).mean(axis=1).astype(np.int16)
                            if self._wake_clips is not None:
                                self._wake_clips.push(raw, native_rate)
                            if up != down:
                                resampled = np.clip(
                                    resample_poly(raw.astype(np.float32), up, down),
                                    -32768,
                                    32767,
                                ).astype(np.int16)
                            else:
                                resampled = raw
                            features = np.concatenate((features, resampled), axis=0)
                            while len(features) >= window_size:
                                res = runner.classify(features[:window_size].tolist())
                                features = features[slide:]
                                if "classification" in res["result"]:
                                    score = res["result"]["classification"].get("hey_reachy", 0.0)
                                    if score > 0.05:
                                        logger.info("Edge Impulse score: %.3f", score)
                                    hits = hits + 1 if score >= self._ei_threshold else 0
                                    if 0 < hits < self._ei_confirm_windows:
                                        logger.info(
                                            "Wake word candidate %.3f (%d/%d windows)",
                                            score,
                                            hits,
                                            self._ei_confirm_windows,
                                        )
                                    if hits >= self._ei_confirm_windows:
                                        logger.info(
                                            "Hey Reachy detected! score=%.3f (%d windows) — waking up", score, hits
                                        )
                                        self._sleeping = False
                                        stop_event.set()  # exits the while loop, context manager closes stream
                                        if self._wake_clips is not None:
                                            self._last_wake_clip = self._wake_clips.save(score)
                                        self._play_wake_ack()
                                        asyncio.run_coroutine_threadsafe(self.wake(), loop)
                                        return

            except Exception as e:
                logger.warning("Edge Impulse detection error: %s", e)
                import traceback as _tb

                logger.debug(_tb.format_exc())

        self._ei_thread = threading.Thread(target=_run, daemon=True, name="ei-wake-word")
        self._ei_thread.start()
        logger.info("Edge Impulse wake word detection started")

    def _play_wake_ack(self) -> None:
        """Play the rooster crow wake acknowledgement.

        Sound source: Wikimedia Commons "Young rooster crowing" (CC BY-SA 4.0).
        See sounds/CREDITS.md for attribution.
        """
        try:
            import wave
            import pathlib as _pl

            import sounddevice as sd

            wav_path = _pl.Path(__file__).parent / "sounds" / "rooster_crow.wav"
            if not wav_path.is_file():
                logger.warning("Wake ack WAV missing at %s", wav_path)
                return

            with wave.open(str(wav_path), "rb") as wf:
                file_rate = wf.getframerate()
                wav_channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                if sampwidth != 2:
                    logger.warning("Wake ack WAV must be 16-bit PCM (got %d bytes/sample)", sampwidth)
                    return
                pcm_bytes = wf.readframes(wf.getnframes())

            mono = np.frombuffer(pcm_bytes, dtype=np.int16)
            if wav_channels > 1:
                mono = mono.reshape(-1, wav_channels).mean(axis=1).astype(np.int16)

            # Pick output device + its native rate. PortAudio strictly enforces
            # the device's actual sample rate, so we resample to match.
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
                logger.warning("No usable output device for wake ack")
                return

            if file_rate != device_rate:
                from math import gcd

                from scipy.signal import resample_poly

                g = gcd(file_rate, device_rate)
                up, down = device_rate // g, file_rate // g
                resampled = resample_poly(mono.astype(np.float32), up, down)
                mono = np.clip(resampled, -32768, 32767).astype(np.int16)

            audio = np.repeat(mono[:, None], device_channels, axis=1) if device_channels > 1 else mono
            sd.play(audio, samplerate=device_rate, device=device_id, blocking=True)
        except Exception as e:
            logger.warning("Failed to play wake ack: %s", e)

    def _stop_ei_detection(self) -> None:
        """Stop the Edge Impulse wake word detection thread."""
        if self._ei_stop is not None:
            self._ei_stop.set()
        if self._ei_thread is not None and self._ei_thread.is_alive():
            self._ei_thread.join(timeout=2.0)
        self._ei_thread = None
        self._ei_stop = None

    async def receive(self, frame: Tuple[int, NDArray[np.int16]]) -> None:
        """Receive audio frame from microphone and send to Gemini."""
        input_sample_rate, audio_frame = frame

        # During sleep, Edge Impulse handles wake word detection in its own thread
        if self._sleeping:
            return

        if not self.session:
            return

        # Reshape if needed
        if audio_frame.ndim == 2:
            if audio_frame.shape[1] > audio_frame.shape[0]:
                audio_frame = audio_frame.T
            if audio_frame.shape[1] > 1:
                audio_frame = audio_frame[:, 0]

        # Resample to 16kHz if needed
        if GEMINI_INPUT_SAMPLE_RATE != input_sample_rate:
            audio_frame = resample(
                audio_frame,
                int(len(audio_frame) * GEMINI_INPUT_SAMPLE_RATE / input_sample_rate),
            )

        # Cast to int16
        audio_frame = audio_to_int16(audio_frame)

        # Send raw PCM bytes to Gemini
        try:
            pcm_bytes = audio_frame.tobytes()
            await self.session.send_realtime_input(audio=types.Blob(data=pcm_bytes, mime_type="audio/pcm;rate=16000"))
        except Exception as e:
            logger.debug("Dropping audio frame: session not ready (%s)", e)
            return

    async def emit(self) -> Tuple[int, NDArray[np.int16]] | AdditionalOutputs | None:
        """Emit audio frame to be played by the speaker."""
        # Idle nudges make the model answer nobody every 15s ("anything else?", spoken
        # do_nothing reasoning, placeholders), so they're opt-in.
        idle_duration = asyncio.get_event_loop().time() - self.last_activity_time
        if self._idle_nudges and idle_duration > 15.0 and self.deps.movement_manager.is_idle():
            try:
                await self.send_idle_signal(idle_duration)
            except Exception as e:
                logger.warning("Idle signal skipped: %s", e)
                return None
            self.last_activity_time = asyncio.get_event_loop().time()

        return await wait_for_item(self.output_queue)  # type: ignore[no-any-return]

    async def shutdown(self) -> None:
        """Shutdown the handler."""
        self._stop_event.set()

        await self.tool_manager.shutdown()

        if self.session:
            try:
                await self.session.close()
            except Exception as e:
                logger.debug("session.close() error: %s", e)
            finally:
                self.session = None

        # Save transcript to memory — awaited so it completes before teardown
        logger.info("Memory: session ended with %d transcript turns", len(self._session_transcript))
        if self._session_transcript and self._memory_enabled:
            await self._save_transcript(self._session_transcript, reason="shutdown")
        else:
            logger.warning("Memory: no transcript captured — nothing to save")

        # Clean up MCP connections and deregister tools
        if self._mcp_pool is not None:
            deregister_mcp_tools(self._mcp_pool)
            await self._mcp_pool.close()
            self._mcp_pool = None
            self._mcp_pool_profile = None

        # Clear remaining items in the output queue
        while not self.output_queue.empty():
            try:
                self.output_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def format_timestamp(self) -> str:
        """Format current timestamp with date, time, and elapsed seconds."""
        loop_time = asyncio.get_event_loop().time()
        elapsed_seconds = loop_time - self.start_time
        dt = datetime.now()
        return f"[{dt.strftime('%Y-%m-%d %H:%M:%S')} | +{elapsed_seconds:.1f}s]"

    async def send_idle_signal(self, idle_duration: float) -> None:
        """Send an idle signal to Gemini."""
        logger.debug("Sending idle signal")
        self.is_idle_tool_call = True
        timestamp_msg = (
            f"[Idle time update: {self.format_timestamp()} - No activity for {idle_duration:.1f}s] "
            "You've been idle for a while. Feel free to get creative - dance, show an emotion, "
            "look around, do nothing, or just be yourself!"
        )
        if not self.session:
            logger.debug("No session, cannot send idle signal")
            return

        await self.session.send_realtime_input(text=timestamp_msg)

    async def get_available_voices(self) -> list[str]:
        """Return the list of available Gemini voices."""
        from reachy_mini_gemini_live.config import AVAILABLE_VOICES

        return list(AVAILABLE_VOICES)
