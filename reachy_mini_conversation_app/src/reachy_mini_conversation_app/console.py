"""Bidirectional local audio stream with optional settings UI.

In headless mode, there is no Gradio UI. If the selected backend is missing
its required API key, we expose a minimal settings page via the Reachy Mini
Apps settings server so users can pick a backend and provide any missing
credentials.

The settings UI is served from this package's ``static/`` folder. It persists
the selected backend and any provided API keys into the app instance's ``.env``
file when available.
"""

import json
import os
import sys
import time
import asyncio
import logging
from typing import List, Optional
from pathlib import Path

from fastrtc import AdditionalOutputs, audio_to_float32
from scipy.signal import resample

from reachy_mini import ReachyMini
from reachy_mini.media.media_manager import MediaBackend
from reachy_mini_conversation_app.config import (
    AVAILABLE_VOICES,
    GEMINI_BACKEND,
    LOCKED_PROFILE,
    REACHY_MINI_DIR,
    REACHY_MINI_ENV_PATH,
    refresh_runtime_config_from_env,
)

VOICE_STYLES: dict[str, str] = {
    "Achird": "Friendly", "Achernar": "Soft", "Algenib": "Gravelly",
    "Algieba": "Smooth", "Alnilam": "Firm", "Aoede": "Breezy",
    "Autonoe": "Bright", "Callirrhoe": "Easy-going", "Charon": "Informative",
    "Despina": "Smooth", "Enceladus": "Breathy", "Erinome": "Clear",
    "Fenrir": "Excitable", "Gacrux": "Mature", "Iapetus": "Clear",
    "Kore": "Firm", "Laomedeia": "Upbeat", "Leda": "Youthful",
    "Orus": "Firm", "Pulcherrima": "Forward", "Puck": "Upbeat",
    "Rasalgethi": "Informative", "Sadachbia": "Lively", "Sadaltager": "Knowledgeable",
    "Schedar": "Even", "Sulafat": "Warm", "Umbriel": "Easy-going",
    "Vindemiatrix": "Gentle", "Zephyr": "Bright", "Zubenelgenubi": "Casual",
}
from reachy_mini_conversation_app.headless_personality_ui import mount_personality_routes


try:
    from reachy_mini_conversation_app.gemini_live import GeminiLiveHandler
except ImportError:
    GeminiLiveHandler = None  # type: ignore[misc,assignment]


try:
    # FastAPI is provided by the Reachy Mini Apps runtime
    from fastapi import FastAPI, Request, Response
    from pydantic import BaseModel
    from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
    from starlette.staticfiles import StaticFiles
except Exception:  # pragma: no cover - only loaded when settings_app is used
    FastAPI = object  # type: ignore
    FileResponse = object  # type: ignore
    JSONResponse = object  # type: ignore
    StaticFiles = object  # type: ignore
    BaseModel = object  # type: ignore


logger = logging.getLogger(__name__)


def _estimate_pending_playback_seconds(robot: ReachyMini) -> float:
    """Best-effort estimate of audio still queued in the local player."""
    media = getattr(robot, "media", None)
    audio = getattr(media, "audio", None)
    if audio is None:
        return 0.0

    next_pts_ns = getattr(audio, "_playback_next_pts_ns", None)
    get_running_time_ns = getattr(audio, "_get_playback_running_time_ns", None)
    if next_pts_ns is None or not callable(get_running_time_ns):
        return 0.0

    try:
        pending_ns = int(next_pts_ns) - int(get_running_time_ns())
    except Exception:
        return 0.0

    return max(0.0, pending_ns / 1e9)


