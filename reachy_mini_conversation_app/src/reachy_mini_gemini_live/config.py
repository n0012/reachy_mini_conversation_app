import os
import sys
import shutil
import logging
from pathlib import Path
from importlib.resources import files

from dotenv import find_dotenv, load_dotenv

logger = logging.getLogger(__name__)


# Locked profile: set to a profile name (e.g., "astronomer") to lock the app
# to that profile and disable all profile switching. Leave as None for normal behavior.
LOCKED_PROFILE: str | None = None
PROJECT_ROOT = Path(__file__).parents[2].resolve()

# Stable per-user config directory — survives reinstalls and launcher reboots.
# Distinct from upstream Pollen's ~/.reachy_mini/ so this fork can coexist.
REACHY_MINI_DIR: Path = Path.home() / ".reachy_mini_gemini_live"
REACHY_MINI_ENV_PATH: Path = REACHY_MINI_DIR / ".env"
_LEGACY_REACHY_MINI_DIR: Path = Path.home() / ".reachy_mini"
_LEGACY_MIGRATION_FILES = (".env", "service_account.json", "active_user.json")
_LEGACY_MIGRATION_DIRS = ("memory", "mcp_servers")


def _migrate_legacy_dotdir() -> None:
    """One-time copy of fork-owned files from ~/.reachy_mini/ to the new dotdir.

    Earlier versions of this fork wrote config under ~/.reachy_mini/ — the same
    dotdir upstream Pollen could one day adopt. We now isolate to
    ~/.reachy_mini_gemini_live/. If the new dir is missing but the legacy one
    has our files, copy them over so existing installs keep working without
    re-running the setup wizard.
    """
    if REACHY_MINI_DIR.exists() or not _LEGACY_REACHY_MINI_DIR.is_dir():
        return
    has_payload = any(
        (_LEGACY_REACHY_MINI_DIR / name).exists()
        for name in (*_LEGACY_MIGRATION_FILES, *_LEGACY_MIGRATION_DIRS)
    )
    if not has_payload:
        return
    try:
        REACHY_MINI_DIR.mkdir(parents=True, exist_ok=True)
        for name in _LEGACY_MIGRATION_FILES:
            src = _LEGACY_REACHY_MINI_DIR / name
            if src.is_file():
                shutil.copy2(src, REACHY_MINI_DIR / name)
        for name in _LEGACY_MIGRATION_DIRS:
            src = _LEGACY_REACHY_MINI_DIR / name
            if src.is_dir():
                shutil.copytree(src, REACHY_MINI_DIR / name, dirs_exist_ok=True)
        logger.info("Migrated legacy config from %s to %s", _LEGACY_REACHY_MINI_DIR, REACHY_MINI_DIR)
    except Exception as exc:
        logger.warning("Legacy dotdir migration failed: %s", exc)


_migrate_legacy_dotdir()


def _env_flag(name: str, default: bool = False) -> bool:
    """Parse a boolean environment flag.

    Accepted truthy values: 1, true, yes, on
    Accepted falsy values: 0, false, no, off
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    logger.warning("Invalid boolean value for %s=%r, using default=%s", name, raw, default)
    return default


# Load .env BEFORE any env-dependent module-level computation runs.
# Without this, REACHY_MINI_PROFILES_OVERRIDE (and friends) are missed at
# module load time, so DEFAULT_PROFILES_DIRECTORY and Config.PROFILES_DIRECTORY
# can resolve to different paths — which trips the collision check in __init__.
if _env_flag("REACHY_MINI_SKIP_DOTENV", default=False):
    logger.info("Skipping .env loading because REACHY_MINI_SKIP_DOTENV is set")
elif REACHY_MINI_ENV_PATH.exists():
    load_dotenv(dotenv_path=REACHY_MINI_ENV_PATH, override=True)
    logger.info("Configuration loaded from %s", REACHY_MINI_ENV_PATH)
else:
    _dotenv_path = find_dotenv(usecwd=True)
    if _dotenv_path:
        load_dotenv(dotenv_path=_dotenv_path, override=True)
        logger.info("Configuration loaded from %s", _dotenv_path)
    else:
        logger.warning("No .env file found, using environment variables")


def _is_source_checkout_root(root: Path) -> bool:
    """Return whether the given root looks like this project's source checkout."""
    return (root / "pyproject.toml").is_file() and (root / "src" / "reachy_mini_gemini_live").is_dir()


