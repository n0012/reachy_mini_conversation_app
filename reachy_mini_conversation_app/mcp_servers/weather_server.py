#!/usr/bin/env python3
"""Weather MCP server using wttr.in — no API key required.

Returns clean current conditions without the forecast table that confuses
the grounding model into reporting overnight lows instead of current temp.

Usage (stdio transport):
    python3 weather_server.py
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("weather")


@mcp.tool()
def get_current_weather(location: str) -> str:
    """Get the current temperature and weather conditions for a location.

    Returns only the current conditions (not forecast), preventing the model
    from accidentally reporting tomorrow's low instead of today's temperature.

    Args:
        location: City name, zip code, or 'City,State' (e.g. 'Springfield,IL' or '62701')

    """
    try:
        url = f"https://wttr.in/{urllib.parse.quote(location)}?format=j1"
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read())

        current = data["current_condition"][0]
        area = data.get("nearest_area", [{}])[0]

        temp_f = current.get("temp_F", "?")
        temp_c = current.get("temp_C", "?")
        desc = current.get("weatherDesc", [{}])[0].get("value", "unknown")
        humidity = current.get("humidity", "?")
        feels_f = current.get("FeelsLikeF", "?")
        wind_mph = current.get("windspeedMiles", "?")

        area_name = area.get("areaName", [{}])[0].get("value", location)
        country = area.get("country", [{}])[0].get("value", "")

        return (
            f"Current weather in {area_name}{', ' + country if country else ''}:\n"
            f"  Temperature: {temp_f}°F ({temp_c}°C)\n"
            f"  Feels like: {feels_f}°F\n"
            f"  Conditions: {desc}\n"
            f"  Humidity: {humidity}%\n"
            f"  Wind: {wind_mph} mph"
        )

    except urllib.error.URLError as e:
        return f"Weather lookup failed (network error): {e}"
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        return f"Weather lookup failed (parse error): {e}"
    except Exception as e:
        return f"Weather lookup failed: {e}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
