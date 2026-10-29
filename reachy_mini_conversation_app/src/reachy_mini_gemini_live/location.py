"""Infer location context from public IP at session start.

Uses ipinfo.io (free, no API key). Result is injected into system_instruction
so Reachy knows local defaults (city, weather units, timezone) without any
configuration. Memory facts override these defaults as conversations accumulate.
"""

from __future__ import annotations
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime


try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]


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

        city = data.get("city", "")
        region = data.get("region", "")
        country = data.get("country", "")
        timezone = data.get("timezone", "")

        if not city and not region:
            logger.warning("Location: lookup returned no city/region")
            return ""

        temp_unit = "Fahrenheit" if country in _FAHRENHEIT_COUNTRIES else "Celsius"
        location_str = ", ".join(filter(None, [city, region, country]))

        # Compute current local time so the model doesn't default to UTC
        local_time_str = ""
        if timezone:
            try:
                tz = ZoneInfo(timezone)
                now = datetime.now(tz)
                local_time_str = now.strftime("%I:%M %p %Z, %A %B %-d, %Y")
            except Exception:
                pass

        logger.warning(
            "Location: inferred %s (tz=%s, units=%s, local=%s)",
            location_str,
            timezone,
            temp_unit,
            local_time_str,
        )

        lines = [
            "## User location (confirmed via network lookup — treat as authoritative)",
            f"- The user is located in: {location_str}",
            f"- Current local time: {local_time_str}" if local_time_str else "",
            f"- Timezone: {timezone}" if timezone else "",
            f"- Temperature units: {temp_unit}",
            f"- When asked 'where am I?' or 'where are we?' answer: '{location_str}'",
            "- Use this for all local weather, news, sports, and time references.",
            "- Always use the local time above when asked what time it is — never UTC.",
            "- If the user corrects their location, update and use their answer instead.",
        ]
        return "\n".join(line for line in lines if line)

    except urllib.error.URLError as exc:
        logger.warning("Location: lookup failed (network): %s", exc)
        return ""
    except Exception as exc:
        logger.warning("Location: lookup failed unexpectedly: %s", exc)
        return ""
