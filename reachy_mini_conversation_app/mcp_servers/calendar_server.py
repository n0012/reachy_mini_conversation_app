"""Home Assistant calendar MCP server.

Exposes get_calendar_events tool that queries HA's REST API for events
across all configured calendars for a given date range.

Environment vars required:
  HA_URL        - e.g. http://10.0.0.5:8123
  HA_MCP_TOKEN  - Long-Lived Access Token
"""

import asyncio
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123")
HA_TOKEN = os.environ.get("HA_MCP_TOKEN", "")

def _get_local_tz() -> ZoneInfo:
    """Get the timezone from HA config, falling back to UTC."""
    try:
        data = _ha_get("/api/config")
        tz_name = data.get("time_zone", "UTC")
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


# Configure your calendar entity IDs from Home Assistant.
# Find them at: Settings → Devices & Services → Google Calendar
# Each key is a short name you use in conversation ("check my work calendar")
CALENDARS = {
    "personal": "calendar.personal",    # replace with your HA calendar entity IDs
    "family": "calendar.family",
    "birthdays": "calendar.birthdays",
    "holidays": "calendar.holidays_in_united_states",
}


def _ha_get(path: str) -> list:
    url = f"{HA_URL}{path}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return []


def _ha_post(path: str, body: dict) -> dict:
    url = f"{HA_URL}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except Exception as e:
        return {"error": str(e)}


def _get_events(calendar_id: str, start: str, end: str) -> list:
    path = f"/api/calendars/{calendar_id}?start={start}&end={end}"
    events = _ha_get(path)
    result = []
    for e in events:
        start_dt = e.get("start", {})
        end_dt = e.get("end", {})
        result.append({
            "summary": e.get("summary", ""),
            "start": start_dt.get("dateTime") or start_dt.get("date", ""),
            "end": end_dt.get("dateTime") or end_dt.get("date", ""),
            "description": e.get("description", ""),
            "location": e.get("location", ""),
            "uid": e.get("uid", ""),
        })
    return result


server = Server("calendar")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="create_calendar_event",
            description=(
                "Create a new calendar event in a Home Assistant calendar. "
                "Always confirm the details with the user before creating. "
                "Use the calendar names configured in CALENDARS (e.g. personal, family)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "calendar": {
                        "type": "string",
                        "description": "Which calendar to use (e.g. personal, family)",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Event title",
                    },
                    "start_datetime": {
                        "type": "string",
                        "description": "Start date/time as YYYY-MM-DDTHH:MM:SS or YYYY-MM-DD for all-day",
                    },
                    "end_datetime": {
                        "type": "string",
                        "description": "End date/time as YYYY-MM-DDTHH:MM:SS or YYYY-MM-DD for all-day",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional event description",
                    },
                    "location": {
                        "type": "string",
                        "description": "Optional location",
                    },
                    "attendees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of attendee email addresses to invite",
                    },
                },
                "required": ["calendar", "summary", "start_datetime", "end_datetime"],
            },
        ),
        Tool(
            name="get_calendar_events",
            description=(
                "Get upcoming calendar events from Home Assistant calendars. "
                "Use this for any calendar or schedule question. "
                "Available calendars are configured in CALENDARS (e.g. personal, family, birthdays, holidays). "
                "Leave calendar blank to query all calendars."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "calendar": {
                        "type": "string",
                        "description": "Which calendar to query, or 'all' for every calendar",
                        "default": "all",
                    },
                    "days_ahead": {
                        "type": "integer",
                        "description": "How many days ahead to look (default 7)",
                        "default": 7,
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Optional start date YYYY-MM-DD (default: today)",
                    },
                },
            },
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "create_calendar_event":
        default_cal = next(iter(CALENDARS))  # first calendar in CALENDARS is the default
        calendar = (arguments.get("calendar") or default_cal).strip().lower()
        cal_id = CALENDARS.get(calendar, CALENDARS[default_cal])
        summary = arguments.get("summary", "")
        start = arguments.get("start_datetime", "")
        end = arguments.get("end_datetime", "")
        desc = arguments.get("description", "")
        location = arguments.get("location", "")

        payload: dict = {"entity_id": cal_id, "summary": summary}
        if "T" in start:
            payload["start_date_time"] = start
            payload["end_date_time"] = end
        else:
            payload["start_date"] = start
            payload["end_date"] = end
        if desc:
            payload["description"] = desc
        if location:
            payload["location"] = location
        attendees = arguments.get("attendees") or []
        if attendees:
            payload["attendees"] = [{"email": e} for e in attendees]

        result = _ha_post("/api/services/calendar/create_event", payload)
        if "error" in result:
            # HA may not support attendees — retry without them
            if attendees and "400" in str(result.get("error", "")):
                payload.pop("attendees", None)
                result = _ha_post("/api/services/calendar/create_event", payload)
                if "error" in result:
                    return [TextContent(type="text", text=f"Failed to create event: {result['error']}")]
                return [TextContent(type="text", text=
                    f"Created '{summary}' on {start} in {calendar} calendar. "
                    f"Note: invite emails ({', '.join(attendees)}) could not be added automatically — send invites manually.")]
            return [TextContent(type="text", text=f"Failed to create event: {result['error']}")]
        invited = f" — invited {', '.join(attendees)}" if attendees else ""
        return [TextContent(type="text", text=f"Created '{summary}' on {start} in {calendar} calendar{invited}.")]

    if name != "get_calendar_events":
        return [TextContent(type="text", text="Unknown tool")]

    calendar = (arguments.get("calendar") or "all").strip().lower()
    days_ahead = int(arguments.get("days_ahead") or 7)
    start_date = arguments.get("start_date", "")

    if start_date:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            start_dt = datetime.now(timezone.utc)
    else:
        # Start from midnight local time so "today/tonight" events aren't missed
        local_tz = _get_local_tz()
        now_local = datetime.now(local_tz)
        local_midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        start_dt = local_midnight.astimezone(timezone.utc)

    end_dt = start_dt + timedelta(days=days_ahead + 1)  # +1 buffer for timezone offsets
    start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    if calendar == "all":
        targets = CALENDARS
    elif calendar in CALENDARS:
        targets = {calendar: CALENDARS[calendar]}
    else:
        targets = CALENDARS

    all_events = []
    for cal_name, cal_id in targets.items():
        events = _get_events(cal_id, start_iso, end_iso)
        for e in events:
            e["calendar"] = cal_name
            all_events.append(e)

    all_events.sort(key=lambda e: e.get("start", ""))

    if not all_events:
        return [TextContent(type="text", text=f"No events found in the next {days_ahead} days.")]

    lines = [f"Events {start_dt.strftime('%b %d')} – {end_dt.strftime('%b %d')}:"]
    for e in all_events:
        start_str = e["start"][:16].replace("T", " ") if "T" in e["start"] else e["start"]
        cal_label = f"[{e['calendar']}]" if calendar == "all" else ""
        desc = f" — {e['description']}" if e.get("description") else ""
        loc = f" @ {e['location']}" if e.get("location") else ""
        lines.append(f"  {start_str} {cal_label} {e['summary']}{loc}{desc}")

    return [TextContent(type="text", text="\n".join(lines))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
