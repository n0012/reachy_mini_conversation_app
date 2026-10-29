"""Tests for motor/cert health checks and honest memory save results."""

import json
import datetime as dt
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reachy_mini_gemini_live import memory as memory_mod
from reachy_mini_gemini_live.config import _resolve_auto_sleep_minutes
from reachy_mini_gemini_live.memory import FileMemoryBackend, VertexMemoryBackend
from reachy_mini_gemini_live.robot_health import (
    motor_mode,
    ensure_awake,
    motors_off_error,
    wif_cert_days_left,
)


def _robot(mode: str | None) -> MagicMock:
    robot = MagicMock()
    backend = SimpleNamespace(motor_control_mode=SimpleNamespace(value=mode)) if mode else None
    robot.client.get_status.return_value = SimpleNamespace(backend_status=backend)
    return robot


def test_motor_mode_reads_daemon_status() -> None:
    assert motor_mode(_robot("disabled")) == "disabled"
    assert motor_mode(_robot(None)) is None


def test_motor_mode_unknown_when_status_fails() -> None:
    robot = MagicMock()
    robot.client.get_status.side_effect = TimeoutError
    assert motor_mode(robot) is None


def test_motors_off_error_only_when_disabled() -> None:
    assert motors_off_error(_robot("disabled")) is not None
    assert motors_off_error(_robot("enabled")) is None
    assert motors_off_error(None) is None


def test_ensure_awake_wakes_limp_robot() -> None:
    robot = _robot("disabled")
    ensure_awake(robot)
    robot.enable_motors.assert_called_once()
    robot.wake_up.assert_called_once()


def test_ensure_awake_leaves_enabled_robot_alone() -> None:
    robot = _robot("enabled")
    ensure_awake(robot)
    robot.enable_motors.assert_not_called()
    robot.wake_up.assert_not_called()


def test_auto_sleep_zero_means_never() -> None:
    assert _resolve_auto_sleep_minutes("0") == 0
    assert _resolve_auto_sleep_minutes("90") == 60


def test_wif_cert_days_left(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=10, hours=1))
        .sign(key, hashes.SHA256())
    )
    pem = tmp_path / "cert.pem"
    pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    cfg = tmp_path / "certificate_config.json"
    cfg.write_text(json.dumps({"cert_configs": {"workload": {"cert_path": str(pem), "key_path": "x"}}}))
    creds = tmp_path / "credentials.json"
    creds.write_text(
        json.dumps(
            {
                "type": "external_account",
                "credential_source": {"certificate": {"certificate_config_location": str(cfg)}},
            }
        )
    )
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(creds))
    assert wif_cert_days_left() == 10


def test_wif_cert_days_left_none_for_service_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sa = tmp_path / "sa.json"
    sa.write_text(json.dumps({"type": "service_account"}))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(sa))
    assert wif_cert_days_left() is None


@pytest.mark.asyncio
async def test_file_backend_reports_save_outcome(tmp_path: Path) -> None:
    mem = FileMemoryBackend(base_dir=tmp_path)
    assert await mem.save_session("default", []) == "skipped"
    assert await mem.save_session("default", [{"role": "user", "content": "hello there"}]) == "saved"
    assert await mem.remember("default", "Likes tea.") is True


@pytest.fixture()
def vertex(monkeypatch: pytest.MonkeyPatch) -> VertexMemoryBackend:
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("VERTEX_REASONING_ENGINE_ID", "1")
    return VertexMemoryBackend()