def _packaged_profiles_directory() -> Path | None:
    """Return the installed wheel's packaged profiles directory when available."""
    try:
        return Path(str(files("reachy_talk_data").joinpath("profiles")))
    except Exception:
        return None


def _resolve_default_profiles_directory() -> Path:
    """Resolve built-in profiles from source checkout or installed package data.

    REACHY_MINI_PROFILES_OVERRIDE env var takes priority — used to point the
    installed app at live source files so edits don't require a reinstall.
    """
    override = os.getenv("REACHY_MINI_PROFILES_OVERRIDE")
    if override:
        p = Path(override).expanduser().resolve()
        if p.is_dir():
            return p

    source_profiles = PROJECT_ROOT / "profiles"
    if _is_source_checkout_root(PROJECT_ROOT) and source_profiles.is_dir():
        return source_profiles

    packaged_profiles = _packaged_profiles_directory()
    if packaged_profiles is not None and packaged_profiles.is_dir():
        return packaged_profiles

    return source_profiles


DEFAULT_PROFILES_DIRECTORY = _resolve_default_profiles_directory()

# Voices supported by the Gemini Live API
AVAILABLE_VOICES: list[str] = [
    "Achird",
    "Achernar",
    "Algenib",
    "Algieba",
    "Alnilam",
    "Aoede",
    "Autonoe",
    "Callirrhoe",
    "Charon",
    "Despina",
    "Enceladus",
    "Erinome",
    "Fenrir",
    "Gacrux",
    "Iapetus",
    "Kore",
    "Laomedeia",
    "Leda",
    "Orus",
    "Pulcherrima",
    "Puck",
    "Rasalgethi",
    "Sadachbia",
    "Sadaltager",
    "Schedar",
    "Sulafat",
    "Umbriel",
    "Vindemiatrix",
    "Zephyr",
    "Zubenelgenubi",
]

DEFAULT_MODEL_NAME = "gemini-3.1-flash-live-preview"
DEFAULT_VOICE = "Kore"

GEMINI_BACKEND = "gemini"


def _resolve_model_name(model_name: str | None = None) -> str:
    """Return a configured model name, falling back to the default Gemini Live model."""
    candidate = (model_name or "").strip()
    return candidate if candidate else DEFAULT_MODEL_NAME


DEFAULT_AUTO_SLEEP_MINUTES = 10
AUTO_SLEEP_MINUTES_RANGE = (1, 60)


def _resolve_auto_sleep_minutes(value: str | None) -> int:
    """Parse the auto-sleep minutes env var, clamped to AUTO_SLEEP_MINUTES_RANGE."""
    try:
        minutes = int((value or "").strip()) if value else DEFAULT_AUTO_SLEEP_MINUTES
    except ValueError:
        minutes = DEFAULT_AUTO_SLEEP_MINUTES
    lo, hi = AUTO_SLEEP_MINUTES_RANGE
    return max(lo, min(hi, minutes))


def _collect_profile_names(profiles_root: Path) -> set[str]:
    """Return profile folder names from a profiles root directory."""
    if not profiles_root.exists() or not profiles_root.is_dir():
        return set()
    return {p.name for p in profiles_root.iterdir() if p.is_dir()}


def _collect_tool_module_names(tools_root: Path) -> set[str]:
    """Return tool module names from a tools directory."""
    if not tools_root.exists() or not tools_root.is_dir():
        return set()
    ignored = {"__init__", "core_tools"}
    return {p.stem for p in tools_root.glob("*.py") if p.is_file() and p.stem not in ignored}


