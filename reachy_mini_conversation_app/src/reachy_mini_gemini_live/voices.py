"""Gemini Live voice constants and validation."""

from reachy_mini_gemini_live.config import AVAILABLE_VOICES, DEFAULT_VOICE


SUPPORTED_GEMINI_VOICES: set[str] = set(AVAILABLE_VOICES)


def validate_voice(name: str) -> str:
    """Return the voice name if valid, otherwise raise ValueError.

    Accepts any casing — normalises to title-case on match.
    """
    normalised = {v.lower(): v for v in SUPPORTED_GEMINI_VOICES}
    canonical = normalised.get((name or "").strip().lower())
    if canonical is None:
        raise ValueError(
            f"Unknown voice {name!r}. Valid voices: {sorted(SUPPORTED_GEMINI_VOICES)}"
        )
    return canonical


def resolve_voice(name: str | None) -> str:
    """Return a valid Gemini voice name, falling back to the default."""
    if not name:
        return DEFAULT_VOICE
    normalised = {v.lower(): v for v in SUPPORTED_GEMINI_VOICES}
    return normalised.get((name or "").strip().lower(), DEFAULT_VOICE)