class LocalStream:
    """LocalStream using Reachy Mini's recorder/player."""

    def __init__(
        self,
        handler: "GeminiLiveHandler",
        robot: ReachyMini,
        *,
        settings_app: Optional[FastAPI] = None,
        instance_path: Optional[str] = None,
    ):
        """Initialize the stream.

        - ``settings_app``: the Reachy Mini Apps FastAPI to attach settings endpoints.
        - ``instance_path``: directory where per-instance ``.env`` should be stored.
        """
        self.handler = handler
        self._robot = robot
        self._stop_event = asyncio.Event()
        self._tasks: List[asyncio.Task[None]] = []
        # Allow the handler to flush the player queue when appropriate.
        self.handler._clear_queue = self.clear_audio_queue
        self._settings_app: Optional[FastAPI] = settings_app
        self._instance_path: Optional[str] = instance_path
        self._settings_initialized = False
        self._asyncio_loop = None
        self._transcript_buffer: list[dict] = []

    # ---- Settings UI ----
    def _read_env_lines(self, env_path: Path) -> list[str]:
        """Load env file contents or a template as a list of lines."""
        inst = env_path.parent
        try:
            if env_path.exists():
                try:
                    return env_path.read_text(encoding="utf-8").splitlines()
                except Exception:
                    return []
            template_text = None
            ex = inst / ".env.example"
            if ex.exists():
                try:
                    template_text = ex.read_text(encoding="utf-8")
                except Exception:
                    template_text = None
            if template_text is None:
                try:
                    cwd_example = Path.cwd() / ".env.example"
                    if cwd_example.exists():
                        template_text = cwd_example.read_text(encoding="utf-8")
                except Exception:
                    template_text = None
            if template_text is None:
                packaged = Path(__file__).parent / ".env.example"
                if packaged.exists():
                    try:
                        template_text = packaged.read_text(encoding="utf-8")
                    except Exception:
                        template_text = None
            return template_text.splitlines() if template_text else []
        except Exception:
            return []

    @staticmethod
    def _has_key(value: Optional[str]) -> bool:
        return bool(value and str(value).strip())

    @staticmethod
    def _is_setup_complete() -> bool:
        """Return True when all required config is present to run the app."""
        return (
            bool(os.environ.get("GEMINI_API_KEY", "").strip())
            and bool(os.environ.get("GCP_PROJECT_ID", "").strip())
            and bool(os.environ.get("VERTEX_REASONING_ENGINE_ID", "").strip())
            and bool(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip())
        )

    def _persist_env_value(self, env_name: str, value: str) -> None:
        """Persist a non-empty environment value in memory and in the instance `.env`."""
        self._persist_env_values({env_name: value})

    def _persist_env_values(self, updates: dict[str, str]) -> None:
        """Persist non-empty environment values in memory and in the instance `.env`."""
        normalized_updates = {name: (value or "").strip() for name, value in updates.items()}
        normalized_updates = {name: value for name, value in normalized_updates.items() if value}
        if not normalized_updates:
            return

        for env_name, value in normalized_updates.items():
            try:
                os.environ[env_name] = value
            except Exception:
                pass
        refresh_runtime_config_from_env()

        try:
            env_path = REACHY_MINI_ENV_PATH
            env_path.parent.mkdir(parents=True, exist_ok=True)
            lines = self._read_env_lines(env_path)
            for env_name, value in normalized_updates.items():
                replaced = False
                for i, ln in enumerate(lines):
                    if ln.strip().startswith(f"{env_name}="):
                        lines[i] = f"{env_name}={value}"
                        replaced = True
                        break
                if not replaced:
                    lines.append(f"{env_name}={value}")
            final_text = "\n".join(lines) + "\n"
            env_path.write_text(final_text, encoding="utf-8")
            logger.info("Persisted %s to %s", ", ".join(sorted(normalized_updates)), env_path)

            try:
                from dotenv import load_dotenv

                load_dotenv(dotenv_path=str(env_path), override=True)
            except Exception:
                pass
            refresh_runtime_config_from_env()
        except Exception as e:
            logger.warning("Failed to persist %s: %s", ", ".join(sorted(normalized_updates)), e)

    def _persist_personality(self, profile: Optional[str]) -> None:
        """Persist the startup personality to the instance .env and config."""
        if LOCKED_PROFILE is not None:
            return
        selection = (profile or "").strip() or None
        try:
            from reachy_mini_conversation_app.config import set_custom_profile

            set_custom_profile(selection)
        except Exception:
            pass

        if not self._instance_path:
            return
        try:
            env_path = Path(self._instance_path) / ".env"
            lines = self._read_env_lines(env_path)
            replaced = False
            for i, ln in enumerate(list(lines)):
                if ln.strip().startswith("REACHY_MINI_CUSTOM_PROFILE="):
                    if selection:
                        lines[i] = f"REACHY_MINI_CUSTOM_PROFILE={selection}"
                    else:
                        lines.pop(i)
                    replaced = True
                    break
            if selection and not replaced:
                lines.append(f"REACHY_MINI_CUSTOM_PROFILE={selection}")
            if selection is None and not env_path.exists():
                return
            final_text = "\n".join(lines) + "\n"
            env_path.write_text(final_text, encoding="utf-8")
            logger.info("Persisted startup personality to %s", env_path)
            try:
                from dotenv import load_dotenv

                load_dotenv(dotenv_path=str(env_path), override=True)
            except Exception:
                pass
        except Exception as e:
            logger.warning("Failed to persist REACHY_MINI_CUSTOM_PROFILE: %s", e)

    def _read_persisted_personality(self) -> Optional[str]:
        """Read persisted startup personality from instance .env (if any)."""
        if not self._instance_path:
            return None
        env_path = Path(self._instance_path) / ".env"
        try:
            if env_path.exists():
                for ln in env_path.read_text(encoding="utf-8").splitlines():
                    if ln.strip().startswith("REACHY_MINI_CUSTOM_PROFILE="):
                        _, _, val = ln.partition("=")
                        v = val.strip()
                        return v or None
        except Exception:
            pass
        return None

    def _init_settings_ui_if_needed(self) -> None:
        """Attach minimal settings UI to the settings app.

        Always mounts the UI when a settings_app is provided so that users
        see a confirmation message even if the API key is already configured.
        """
        if self._settings_initialized:
            return
        if self._settings_app is None:
            return

        static_dir = Path(__file__).parent / "static"
        index_file = static_dir / "index.html"

        if hasattr(self._settings_app, "mount"):
            try:
                # Serve /static/* assets
                self._settings_app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
            except Exception:
                pass

        def _status_payload() -> dict[str, object]:
            return {
                "active_backend": GEMINI_BACKEND,
                "backend_provider": GEMINI_BACKEND,
                "has_key": True,
                "can_proceed": True,
                "requires_restart": False,
                "setup_complete": self._is_setup_complete(),
            }

        # GET /session/status -> sleep/wake state
        @self._settings_app.get("/session/status")
        def _session_status() -> JSONResponse:
            sleeping = getattr(self.handler, "_sleeping", False)
            profile = self.handler.deps.active_profile or config.REACHY_MINI_CUSTOM_PROFILE or "default"
            return JSONResponse({"sleeping": sleeping, "profile": profile})

        # POST /session/sleep
        @self._settings_app.post("/session/sleep")
        def _session_sleep() -> JSONResponse:
            loop = self._asyncio_loop
            if loop and hasattr(self.handler, "sleep"):
                asyncio.run_coroutine_threadsafe(self.handler.sleep(), loop)
            return JSONResponse({"ok": True})

        # POST /session/wake
        @self._settings_app.post("/session/wake")
        async def _session_wake(request: Request) -> JSONResponse:
            try:
                body = await request.json()
                profile = body.get("profile")
            except Exception:
                profile = None
            loop = self._asyncio_loop
            if loop and hasattr(self.handler, "wake"):
                asyncio.run_coroutine_threadsafe(self.handler.wake(profile), loop)
            return JSONResponse({"ok": True})

        # GET /config -> current configuration (no secret values)
        @self._settings_app.get("/config")
        def _get_config() -> JSONResponse:
            return JSONResponse({
                "has_gemini_key": bool(os.environ.get("GEMINI_API_KEY", "").strip()),
                "gcp_project_id": os.environ.get("GCP_PROJECT_ID", ""),
                "gcp_location": os.environ.get("GCP_LOCATION", "us-central1"),
                "vertex_reasoning_engine_id": os.environ.get("VERTEX_REASONING_ENGINE_ID", ""),
                "has_credentials": bool(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()),
                "google_search_grounding": os.environ.get("GOOGLE_SEARCH_GROUNDING", "") == "1",
            })

        # Keys managed elsewhere — excluded from the custom env var editor
        _MANAGED_KEYS = {
            "GEMINI_API_KEY", "GCP_PROJECT_ID", "GCP_LOCATION",
            "VERTEX_REASONING_ENGINE_ID", "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_SEARCH_GROUNDING", "REACHY_MINI_CUSTOM_PROFILE",
            "REACHY_MINI_PROFILES_OVERRIDE", "REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY",
            "REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY", "MODEL_NAME",
        }

        # GET /config/env -> list custom env vars (keys only, values masked)
        @self._settings_app.get("/config/env")
        def _get_env_vars() -> JSONResponse:
            try:
                lines = REACHY_MINI_ENV_PATH.read_text(encoding="utf-8").splitlines() if REACHY_MINI_ENV_PATH.exists() else []
                result = []
                for ln in lines:
                    ln = ln.strip()
                    if not ln or ln.startswith("#") or "=" not in ln:
                        continue
                    key, _, _ = ln.partition("=")
                    key = key.strip()
                    if key and key not in _MANAGED_KEYS:
                        result.append({"key": key, "set": True})
                return JSONResponse(result)
            except Exception:
                return JSONResponse([])

        # POST /config/env -> save/delete custom env vars
        @self._settings_app.post("/config/env")
        async def _save_env_vars(request: Request) -> JSONResponse:
            try:
                updates: list[dict] = await request.json()
            except Exception:
                return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
            try:
                for entry in updates:
                    key = (entry.get("key") or "").strip()
                    value = (entry.get("value") or "").strip()
                    delete = bool(entry.get("delete", False))
                    if not key or key in _MANAGED_KEYS:
                        continue
                    if delete:
                        # Remove from env and .env file
                        os.environ.pop(key, None)
                        if REACHY_MINI_ENV_PATH.exists():
                            lines = REACHY_MINI_ENV_PATH.read_text(encoding="utf-8").splitlines()
                            lines = [ln for ln in lines if not ln.strip().startswith(f"{key}=")]
                            REACHY_MINI_ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    elif value:
                        self._persist_env_values({key: value})
                return JSONResponse({"ok": True})
            except Exception as e:
                return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

        # GET /voices -> available voices with style descriptions
        @self._settings_app.get("/voices")
        def _voices_static() -> JSONResponse:
            return JSONResponse(sorted(AVAILABLE_VOICES))

        # GET /voices/styles -> voice name → character style mapping
        @self._settings_app.get("/voices/styles")
        def _voices_styles() -> JSONResponse:
            return JSONResponse(VOICE_STYLES)

        # GET /voices/preview?voice=Sadachbia -> short audio sample as WAV
        @self._settings_app.get("/voices/preview")
        async def _voice_preview(voice: str = "Sadachbia") -> "Response":
            import wave, io
            project = os.environ.get("GCP_PROJECT_ID", "").strip()
            location = os.environ.get("GCP_LOCATION", "us-central1").strip() or "us-central1"
            if not project:
                return JSONResponse({"error": "not_configured"}, status_code=503)
            try:
                from google import genai as _genai
                from google.genai import types as _types
                client = _genai.Client(vertexai=True, project=project, location=location)
                resp = await asyncio.get_event_loop().run_in_executor(None, lambda: client.models.generate_content(
                    model="gemini-3.1-flash-tts-preview",
                    contents=f"Say naturally: Hello! I'm {voice}, your Reachy Mini assistant.",
                    config=_types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=_types.SpeechConfig(
                            voice_config=_types.VoiceConfig(
                                prebuilt_voice_config=_types.PrebuiltVoiceConfig(voice_name=voice)
                            )
                        ),
                    ),
                ))
                pcm = resp.candidates[0].content.parts[0].inline_data.data
                buf = io.BytesIO()
                with wave.open(buf, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(24000)
                    wf.writeframes(pcm)
                return Response(content=buf.getvalue(), media_type="audio/wav")
            except Exception as e:
                logger.warning("Voice preview failed for %r: %s", voice, e)
                return JSONResponse({"error": str(e)}, status_code=500)

        # GET / -> index.html
        @self._settings_app.get("/")
        def _root() -> FileResponse:
            return FileResponse(str(index_file))

        # GET /favicon.ico -> optional, avoid noisy 404s on some browsers
        @self._settings_app.get("/favicon.ico")
        def _favicon() -> Response:
            return Response(status_code=204)

        # GET /status -> whether key is set
        @self._settings_app.get("/status")
        def _status() -> JSONResponse:
            return JSONResponse(_status_payload())

        # GET /ready -> whether backend finished loading tools
        @self._settings_app.get("/ready")
        def _ready() -> JSONResponse:
            try:
                mod = sys.modules.get("reachy_mini_conversation_app.tools.core_tools")
                ready = bool(getattr(mod, "_TOOLS_INITIALIZED", False)) if mod else False
            except Exception:
                ready = False
            return JSONResponse({"ready": ready})

        class SetupPayload(BaseModel):
            gemini_api_key: str
            gcp_project_id: str
            gcp_location: str = "us-central1"
            vertex_reasoning_engine_id: str
            service_account_json: str
            voice: str = "Sadachbia"

        @self._settings_app.post("/setup")
        def _setup(payload: SetupPayload) -> JSONResponse:
            try:
                REACHY_MINI_DIR.mkdir(parents=True, exist_ok=True)
                sa_path = REACHY_MINI_DIR / "service_account.json"

                # Only write SA key if real JSON was provided (not empty/placeholder)
                sa_json = (payload.service_account_json or "").strip()
                if sa_json and sa_json != "{}":
                    try:
                        json.loads(sa_json)  # validate
                        sa_path.write_text(sa_json, encoding="utf-8")
                        sa_path.chmod(0o600)
                    except json.JSONDecodeError as je:
                        return JSONResponse({"ok": False, "error": f"Invalid JSON: {je}"}, status_code=400)

                # Persist all env vars to ~/.reachy_mini/.env
                updates: dict[str, str] = {
                    "GCP_PROJECT_ID": payload.gcp_project_id.strip(),
                    "GCP_LOCATION": payload.gcp_location.strip() or "us-central1",
                    "VERTEX_REASONING_ENGINE_ID": payload.vertex_reasoning_engine_id.strip(),
                    "GOOGLE_APPLICATION_CREDENTIALS": str(sa_path),
                }
                if payload.gemini_api_key.strip():
                    updates["GEMINI_API_KEY"] = payload.gemini_api_key.strip()
                self._persist_env_values(updates)

                # Set voice in default profile
                try:
                    from reachy_mini_conversation_app.config import PROFILES_DIRECTORY
                    voice_file = Path(PROFILES_DIRECTORY) / "default" / "voice.txt"
                    if voice_file.parent.exists() and payload.voice.strip():
                        voice_file.write_text(payload.voice.strip() + "\n", encoding="utf-8")
                except Exception as ve:
                    logger.warning("Could not write voice.txt: %s", ve)

                logger.info("Setup complete — config written to %s", REACHY_MINI_ENV_PATH)
                return JSONResponse({"ok": True, **_status_payload()})
            except Exception as e:
                logger.warning("Setup failed: %s", e)
                return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

        @self._settings_app.get("/stream/camera")
        def _stream_camera() -> "StreamingResponse":
            def camera_frames():
                try:
                    while True:
                        try:
                            deps = getattr(self.handler, "deps", None)
                            cw = getattr(deps, "camera_worker", None) if deps else None
                            frame = cw.get_latest_frame() if cw is not None else None
                            if frame is not None:
                                from reachy_mini_conversation_app.camera_frame_encoding import encode_bgr_frame_as_jpeg
                                jpeg = encode_bgr_frame_as_jpeg(frame)
                                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
                        except GeneratorExit:
                            return
                        except Exception:
                            pass
                        time.sleep(1.0)
                except GeneratorExit:
                    return
            return StreamingResponse(camera_frames(), media_type="multipart/x-mixed-replace; boundary=frame")

        @self._settings_app.get("/stream/transcript")
        def _stream_transcript() -> "StreamingResponse":
            def transcript_events():
                try:
                    pointer = 0
                    snapshot = list(self._transcript_buffer)
                    for entry in snapshot:
                        yield f"data: {json.dumps(entry)}\n\n"
                        pointer += 1
                    while True:
                        buf = self._transcript_buffer
                        while pointer < len(buf):
                            yield f"data: {json.dumps(buf[pointer])}\n\n"
                            pointer += 1
                        time.sleep(0.3)
                except GeneratorExit:
                    return
            return StreamingResponse(
                transcript_events(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        self._settings_initialized = True

    def launch(self) -> None:
        """Start the recorder/player and run the async processing loops.

        If the selected backend is missing its required key, expose a tiny
        settings UI via the Reachy Mini settings server to collect it before
        starting streams.
        """
        self._stop_event.clear()

        # Try to load an existing instance .env first (covers subsequent runs)
        if self._instance_path:
            try:
                from dotenv import load_dotenv

                env_path = Path(self._instance_path) / ".env"
                if env_path.exists():
                    load_dotenv(dotenv_path=str(env_path), override=True)
                    refresh_runtime_config_from_env()
            except Exception:
                pass  # Instance .env loading is optional; continue with defaults

        # Always expose settings UI if a settings app is available
        # (do this AFTER loading/downloading the key so status endpoint sees the right value)
        self._init_settings_ui_if_needed()

        # If setup is incomplete -> wait until provided via the settings UI
        if not self._is_setup_complete():
            logger.warning("Setup required. Open the app settings page at the robot's IP to configure.")
            try:
                while not self._is_setup_complete():
                    time.sleep(0.2)
            except KeyboardInterrupt:
                logger.info("Interrupted while waiting for setup.")
                return

        # Start media after key is set/available
        self._robot.media.start_recording()
        self._robot.media.start_playing()
        time.sleep(1)  # give some time to the pipelines to start

        async def runner() -> None:
            # Capture loop for cross-thread personality actions
            loop = asyncio.get_running_loop()
            self._asyncio_loop = loop  # type: ignore[assignment]
            # Mount personality routes now that loop and handler are available
            try:
                if self._settings_app is not None:
                    mount_personality_routes(
                        self._settings_app,
                        self.handler,
                        lambda: self._asyncio_loop,
                        persist_personality=self._persist_personality,
                        get_persisted_personality=self._read_persisted_personality,
                    )
            except Exception:
                pass
            self._tasks = [
                asyncio.create_task(self.handler.start_up(), name="gemini-handler"),
                asyncio.create_task(self.record_loop(), name="stream-record-loop"),
                asyncio.create_task(self.play_loop(), name="stream-play-loop"),
            ]
            try:
                await asyncio.gather(*self._tasks)
            except asyncio.CancelledError:
                logger.info("Tasks cancelled during shutdown")
            finally:
                # Ensure handler connection is closed
                await self.handler.shutdown()

        asyncio.run(runner())

    def close(self) -> None:
        """Stop the stream and underlying media pipelines.

        This method:
        - Stops audio recording and playback first
        - Sets the stop event to signal async loops to terminate
        - Cancels all pending async tasks (openai-handler, record-loop, play-loop)
        """
        logger.info("Stopping LocalStream...")

        # Stop media pipelines FIRST before cancelling async tasks
        # This ensures clean shutdown before PortAudio cleanup
        try:
            self._robot.media.stop_recording()
        except Exception as e:
            logger.debug(f"Error stopping recording (may already be stopped): {e}")

        try:
            self._robot.media.stop_playing()
        except Exception as e:
            logger.debug(f"Error stopping playback (may already be stopped): {e}")

        # Now signal async loops to stop
        self._stop_event.set()

        # Cancel all running tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()

    def clear_audio_queue(self) -> None:
        """Flush the player's appsrc to drop any queued audio immediately."""
        logger.info("User intervention: flushing player queue")
        backend = getattr(self._robot.media, "backend", None)
        audio = getattr(self._robot.media, "audio", None)
        if audio is not None:
            if backend == MediaBackend.LOCAL and hasattr(audio, "clear_player") and callable(audio.clear_player):
                audio.clear_player()
            elif (
                backend == MediaBackend.WEBRTC
                and hasattr(audio, "clear_output_buffer")
                and callable(audio.clear_output_buffer)
            ):
                audio.clear_output_buffer()
            elif hasattr(audio, "clear_output_buffer") and callable(audio.clear_output_buffer):
                audio.clear_output_buffer()
            elif hasattr(audio, "clear_player") and callable(audio.clear_player):
                audio.clear_player()
        self.handler.output_queue = asyncio.Queue()

    async def record_loop(self) -> None:
        """Read mic frames from the recorder and forward them to the handler."""
        input_sample_rate = self._robot.media.get_input_audio_samplerate()
        logger.debug(f"Audio recording started at {input_sample_rate} Hz")

        while not self._stop_event.is_set():
            audio_frame = self._robot.media.get_audio_sample()
            if audio_frame is not None:
                await self.handler.receive((input_sample_rate, audio_frame))
            await asyncio.sleep(0)  # avoid busy loop

    async def play_loop(self) -> None:
        """Fetch outputs from the handler: log text and play audio frames."""
        while not self._stop_event.is_set():
            handler_output = await self.handler.emit()

            if isinstance(handler_output, AdditionalOutputs):
                for msg in handler_output.args:
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        logger.info(
                            "role=%s content=%s",
                            msg.get("role"),
                            content if len(content) < 500 else content[:500] + "…",
                        )
                        role = msg.get("role")
                        if role in ("user", "assistant", "tool", "search", "memory") and content.strip():
                            self._transcript_buffer.append({"role": role, "content": content})

            elif isinstance(handler_output, tuple):
                input_sample_rate, audio_data = handler_output
                output_sample_rate = self._robot.media.get_output_audio_samplerate()

                # Skip empty audio frames
                if audio_data.size == 0:
                    continue

                # Reshape if needed
                if audio_data.ndim == 2:
                    # Scipy channels last convention
                    if audio_data.shape[1] > audio_data.shape[0]:
                        audio_data = audio_data.T
                    # Multiple channels -> Mono channel
                    if audio_data.shape[1] > 1:
                        audio_data = audio_data[:, 0]

                # Cast if needed
                audio_frame = audio_to_float32(audio_data)

                # Resample if needed
                if input_sample_rate != output_sample_rate:
                    num_samples = int(len(audio_frame) * output_sample_rate / input_sample_rate)
                    if num_samples == 0:
                        continue
                    audio_frame = resample(
                        audio_frame,
                        num_samples,
                    )

                head_wobbler = self.handler.deps.head_wobbler
                if head_wobbler is not None:
                    playback_delay_s = _estimate_pending_playback_seconds(self._robot)
                    head_wobbler.feed_pcm(audio_data.reshape(1, -1), input_sample_rate, start_delay_s=playback_delay_s)

                self._robot.media.push_audio_sample(audio_frame)

            else:
                logger.debug("Ignoring output type=%s", type(handler_output).__name__)

            await asyncio.sleep(0)  # yield to event loop