def _raise_on_name_collisions(
    *,
    label: str,
    external_root: Path,
    internal_root: Path,
    external_names: set[str],
    internal_names: set[str],
) -> None:
    """Raise with a clear message when external/internal names collide."""
    collisions = sorted(external_names & internal_names)
    if not collisions:
        return

    raise RuntimeError(
        f"Config.__init__(): Ambiguous {label} names found in both external and built-in libraries: {collisions}. "
        f"External {label} root: {external_root}. Built-in {label} root: {internal_root}. "
        f"Please rename the conflicting external {label}(s) to continue."
    )


# Validate LOCKED_PROFILE at startup
if LOCKED_PROFILE is not None:
    _profiles_dir = DEFAULT_PROFILES_DIRECTORY
    _profile_path = _profiles_dir / LOCKED_PROFILE
    _instructions_file = _profile_path / "instructions.txt"
    if not _profile_path.is_dir():
        print(f"Error: LOCKED_PROFILE '{LOCKED_PROFILE}' does not exist in {_profiles_dir}", file=sys.stderr)
        sys.exit(1)
    if not _instructions_file.is_file():
        print(f"Error: LOCKED_PROFILE '{LOCKED_PROFILE}' has no instructions.txt", file=sys.stderr)
        sys.exit(1)

class Config:
    """Configuration class for the conversation app."""

    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    MODEL_NAME = _resolve_model_name(os.getenv("MODEL_NAME"))
    HF_HOME = os.getenv("HF_HOME", "./cache")
    LOCAL_VISION_MODEL = os.getenv("LOCAL_VISION_MODEL", "HuggingFaceTB/SmolVLM2-2.2B-Instruct")
    HF_TOKEN = os.getenv("HF_TOKEN")  # Optional, falls back to hf auth login if not set

    logger.debug(
        "Model: %s, HF_HOME: %s, Vision Model: %s",
        MODEL_NAME,
        HF_HOME,
        LOCAL_VISION_MODEL,
    )

    # Filesystem root containing profile directories, not a Python import path.
    _profiles_override_env = os.getenv("REACHY_MINI_PROFILES_OVERRIDE")
    _profiles_directory_env = os.getenv("REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY")
    PROFILES_DIRECTORY = (
        Path(_profiles_override_env).expanduser().resolve() if _profiles_override_env
        else Path(_profiles_directory_env) if _profiles_directory_env
        else DEFAULT_PROFILES_DIRECTORY
    )
    _tools_directory_env = os.getenv("REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY")
    TOOLS_DIRECTORY = Path(_tools_directory_env) if _tools_directory_env else None
    AUTOLOAD_EXTERNAL_TOOLS = _env_flag("AUTOLOAD_EXTERNAL_TOOLS", default=False)
    REACHY_MINI_CUSTOM_PROFILE = LOCKED_PROFILE or os.getenv("REACHY_MINI_CUSTOM_PROFILE") or "reachy-gemini"
    AUTO_SLEEP_MINUTES = _resolve_auto_sleep_minutes(os.getenv("REACHY_MINI_AUTO_SLEEP_MINUTES"))

    logger.debug(f"Custom Profile: {REACHY_MINI_CUSTOM_PROFILE}")

    def __init__(self) -> None:
        """Initialize the configuration."""
        if self.REACHY_MINI_CUSTOM_PROFILE and self.PROFILES_DIRECTORY != DEFAULT_PROFILES_DIRECTORY:
            selected_profile_path = self.PROFILES_DIRECTORY / self.REACHY_MINI_CUSTOM_PROFILE
            if not selected_profile_path.is_dir():
                available_profiles = sorted(_collect_profile_names(self.PROFILES_DIRECTORY))
                raise RuntimeError(
                    "Config.__init__(): Selected profile "
                    f"'{self.REACHY_MINI_CUSTOM_PROFILE}' was not found in external profiles root "
                    f"{self.PROFILES_DIRECTORY}. "
                    f"Available external profiles: {available_profiles}. "
                    "Either set 'REACHY_MINI_CUSTOM_PROFILE' to one of the available external profiles "
                    "or unset 'REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY' to use built-in profiles."
                )

        if self.PROFILES_DIRECTORY != DEFAULT_PROFILES_DIRECTORY:
            external_profiles = _collect_profile_names(self.PROFILES_DIRECTORY)
            internal_profiles = _collect_profile_names(DEFAULT_PROFILES_DIRECTORY)
            _raise_on_name_collisions(
                label="profile",
                external_root=self.PROFILES_DIRECTORY,
                internal_root=DEFAULT_PROFILES_DIRECTORY,
                external_names=external_profiles,
                internal_names=internal_profiles,
            )

        if self.TOOLS_DIRECTORY is not None:
            builtin_tools_root = Path(__file__).parent / "tools"
            external_tools = _collect_tool_module_names(self.TOOLS_DIRECTORY)
            internal_tools = _collect_tool_module_names(builtin_tools_root)
            _raise_on_name_collisions(
                label="tool",
                external_root=self.TOOLS_DIRECTORY,
                internal_root=builtin_tools_root,
                external_names=external_tools,
                internal_names=internal_tools,
            )

        if self.PROFILES_DIRECTORY != DEFAULT_PROFILES_DIRECTORY:
            logger.warning(
                "Environment variable 'REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY' is set. "
                "Profiles (instructions.txt, ...) will be loaded from %s.",
                self.PROFILES_DIRECTORY,
            )
        else:
            logger.info(
                "'REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY' is not set. Using built-in profiles from %s.",
                DEFAULT_PROFILES_DIRECTORY,
            )

        if self.TOOLS_DIRECTORY is not None:
            logger.warning(
                "Environment variable 'REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY' is set. "
                "External tools will be loaded from %s.",
                self.TOOLS_DIRECTORY,
            )
        else:
            logger.info("'REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY' is not set. Using built-in shared tools only.")


