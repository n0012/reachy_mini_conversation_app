"""Headless personality management (console-based).

Provides an interactive CLI to browse, preview, apply, create and edit
"personalities" (profiles) when running without Gradio.

This module is intentionally not shared with the Gradio implementation to
avoid coupling and keep responsibilities clear for headless mode.
"""

from __future__ import annotations
import os
from typing import List
from pathlib import Path

from .config import REACHY_MINI_DIR, DEFAULT_PROFILES_DIRECTORY


DEFAULT_OPTION = "(built-in default)"

# User profiles default to ~/.reachy_mini_gemini_live/profiles/ — outside the app
# package so they survive updates. Override with REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY.
_env_user_dir = os.getenv("REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY")
USER_PROFILES_DIR: Path = Path(_env_user_dir) if _env_user_dir else REACHY_MINI_DIR / "profiles"


def _user_profiles_root() -> Path:
    """Return the persistent user profiles dir, which survives app updates."""
    USER_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    return USER_PROFILES_DIR


def _profiles_root() -> Path:
    """Where to write new/edited profiles — always the persistent user dir."""
    return _user_profiles_root()


def _prompts_dir() -> Path:
    return Path(__file__).parent / "prompts"


def _tools_dir() -> Path:
    return Path(__file__).parent / "tools"


def _sanitize_name(name: str) -> str:
    import re

    s = name.strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-zA-Z0-9_-]", "", s)
    return s


def list_personalities() -> List[str]:
    """List profiles from both the built-in dir and the user dir (deduped, user takes priority)."""
    seen: dict[str, Path] = {}
    user_root = _user_profiles_root()
    # Built-in profiles first (lower priority)
    for root in [DEFAULT_PROFILES_DIRECTORY]:
        try:
            if root and root.exists():
                for p in sorted(root.iterdir()):
                    if p.is_dir() and (p / "instructions.txt").exists():
                        seen[p.name] = p
        except Exception:
            pass
    # User profiles second — override built-in if same name
    if user_root:
        try:
            if user_root.exists():
                for p in sorted(user_root.iterdir()):
                    if p.is_dir() and (p / "instructions.txt").exists():
                        seen[p.name] = p
        except Exception:
            pass
    return sorted(seen.keys())


def resolve_profile_dir(selection: str) -> Path:
    """Resolve profile dir — user dir takes priority over built-in."""
    user_root = _user_profiles_root()
    if user_root:
        candidate = user_root / selection
        if candidate.is_dir():
            return candidate
    return DEFAULT_PROFILES_DIRECTORY / selection


def read_instructions_for(name: str) -> str:
    """Read the instructions.txt content for the given profile name."""
    try:
        if name == DEFAULT_OPTION:
            df = _prompts_dir() / "default_prompt.txt"
            return df.read_text(encoding="utf-8").strip() if df.exists() else ""
        target = resolve_profile_dir(name) / "instructions.txt"
        return target.read_text(encoding="utf-8").strip() if target.exists() else ""
    except Exception as e:
        return f"Could not load instructions: {e}"


def available_tools_for(selected: str) -> List[str]:
    """List per-profile toggleable tool modules.

    Excludes system tools (always loaded regardless of tools.txt — recall,
    forget, go_to_sleep, task_status, task_cancel) so the UI doesn't show
    toggles that have no effect. `remember` and `identify_user` ARE
    toggleable per-profile and remain visible.
    """
    from reachy_mini_gemini_live.tools.tool_constants import SystemTool

    # Exclude system tools (always loaded) + internal infrastructure modules
    # that don't define a user-facing Tool subclass.
    excluded = {t.value for t in SystemTool} | {
        "__init__",
        "core_tools",
        "tool_constants",
        "background_tool_manager",
        "mcp_adapter",
        "mcp_client",
    }

    shared: List[str] = []
    try:
        for py in _tools_dir().glob("*.py"):
            if py.stem in excluded:
                continue
            shared.append(py.stem)
    except Exception:
        pass
    local: List[str] = []
    try:
        if selected != DEFAULT_OPTION:
            for py in resolve_profile_dir(selected).glob("*.py"):
                if py.stem in excluded:
                    continue
                local.append(py.stem)
    except Exception:
        pass
    return sorted(set(shared + local))


def _write_profile(
    name_s: str,
    instructions: str,
    tools_text: str,
    voice: str = "cedar",
    grounding: bool = False,
    mcp_servers_json: str = "",
    memory: bool = False,
) -> None:
    target_dir = _profiles_root() / name_s
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "instructions.txt").write_text(instructions.strip() + "\n", encoding="utf-8")
    (target_dir / "tools.txt").write_text((tools_text or "").strip() + "\n", encoding="utf-8")
    (target_dir / "voice.txt").write_text((voice or "cedar").strip() + "\n", encoding="utf-8")
    grounding_file = target_dir / "grounding.txt"
    if grounding:
        grounding_file.write_text("google_search=true\n", encoding="utf-8")
    elif grounding_file.exists():
        grounding_file.unlink()
    mcp_file = target_dir / "mcp_servers.json"
    mcp_text = (mcp_servers_json or "").strip()
    if mcp_text and mcp_text != "[]":
        mcp_file.write_text(mcp_text + "\n", encoding="utf-8")
    elif mcp_file.exists():
        mcp_file.unlink()
    memory_file = target_dir / "memory.txt"
    if memory:
        memory_file.write_text("enabled=true\n", encoding="utf-8")
    elif memory_file.exists():
        memory_file.unlink()