@pytest.mark.asyncio
async def test_vertex_failure_is_reported_not_swallowed(
    vertex: VertexMemoryBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> dict:
        raise RuntimeError("invalid_grant")

    monkeypatch.setattr(memory_mod, "_api", boom)
    assert await vertex.remember("default", "Likes tea.", user_id="nick") is False
    assert vertex.last_error == "invalid_grant"
    assert await vertex.load_context("default", user_id="nick") == ""
    assert vertex.last_error == "invalid_grant"


@pytest.mark.asyncio
async def test_vertex_success_clears_error(vertex: VertexMemoryBackend, monkeypatch: pytest.MonkeyPatch) -> None:
    vertex.last_error = "old"
    monkeypatch.setattr(memory_mod, "_api", lambda *_a, **_k: {"name": "ops/1"})
    assert await vertex.remember("default", "Likes tea.", user_id="nick") is True
    assert vertex.last_error is None


@pytest.mark.asyncio
async def test_sleep_saves_transcript_and_wake_reconnects_in_place(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from reachy_mini_gemini_live import gemini_live
    from reachy_mini_gemini_live.gemini_live import GeminiLiveHandler
    from reachy_mini_gemini_live.tools.core_tools import ToolDependencies

    robot = MagicMock()
    movement = MagicMock()
    handler = GeminiLiveHandler(ToolDependencies(reachy_mini=robot, movement_manager=movement))
    handler._memory = MagicMock()
    handler._memory.save_session = AsyncMock(return_value="saved")
    handler._memory_enabled = True
    handler._session_transcript = [{"role": "user", "content": "we're going to Japan in March"}]
    handler._false_wake_timeout_s = 0
    monkeypatch.setattr(handler, "_start_ei_detection", MagicMock())
    monkeypatch.setattr(handler, "_stop_ei_detection", MagicMock())
    monkeypatch.setattr(handler, "_ei_watchdog", AsyncMock())
    real_sleep = asyncio.sleep
    monkeypatch.setattr(gemini_live.asyncio, "sleep", lambda *_a, **_k: real_sleep(0))
    restart = MagicMock()
    monkeypatch.setattr("urllib.request.urlopen", restart)

    await handler.sleep()
    await real_sleep(0.01)
    handler._memory.save_session.assert_awaited_once()
    assert handler._session_transcript == []
    movement.pause_output.assert_called_once()

    await handler.wake()
    robot.wake_up.assert_called_once()
    movement.resume_output.assert_called_once()
    assert handler._wake_event.is_set()
    restart.assert_not_called()  # no app restart unless the profile changes


@pytest.mark.asyncio
@pytest.mark.parametrize("spoke, expect_sleep", [(False, True), (True, False)])
async def test_false_wake_guard(spoke: bool, expect_sleep: bool) -> None:
    from unittest.mock import AsyncMock

    from reachy_mini_gemini_live.gemini_live import GeminiLiveHandler
    from reachy_mini_gemini_live.tools.core_tools import ToolDependencies

    handler = GeminiLiveHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler._sleeping = False
    handler._false_wake_timeout_s = 0.01
    handler._user_spoke_since_wake = spoke
    handler.sleep = AsyncMock()  # type: ignore[method-assign]
    await handler._false_wake_guard()
    assert handler.sleep.await_count == (1 if expect_sleep else 0)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("---", True),
        ("-", True),
        ("・", True),
        ("\b", True),
        ("\U000e002a", True),
        ("<!-- No response required -->", True),
        ("(No response; wait for the user to speak.)", True),
        ("Done.", False),
        ("Nice — how did it go?", False),
        ("Good evening, Nick. Did you ever get me dancing to Daft Punk?", False),
    ],
)
def test_placeholder_non_answers_are_detected(text: str, expected: bool) -> None:
    from reachy_mini_gemini_live.gemini_live import _is_placeholder

    assert _is_placeholder(text) is expected


def _tool_call_response(*calls: tuple[str, str, dict]) -> SimpleNamespace:
    fcs = [SimpleNamespace(name=name, id=call_id, args=args) for name, call_id, args in calls]
    return SimpleNamespace(tool_call=SimpleNamespace(function_calls=fcs))


@pytest.mark.asyncio
async def test_duplicate_tool_calls_run_once_and_get_answered() -> None:
    from unittest.mock import AsyncMock

    from reachy_mini_gemini_live import gemini_live

    # Use the handler module's own class: other tests reload the tool modules.
    handler = gemini_live.GeminiLiveHandler(
        gemini_live.ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    )
    handler.session = MagicMock()
    handler.session.send_tool_response = AsyncMock()
    handler.tool_manager = MagicMock()
    handler.tool_manager.start_tool = AsyncMock(return_value=SimpleNamespace(tool_id="t"))

    # Two camera calls with differently-worded questions: one snapshot.
    await handler._handle_tool_call(
        _tool_call_response(
            ("camera", "c1", {"question": "Let's take a quick look"}),
            ("camera", "c2", {"question": "I'm taking a quick look"}),
        )
    )
    assert handler.tool_manager.start_tool.await_count == 1
    (dup,) = handler.session.send_tool_response.await_args.kwargs["function_responses"]
    assert dup.id == "c2" and dup.response["scheduling"] == "SILENT"

    # Different searches both run; an identical repeat does not.
    await handler._handle_tool_call(
        _tool_call_response(
            ("web_search", "s1", {"query": "ryokan in Hakone"}),
            ("web_search", "s2", {"query": "ryokan in Kyoto"}),
            ("web_search", "s3", {"query": "ryokan in Hakone"}),
        )
    )
    assert handler.tool_manager.start_tool.await_count == 3


@pytest.mark.asyncio
async def test_restart_mid_conversation_keeps_history_and_skips_greeting() -> None:
    from reachy_mini_gemini_live.gemini_live import GeminiLiveHandler
    from reachy_mini_gemini_live.tools.core_tools import ToolDependencies

    handler = GeminiLiveHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler._memory_enabled = True
    handler._session_transcript = [
        {"role": "user", "content": "the Japan trip is February 2028"},
        {"role": "assistant", "content": "Got it, February 2028."},
    ]
    notes = await handler._session_notes("nick", None, continuing=True)
    assert "Do not greet" in notes
    assert "February 2028" in notes
    assert "don't assume" not in notes  # no identity re-check mid-conversation

    fresh = await handler._session_notes("nick", None, continuing=False)
    assert "Do not greet" not in fresh
