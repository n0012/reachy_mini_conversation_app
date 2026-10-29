"""Small health checks: motor state, and expiry of the Workload Identity client cert."""

import os
import json
import logging
from pathlib import Path
from datetime import datetime, timezone


logger = logging.getLogger(__name__)

CERT_WARN_DAYS = 30


def motor_mode(robot: object) -> str | None:
    """Return the daemon's motor control mode ("enabled", "disabled", ...), or None if unknown."""
    try:
        status = robot.client.get_status(wait=True, timeout=2.0)  # type: ignore[attr-defined]
        backend = getattr(status, "backend_status", None)
        mode = getattr(backend, "motor_control_mode", None)
        return getattr(mode, "value", None) or (str(mode) if mode else None)
    except Exception as e:
        logger.debug("motor_mode unavailable: %s", e)
        return None


def motors_off_error(robot: object) -> dict | None:
    """Tool-result error when the motors can't move, so the model doesn't claim it moved."""
    if robot is None:
        return None
    mode = motor_mode(robot)
    if mode == "disabled":
        return {"error": "My motors are switched off, so I can't move right now. Tell the user."}
    return None


def ensure_awake(robot: object) -> None:
    """Turn the motors on and play the wake-up move if the daemon left the robot limp.

    The daemon deliberately leaves an asleep robot limp when an app starts, which
    otherwise leaves the head slumped while the conversation runs.
    """
    mode = motor_mode(robot)
    if mode in (None, "enabled"):
        return
    logger.warning("Motors are %r at startup — enabling and waking up", mode)
    try:
        robot.enable_motors()  # type: ignore[attr-defined]
        robot.wake_up()  # type: ignore[attr-defined]
    except Exception as e:
        logger.error("Could not wake the robot: %s", e)


def wif_cert_days_left() -> int | None:
    """Days until the X.509 Workload Identity client cert expires, or None if not using one."""
    cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not cred_path:
        return None
    try:
        creds = json.loads(Path(cred_path).read_text(encoding="utf-8"))
        cert_src = (creds.get("credential_source") or {}).get("certificate")
        if creds.get("type") != "external_account" or not cert_src:
            return None
        cfg_path = cert_src.get("certificate_config_location") or str(
            Path.home() / ".config" / "gcloud" / "certificate_config.json"
        )
        cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
        pem = Path(cfg["cert_configs"]["workload"]["cert_path"]).read_bytes()

        from cryptography import x509

        not_after = x509.load_pem_x509_certificate(pem).not_valid_after_utc
        return (not_after - datetime.now(timezone.utc)).days
    except Exception as e:
        logger.debug("WIF cert expiry check skipped: %s", e)
        return None
