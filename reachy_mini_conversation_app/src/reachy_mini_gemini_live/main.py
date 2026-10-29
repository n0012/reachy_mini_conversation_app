"""Entrypoint for the Reachy Mini conversation app."""

import os
import sys
import time
import asyncio
import argparse
import threading
from typing import Any, Dict, List, Optional
from pathlib import Path

from fastapi import FastAPI

from reachy_mini import ReachyMini, ReachyMiniApp
from reachy_mini_gemini_live.utils import (
    CameraVisionInitializationError,
    parse_args,
    setup_logger,
    initialize_camera_and_vision,
    log_connection_troubleshooting,
)


def update_chatbot(chatbot: List[Dict[str, Any]], response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Update the chatbot with AdditionalOutputs."""
    chatbot.append(response)
    return chatbot


def _ensure_launcher_metadata() -> None:
    """Write full cardData into the launcher's .app_metadata file so the icon shows.

    The Pollen launcher reads /venvs/.app_metadata/reachy_mini_gemini_live.json
    for display metadata (icon emoji, title, description). On a fresh install the
    launcher only writes a minimal stub {"id": "..."}. We self-heal it on every
    startup so reinstalls don't lose the icon.
    """
    try:
        import json as _json

        meta_path = Path("/venvs/.app_metadata/reachy_mini_gemini_live.json")
        if not meta_path.parent.exists():
            return
        current = _json.loads(meta_path.read_text()) if meta_path.exists() else {}
        if current.get("cardData", {}).get("emoji"):
            return  # already populated
        current.update(
            {
                "id": "n0012/reachy_mini_conversation_app_gemini",
                "author": "n0012",
                "isPythonApp": True,
                "cardData": {
                    "emoji": "✨",
                    "title": "Reachy Mini Gemini Live Edition",
                    "short_description": "Gemini Live robot with memory, MCP tools, home control",
                    "sdk": "static",
                    "colorFrom": "blue",
                    "colorTo": "purple",
                },
            }
        )
        meta_path.write_text(_json.dumps(current, indent=2, ensure_ascii=False))
    except Exception:
        pass  # never crash startup over a metadata file


def main() -> None:
    """Entrypoint for the Reachy Mini conversation app."""
    _ensure_launcher_metadata()
    args, _ = parse_args()
    run(args)


def run(
    args: argparse.Namespace,
    robot: ReachyMini = None,
    app_stop_event: Optional[threading.Event] = None,
    settings_app: Optional[FastAPI] = None,
    instance_path: Optional[str] = None,
) -> None:
    """Run the Reachy Mini conversation app."""
    # Putting these dependencies here makes the dashboard faster to load when the conversation app is installed
    from reachy_mini_gemini_live.moves import MovementManager
    from reachy_mini_gemini_live.config import config, refresh_runtime_config_from_env

    logger = setup_logger(args.debug)
    logger.info("Starting Reachy Mini Conversation App")

    if instance_path is not None:
        try:
            from dotenv import load_dotenv

            env_path = Path(instance_path) / ".env"
            if env_path.exists():
                load_dotenv(dotenv_path=str(env_path), override=True)
                refresh_runtime_config_from_env()
                logger.info("Loaded instance configuration from %s", env_path)
        except Exception as e:
            logger.warning("Failed to load instance configuration: %s", e)

    from reachy_mini_gemini_live.console import LocalStream
    from reachy_mini_gemini_live.tools.core_tools import ToolDependencies
    from reachy_mini_gemini_live.audio.head_wobbler import HeadWobbler

    if args.no_camera and args.head_tracker is not None:
        logger.warning("Head tracking disabled: --no-camera flag is set. Remove --no-camera to enable head tracking.")

    if robot is None:
        try:
            robot_kwargs = {}
            if args.robot_name is not None:
                robot_kwargs["robot_name"] = args.robot_name

            logger.info("Initializing ReachyMini (SDK will auto-detect appropriate backend)")
            robot = ReachyMini(**robot_kwargs)

        except TimeoutError as e:
            logger.error(f"Connection timeout: Failed to connect to Reachy Mini daemon. Details: {e}")
            log_connection_troubleshooting(logger, args.robot_name)
            sys.exit(1)

        except ConnectionError as e:
            logger.error(f"Connection failed: Unable to establish connection to Reachy Mini. Details: {e}")
            log_connection_troubleshooting(logger, args.robot_name)
            sys.exit(1)

        except Exception as e:
            logger.error(f"Unexpected error during robot initialization: {type(e).__name__}: {e}")
            logger.error("Please check your configuration and try again.")
            sys.exit(1)

    from reachy_mini_gemini_live.deep_sleep import DeepSleep, recover_released_media

    recover_released_media(robot)

    # Auto-enable Gradio in simulation mode (both MuJoCo for daemon and mockup-sim for desktop app)
    status = robot.client.get_status()
    if isinstance(status, dict):
        simulation_enabled = status.get("simulation_enabled", False)
        mockup_sim_enabled = status.get("mockup_sim_enabled", False)
    else:
        simulation_enabled = getattr(status, "simulation_enabled", False)
        mockup_sim_enabled = getattr(status, "mockup_sim_enabled", False)

    is_simulation = simulation_enabled or mockup_sim_enabled

    if is_simulation and not args.gradio:
        logger.info("Simulation mode detected. Automatically enabling gradio flag.")
        args.gradio = True

    if args.gradio:
        try:
            import gradio as gr
            from fastrtc import Stream
        except ImportError:
            logger.warning("gradio/fastrtc not installed (incompatible with reachy-mini>=1.12) — running headless")
            args.gradio = False

    try:
        camera_worker, vision_processor = initialize_camera_and_vision(args, robot)
    except CameraVisionInitializationError as e:
        logger.error("Failed to initialize camera/vision: %s", e)
        sys.exit(1)

    deep_sleep = DeepSleep(robot, camera_worker)

    movement_manager = MovementManager(
        current_robot=robot,
        camera_worker=camera_worker,
    )

    head_wobbler = HeadWobbler(set_speech_offsets=movement_manager.set_speech_offsets)

    deps = ToolDependencies(
        reachy_mini=robot,
        movement_manager=movement_manager,
        camera_worker=camera_worker,
        vision_processor=vision_processor,
        head_wobbler=head_wobbler,
        deep_sleep=deep_sleep,
    )
    current_file_path = os.path.dirname(os.path.abspath(__file__))
    logger.debug(f"Current file absolute path: {current_file_path}")
    from reachy_mini_gemini_live.gemini_live import GeminiLiveHandler

    logger.info("Using Gemini Live handler for model: %s", config.MODEL_NAME)
    handler = GeminiLiveHandler(deps, gradio_mode=args.gradio, instance_path=instance_path)

    stream_manager: Any = None

    if args.gradio:
        chatbot = gr.Chatbot(
            type="messages",
            resizable=True,
            avatar_images=(
                os.path.join(current_file_path, "images", "user_avatar.png"),
                os.path.join(current_file_path, "images", "reachymini_avatar.png"),
            ),
        )
        api_key_textbox = gr.Textbox(
            label="GEMINI_API_KEY",
            type="password",
            value=os.getenv("GEMINI_API_KEY") if not os.getenv("SPACE_ID") else "",
        )

        from reachy_mini_gemini_live.gradio_personality import PersonalityUI

        personality_ui = PersonalityUI()
        personality_ui.create_components()

        stream = Stream(
            handler=handler,
            mode="send-receive",
            modality="audio",
            additional_inputs=[
                chatbot,
                api_key_textbox,
                *personality_ui.additional_inputs_ordered(),
            ],
            additional_outputs=[chatbot],
            additional_outputs_handler=update_chatbot,
            ui_args={"title": "Talk with Reachy Mini"},
        )
        stream_manager = stream.ui
        if not settings_app:
            app = FastAPI()
        else:
            app = settings_app

        personality_ui.wire_events(handler, stream_manager)

        app = gr.mount_gradio_app(app, stream.ui, path="/")
    else:
        # In headless mode, wire settings_app + instance_path to console LocalStream
        stream_manager = LocalStream(
            handler,
            robot,
            settings_app=settings_app,
            instance_path=instance_path,
        )

    # The daemon leaves an asleep robot limp when an app starts; starting the app
    # is the human's request to talk, so wake him before the movement loop runs.
    from reachy_mini_gemini_live.robot_health import ensure_awake

    ensure_awake(robot)

    # Each async service → its own thread/loop
    movement_manager.start()
    head_wobbler.start()
    if camera_worker:
        camera_worker.start()

    def poll_stop_event() -> None:
        """Poll the stop event to allow graceful shutdown."""
        if app_stop_event is not None:
            app_stop_event.wait()

        logger.info("App stop event detected, shutting down...")
        try:
            stream_manager.close()
        except Exception as e:
            logger.error(f"Error while closing stream manager: {e}")

    if app_stop_event:
        threading.Thread(target=poll_stop_event, daemon=True).start()

    try:
        stream_manager.launch()
    except KeyboardInterrupt:
        logger.info("Keyboard interruption in main thread... closing server.")
    finally:
        movement_manager.stop()
        head_wobbler.stop()
        if camera_worker:
            camera_worker.stop()

        deep_sleep.restore()
        # Ensure media is explicitly closed before disconnecting
        try:
            robot.media.close()
        except Exception as e:
            logger.debug(f"Error closing media during shutdown: {e}")

        # prevent connection to keep alive some threads
        robot.client.disconnect()
        time.sleep(1)
        logger.info("Shutdown complete.")


class ReachyMiniConversationApp(ReachyMiniApp):  # type: ignore[misc]
    """Reachy Mini Apps entry point for the conversation app."""

    custom_app_url = "http://0.0.0.0:7860/"
    dont_start_webserver = False

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        """Run the Reachy Mini conversation app."""
        asyncio.set_event_loop(asyncio.new_event_loop())

        args, _ = parse_args()

        instance_path = self._get_instance_path().parent
        run(
            args,
            robot=reachy_mini,
            app_stop_event=stop_event,
            settings_app=self.settings_app,
            instance_path=instance_path,
        )


if __name__ == "__main__":
    app = ReachyMiniConversationApp()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()