config = Config()


def refresh_runtime_config_from_env() -> None:
    """Refresh mutable runtime config fields from the current environment."""
    config.GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    config.MODEL_NAME = _resolve_model_name(os.getenv("MODEL_NAME"))
    config.REACHY_MINI_CUSTOM_PROFILE = LOCKED_PROFILE or os.getenv("REACHY_MINI_CUSTOM_PROFILE")
    config.AUTO_SLEEP_MINUTES = _resolve_auto_sleep_minutes(os.getenv("REACHY_MINI_AUTO_SLEEP_MINUTES"))
    override = os.getenv("REACHY_MINI_PROFILES_OVERRIDE")
    if override:
        p = Path(override).expanduser().resolve()
        if p.is_dir():
            config.PROFILES_DIRECTORY = p


def get_backend_choice(model_name: str | None = None) -> str:
    """Always returns gemini — this fork is Gemini-only."""
    return GEMINI_BACKEND


def get_model_name_for_backend(backend: str) -> str:
    """Returns the configured model name regardless of backend arg."""
    return config.MODEL_NAME


def get_available_voices_for_backend(backend: str | None = None) -> list[str]:
    """Return the curated Gemini voice list. The `backend` param is kept for API compatibility."""
    return list(AVAILABLE_VOICES)


def get_default_voice_for_backend(backend: str | None = None) -> str:
    """Return the default Gemini voice. The `backend` param is kept for API compatibility."""
    return DEFAULT_VOICE


def is_gemini_model() -> bool:
    """Always True — this fork is Gemini-only."""
    return True


def set_custom_profile(profile: str | None) -> None:
    """Update the selected custom profile at runtime and expose it via env.

    This ensures modules that read `config` and code that inspects the
    environment see a consistent value.
    """
    if LOCKED_PROFILE is not None:
        return
    try:
        config.REACHY_MINI_CUSTOM_PROFILE = profile
    except Exception:
        pass
    try:
        import os as _os

        if profile:
            _os.environ["REACHY_MINI_CUSTOM_PROFILE"] = profile
        else:
            # Remove to reflect default
            _os.environ.pop("REACHY_MINI_CUSTOM_PROFILE", None)
    except Exception:
        pass
