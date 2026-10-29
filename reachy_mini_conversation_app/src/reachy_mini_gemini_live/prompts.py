import os
import re
import logging
from pathlib import Path

from reachy_mini_gemini_live.config import DEFAULT_PROFILES_DIRECTORY, config, get_default_voice_for_backend


logger = logging.getLogger(__name__)


PROMPTS_LIBRARY_DIRECTORY = Path(__file__).parent / "prompts"
INSTRUCTIONS_FILENAME = "instructions.txt"
VOICE_FILENAME = "voice.txt"
GROUNDING_FILENAME = "grounding.txt"


def _expand_prompt_includes(content: str) -> str:
    """Expand [<name>] placeholders with content from prompts library files.

    Args:
        content: The template content with [<name>] placeholders

    Returns:
        Expanded content with placeholders replaced by file contents

    """
    # Pattern to match [<name>] where name is a valid file stem (alphanumeric, underscores, hyphens)
    # pattern = re.compile(r'^\[([a-zA-Z0-9_-]+)\]$')
    # Allow slashes for subdirectories
    pattern = re.compile(r"^\[([a-zA-Z0-9/_-]+)\]$")

    lines = content.split("\n")
    expanded_lines = []

    for line in lines:
        stripped = line.strip()
        match = pattern.match(stripped)

        if match:
            # Extract the name from [<name>]
            template_name = match.group(1)
            template_file = PROMPTS_LIBRARY_DIRECTORY / f"{template_name}.txt"

            try:
                if template_file.exists():
                    template_content = template_file.read_text(encoding="utf-8").rstrip()
                    expanded_lines.append(template_content)
                    logger.debug("Expanded template: [%s]", template_name)
                else:
                    logger.warning("Template file not found: %s, keeping placeholder", template_file)
                    expanded_lines.append(line)
            except Exception as e:
                logger.warning("Failed to read template '%s': %s, keeping placeholder", template_name, e)
                expanded_lines.append(line)
        else:
            expanded_lines.append(line)

    return "\n".join(expanded_lines)


_FALLBACK_PROFILE = "reachy-gemini"


def _read_instructions(instructions_file) -> str | None:
    """Return file contents (with includes expanded) or None if missing/empty/error."""
    try:
        if instructions_file.exists():
            text = instructions_file.read_text(encoding="utf-8").strip()
            if text:
                return _expand_prompt_includes(text)
    except Exception as e:
        logger.error(f"Failed to read {instructions_file}: {e}")
    return None


def get_session_instructions() -> str:
    """Get session instructions, loading from REACHY_MINI_CUSTOM_PROFILE if set.

    Falls back to the built-in default profile (and then to the bundled
    default prompt) if the configured profile is missing — instead of
    sys.exit(1), which crashes the whole app on a stale .env entry.
    """
    profile = config.REACHY_MINI_CUSTOM_PROFILE
    if profile:
        if config.PROFILES_DIRECTORY != DEFAULT_PROFILES_DIRECTORY:
            logger.info("Loading prompt from external profile '%s' (root=%s)", profile, config.PROFILES_DIRECTORY)
        else:
            logger.info(f"Loading prompt from profile '{profile}'")
        text = _read_instructions(config.PROFILES_DIRECTORY / profile / INSTRUCTIONS_FILENAME)
        if text is not None:
            return text
        logger.warning(
            "Profile '%s' missing or empty — falling back to '%s'",
            profile,
            _FALLBACK_PROFILE,
        )

    # Fallback path: try the built-in reachy-gemini profile, then the bundled default prompt
    text = _read_instructions(DEFAULT_PROFILES_DIRECTORY / _FALLBACK_PROFILE / INSTRUCTIONS_FILENAME)
    if text is not None:
        return text

    default_path = PROMPTS_LIBRARY_DIRECTORY / "default_prompt.txt"
    logger.info(f"Loading bundled default prompt from {default_path}")
    text = _read_instructions(default_path)
    if text is not None:
        return text

    logger.error("No instructions found anywhere — using minimal placeholder")
    return "You are Reachy Mini, a friendly conversational robot."


def get_session_voice(default: str | None = None) -> str:
    """Resolve the voice to use for the session.

    If a custom profile is selected and contains a voice.txt, return its
    trimmed content; otherwise return the provided default or the active
    backend default voice.
    """
    fallback = get_default_voice_for_backend() if default is None else default
    profile = config.REACHY_MINI_CUSTOM_PROFILE
    if not profile:
        return fallback
    try:
        voice_file = config.PROFILES_DIRECTORY / profile / VOICE_FILENAME
        if voice_file.exists():
            voice = voice_file.read_text(encoding="utf-8").strip()
            return voice or fallback
    except Exception:
        pass
    return fallback


def get_session_grounding() -> bool:
    """Return True if Google Search grounding should be enabled for this session.

    Priority order:
    1. Per-profile grounding.txt containing "google_search=true" (or just "true")
    2. Global env var GOOGLE_SEARCH_GROUNDING=1
    """
    profile = config.REACHY_MINI_CUSTOM_PROFILE
    if profile:
        try:
            grounding_file = config.PROFILES_DIRECTORY / profile / GROUNDING_FILENAME
            if grounding_file.exists():
                content = grounding_file.read_text(encoding="utf-8").strip().lower()
                return content in {"true", "1", "yes", "google_search=true"}
        except Exception:
            pass

    return os.environ.get("GOOGLE_SEARCH_GROUNDING", "").strip().lower() in {"1", "true", "yes"}
