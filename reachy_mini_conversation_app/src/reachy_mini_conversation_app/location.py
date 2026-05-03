"""Infer location context from public IP at session start.

Uses ipinfo.io (free, no API key). Result is injected into system_instruction
so Reachy knows local defaults (city, weather units, timezone) without any
configuration. Memory facts override these defaults as conversations accumulate.
"""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error


logger = logging.getLogger(__name__)

# Countries that use Fahrenheit (US, Bahamas, Cayman Islands, Liberia, Palau)
_FAHRENHEIT_COUNTRIES = {"US", "BS", "KY", "LR", "PW"}


def get_location_context() -> str:
    """Return a system prompt block describing the inferred local context.

    Returns empty string if the lookup fails — never raises.
    """
    try:
        with urllib.request.urlopen("https://ipinfo.io/json", timeout=5) as resp:
            data = json.loads(resp.read())

        city     = data.get("city", "")
        region   = data.get("region", "")
        country  = data.get("country", "")
        timezone = data.get("timezone", "")
        org      = data.get("org", "")

        if not city and not region:
            logger.info("Location: lookup returned no city/region")
            return ""

        temp_unit = "Fahrenheit" if country in _FAHRENHEIT_COUNTRIES else "Celsius"
        location_str = ", ".join(filter(None, [city, region, country]))

        logger.info(
            "Location: inferred %s (tz=%s, units=%s, org=%s)",
            location_str, timezone, temp_unit, org,
        )

        lines = [
            "## Location defaults (inferred from network — memory overrides these)",
            f"- Location: {location_str}",
            f"- Timezone: {timezone}" if timezone else "",
            f"- Temperature units: {temp_unit}",
            "- Use this for local news, weather, sports, and time references.",
            "- If the user mentions a different location or preference, that takes priority.",
        ]
        return "\n".join(line for line in lines if line)

    except urllib.error.URLError as exc:
        logger.info("Location: lookup failed (network): %s", exc)
        return ""
    except Exception as exc:
        logger.warning("Location: lookup failed unexpectedly: %s", exc)
        return ""
