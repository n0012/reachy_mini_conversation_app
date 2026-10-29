"""Replay eval: drive the real GeminiLiveHandler through one scripted conversation, read-only.

Runs on the robot with the candidate package first on PYTHONPATH (see run_eval.sh):

  PYTHONPATH=<stage> /venvs/apps_venv/bin/python -u harness.py --scenarios S --audio DIR --out DIR

Mic audio is streamed in real time (speech clips with silence between them, like the
robot's mic) and every handler output is timestamped. Writes events.jsonl, log.txt,
transcript.txt and metrics.json to --out.

Safety, so an eval can never change anything real:
  * Memory Bank is read-only: memory._api passes GET and memories:retrieve only, and the
    backends' write methods (save_session, remember, delete_memory, purge_expired,
    compact_memories) are replaced with recorders.
  * MCP calls other than read-only ones (weather, GetLiveContext, GetDateTime, calendar
    reads) are recorded and answered with a stub — no lights, locks, broadcasts or events.
  * The active-user file points at a temp copy; the profile is a temp copy.
  * No robot motion (mocked ReachyMini / movement), no wake-word thread; the camera returns a
    fixed still (--camera-image) or a gray frame.
"""

import os
import re
import sys
import json
import time
import wave
import shutil
import asyncio
import logging
import argparse
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np


RATE = 16000
FRAME = 640  # 40 ms
REPLY_QUIET_S = 6.0  # robot silence that ends a reply
REPLY_FIRST_AUDIO_S = 15.0  # no audio this long after the user stops = no reply
REPLY_MAX_S = 60.0
HUMAN_PAUSE_S = 1.5
AMBIENT_TAIL_S = 10.0

# Non-answers seen from Gemini Live: "---", "・", "<!-- No response required -->",
# "(No response...)", "<no speech detected>", "<no speech>{pause}". Markup is stripped
# first; whatever has no letters or digits left is not real speech.
MARKUP_RE = re.compile(
    r"<!--.*?-->|<[^<>]*>|\{[^{}]*\}|\(\s*(no response|no reply|silence|silent)[^)]*\)", re.I | re.S
)
READ_ONLY_MCP = {
    ("home_assistant", "GetLiveContext"),
    ("home_assistant", "GetDateTime"),
    ("calendar", "get_calendar_events"),
}
READ_ONLY_MCP_SERVERS = {"weather"}


def is_placeholder(text: str) -> bool:
    """Return True for an assistant message that is not real speech."""
    return not any(c.isalnum() for c in MARKUP_RE.sub("", text))


class Recorder:
    """Timestamped record of everything the handler emitted, said or logged."""

    def __init__(self) -> None:
        """Start the clock."""
        self.t0 = time.monotonic()
        self.events: list[dict] = []
        self.speech: list[list[float]] = []  # robot audio segments [start, end] in playback time
        self.play_end = 0.0

    def now(self) -> float:
        """Seconds since the run started."""
        return time.monotonic() - self.t0

    def add(self, kind: str, **fields: object) -> None:
        """Record one event."""
        self.events.append({"t": round(self.now(), 2), "kind": kind, **fields})

    def audio(self, seconds: float) -> None:
        """Track robot speech as it would play out of the speaker."""
        t = self.now()
        if t > self.play_end + 0.3 or not self.speech:
            self.speech.append([t, t])
            self.play_end = t
        self.play_end += seconds
        self.speech[-1][1] = self.play_end


class LogCapture(logging.Handler):
    """Keep log records with run-relative timestamps."""

    def __init__(self, rec: Recorder) -> None:
        """Attach to a recorder."""
        super().__init__(logging.DEBUG)
        self.rec = rec
        self.records: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Store one record."""
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        if record.exc_info:
            msg += " | " + logging.Formatter().formatException(record.exc_info).splitlines()[-1]
        self.records.append(
            {"t": round(self.rec.now(), 2), "level": record.levelname, "name": record.name, "msg": msg}
        )


class Mic:
    """Feeds the handler 40 ms frames in real time: queued speech, else quiet room noise."""

    def __init__(self, handler: object, rec: Recorder) -> None:
        """Bind to the handler."""
        self.handler = handler
        self.rec = rec
        self._buf = np.zeros(0, dtype=np.int16)
        self._done = asyncio.Event()
        self._rng = np.random.default_rng(0)

    async def play(self, pcm: np.ndarray) -> tuple[float, float]:
        """Queue a clip; return (start, end) once it has been fully sent."""
        self._done.clear()
        start = self.rec.now()
        self._buf = pcm
        await self._done.wait()
        return start, self.rec.now()

    async def run(self, stop: asyncio.Event) -> None:
        """Stream until stopped."""
        loop = asyncio.get_running_loop()
        next_t = loop.time()
        while not stop.is_set():
            if len(self._buf):
                frame, self._buf = self._buf[:FRAME], self._buf[FRAME:]
                if len(frame) < FRAME:
                    frame = np.concatenate([frame, np.zeros(FRAME - len(frame), dtype=np.int16)])
                if not len(self._buf):
                    self._done.set()
            else:
                frame = self._rng.normal(0, 40, FRAME).astype(np.int16)
            await self.handler.receive((RATE, frame))
            next_t += FRAME / RATE
            await asyncio.sleep(max(0.0, next_t - loop.time()))


def load_wav(path: Path) -> np.ndarray:
    """Read a 16 kHz mono WAV."""
    with wave.open(str(path)) as wf:
        assert wf.getframerate() == RATE and wf.getnchannels() == 1, path
        return np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)


def install_guards(tmp: Path, start_user: str) -> dict:
    """Make memory read-only, stub write-capable MCP tools, redirect the active user."""
    from reachy_mini_gemini_live import memory
    from reachy_mini_gemini_live.tools import mcp_client

    blocked: dict = {"memory": [], "mcp": [], "mcp_allowed": []}

    real_api = memory._api

    def guarded_api(method: str, url: str, body: dict | None = None) -> dict:
        if method == "GET" or (method == "POST" and url.endswith("memories:retrieve")):
            return real_api(method, url, body)
        blocked["memory"].append(f"_api {method} {url.rsplit('/', 1)[-1]}")
        raise PermissionError("eval: Memory Bank writes are disabled")

    memory._api = guarded_api

    write_results = {
        "save_session": "skipped",
        "remember": False,
        "delete_memory": False,
        "purge_expired": 0,
        "compact_memories": {"dry_run": True, "applied": 0},
    }
    for cls in (memory.VertexMemoryBackend, memory.FileMemoryBackend):
        for name, result in write_results.items():
            if hasattr(cls, name):

                async def _blocked(self, *a, _name=name, _result=result, **k):  # noqa: ANN001
                    blocked["memory"].append(_name)
                    return _result

                setattr(cls, name, _blocked)

    real_call = mcp_client.MCPClientPool.call_tool

    async def guarded_call(self, server_name: str, tool_name: str, args: dict) -> str:  # noqa: ANN001
        if server_name in READ_ONLY_MCP_SERVERS or (server_name, tool_name) in READ_ONLY_MCP:
            blocked["mcp_allowed"].append(f"{server_name}__{tool_name}")
            return await real_call(self, server_name, tool_name, args)
        blocked["mcp"].append(f"{server_name}__{tool_name} {json.dumps(args)[:120]}")
        return "Done."

    mcp_client.MCPClientPool.call_tool = guarded_call

    active = tmp / "active_user.json"
    active.write_text(json.dumps({"user_id": start_user}))
    memory._ACTIVE_USER_FILE = active
    return blocked


async def run(args: argparse.Namespace) -> int:
    """Run one conversation and write the results."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="reachy-eval-"))
    rec = Recorder()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    capture = LogCapture(rec)
    logging.getLogger().addHandler(capture)
    logging.getLogger("reachy_mini_gemini_live").setLevel(logging.DEBUG)

    # Same configuration the app loads, but against a temp copy of the profile.
    from dotenv import load_dotenv

    app_dir = Path.home() / ".reachy_mini_gemini_live"
    load_dotenv(app_dir / ".env", override=True)
    profile = os.environ.get("REACHY_MINI_CUSTOM_PROFILE", "default")
    src_profiles = Path(os.environ.get("REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY", app_dir / "profiles"))
    shutil.copytree(src_profiles / profile, tmp / "profiles" / profile)
    if args.profile_overlay:
        shutil.copytree(args.profile_overlay, tmp / "profiles" / profile, dirs_exist_ok=True)
    os.environ["REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY"] = str(tmp / "profiles")

    import reachy_mini_gemini_live

    rec.add("package", path=str(Path(reachy_mini_gemini_live.__file__).parent))
    from reachy_mini_gemini_live import config as config_mod

    if hasattr(config_mod, "refresh_runtime_config_from_env"):
        config_mod.refresh_runtime_config_from_env()

    expectations = json.loads(Path(args.expectations).read_text())
    blocked = install_guards(tmp, expectations["start_user"])

    from reachy_mini_gemini_live import gemini_live
    from reachy_mini_gemini_live.streaming import AdditionalOutputs
    from reachy_mini_gemini_live.tools.core_tools import ToolDependencies

    movement = MagicMock()
    movement.is_idle.return_value = True
    camera = MagicMock()
    frame = np.full((480, 640, 3), 128, dtype=np.uint8)
    if args.camera_image:
        import cv2

        frame = cv2.imread(args.camera_image)  # BGR, like the camera worker
    camera.get_latest_frame.return_value = frame
    deps = ToolDependencies(
        reachy_mini=MagicMock(), movement_manager=movement, camera_worker=camera, head_wobbler=MagicMock()
    )
    handler = gemini_live.GeminiLiveHandler(deps)
    if type(handler._memory).__name__ != "VertexMemoryBackend":
        print(f"✗ memory backend is {type(handler._memory).__name__}, expected Vertex", file=sys.stderr)
        return 2
    handler._start_ei_detection = lambda loop: None
    handler._stop_ei_detection = lambda: None

    async def _no_watchdog(loop: object) -> None:
        return None

    handler._ei_watchdog = _no_watchdog

    stop = asyncio.Event()
    mic = Mic(handler, rec)

    async def emit_loop() -> None:
        while not stop.is_set():
            item = await handler.emit()
            if item is None:
                continue
            if isinstance(item, AdditionalOutputs):
                for msg in item.args:
                    content = msg.get("content", "")
                    if not isinstance(content, str):
                        continue
                    fields = {"role": msg.get("role"), "content": content}
                    if msg.get("metadata"):
                        fields["tool_echo"] = True
                    rec.add("msg", **fields)
            elif isinstance(item, tuple):
                sr, pcm = item
                rec.audio(len(pcm) / sr)

    tasks = [
        asyncio.create_task(handler.start_up(), name="handler"),
        asyncio.create_task(emit_loop(), name="emit"),
        asyncio.create_task(mic.run(stop), name="mic"),
    ]
    try:
        await asyncio.wait_for(handler._connected_event.wait(), timeout=60)
    except asyncio.TimeoutError:
        rec.add("error", what="session never connected")
    rec.add("connected")
    await asyncio.sleep(3.0)

    scenarios = json.loads(Path(args.scenarios).read_text())
    manifest = json.loads((Path(args.audio) / "manifest.json").read_text())
    steps_out: list[dict] = []

    def last_reply_since(t: float) -> str:
        return " ".join(
            e["content"]
            for e in rec.events
            if e["kind"] == "msg" and e["role"] == "assistant" and not e.get("tool_echo") and e["t"] >= t
        )

    async def await_reply(spoke_end: float) -> None:
        while True:
            await asyncio.sleep(0.2)
            now = rec.now()
            started = [s for s in rec.speech if s[0] >= spoke_end - 0.5]
            recent_tool = any(
                e["kind"] == "msg" and e["role"] == "tool" and now - e["t"] < 15 and e["t"] >= spoke_end
                for e in rec.events
            )
            if now - spoke_end > REPLY_MAX_S:
                return
            if not started and not recent_tool and now - spoke_end > REPLY_FIRST_AUDIO_S:
                return
            if started and not recent_tool and now > rec.play_end + REPLY_QUIET_S:
                return

    for step in scenarios["steps"]:
        start = rec.now()
        entry = {"id": step["id"], "kind": step["kind"], "start": round(start, 2)}
        if step["kind"] == "conditional":
            prev = steps_out[-1] if steps_out else None
            reply = last_reply_since(prev["start"]) if prev else ""
            if not re.search(step["if_reply"], reply, re.I):
                entry["skipped"] = True
                steps_out.append(entry)
                continue
        if step["kind"] in ("prompted", "conditional"):
            clip = manifest[step["id"]]
            s, e = await mic.play(load_wav(Path(args.audio) / clip["file"]))
            entry.update(said=clip["text"], said_start=round(s, 2), said_end=round(e, 2))
            rec.add("said", step=step["id"], text=clip["text"])
            await await_reply(e)
            await asyncio.sleep(HUMAN_PAUSE_S)
        elif step["kind"] == "quiet":
            rec.add("quiet", step=step["id"], seconds=step["seconds"])
            await asyncio.sleep(step["seconds"])
        elif step["kind"] == "ambient":
            clip = manifest[step["id"]]
            rec.add("ambient", step=step["id"], text=clip["text"])
            s, e = await mic.play(load_wav(Path(args.audio) / clip["file"]))
            entry.update(said=clip["text"], said_start=round(s, 2), said_end=round(e, 2))
            await asyncio.sleep(AMBIENT_TAIL_S)
        entry["end"] = round(rec.now(), 2)
        steps_out.append(entry)

    await asyncio.sleep(5.0)
    sleeping = bool(handler._sleeping)
    stop.set()
    try:
        await asyncio.wait_for(handler.shutdown(), timeout=30)
    except Exception as exc:
        rec.add("error", what=f"shutdown: {exc}")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    metrics = compute_metrics(rec, capture.records, steps_out, blocked, expectations, sleeping)
    write_outputs(out, rec, capture.records, steps_out, metrics)
    shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps({k: v for k, v in metrics.items() if not isinstance(v, (list, dict))}))
    return 0


def compute_metrics(
    rec: Recorder, logs: list[dict], steps: list[dict], blocked: dict, expectations: dict, sleeping: bool
) -> dict:
    """Code-computed metrics — counts come from code, never from the judge."""
    msgs = [e for e in rec.events if e["kind"] == "msg"]
    assistant = [m for m in msgs if m["role"] == "assistant" and not m.get("tool_echo")]
    chunks = [r for r in logs if r["msg"].startswith("User transcript chunk")]

    def within(t: float, step: dict) -> bool:
        return step["start"] <= t < step.get("end", float("inf"))

    turns = []
    unprompted_speech = 0
    unprompted_text: list[str] = []
    for step in steps:
        if step.get("skipped"):
            continue
        replies = [m["content"] for m in assistant if within(m["t"], step) and not is_placeholder(m["content"])]
        if step["kind"] in ("prompted", "conditional"):
            first_audio = next(
                (s[0] for s in rec.speech if s[0] >= step["said_end"] - 0.5 and within(s[0], step)), None
            )
            heard = "".join(r["msg"].split(":", 1)[1] for r in chunks if within(r["t"], step)).strip()
            turns.append(
                {
                    "step": step["id"],
                    "heard": bool(heard),
                    "heard_text": heard,
                    "replied": bool(replies),
                    "reply": " ".join(replies),
                    "latency_s": round(first_audio - step["said_end"], 2) if first_audio is not None else None,
                }
            )
        else:
            unprompted_speech += sum(1 for s in rec.speech if within(s[0], step))
            unprompted_text += replies

    recall_text = " ".join(t["reply"] for t in turns if t["step"] in {"family", "about_me"})
    names = expectations.get("recall_names", [])
    recalled = [n for n in names if re.search(rf"\b{re.escape(n)}\b", recall_text, re.I)]
    latencies = [t["latency_s"] for t in turns if t["latency_s"] is not None]
    errors = [r for r in logs if r["level"] in ("ERROR", "CRITICAL")]
    return {
        "prompted_turns": len(turns),
        "heard": sum(t["heard"] for t in turns),
        "replied": sum(t["replied"] for t in turns),
        "median_latency_s": round(float(np.median(latencies)), 2) if latencies else None,
        "unprompted_speech": unprompted_speech,
        "placeholders_shown": sum(1 for m in assistant if is_placeholder(m["content"])),
        "tool_echoes_shown": sum(1 for m in msgs if m.get("tool_echo")),
        "restarts": max(0, sum(1 for r in logs if r["msg"].startswith("Session starting")) - 1),
        "errors": len(errors),
        "names_recalled": len(recalled),
        "names_expected": len(names),
        "sleep_reached": sleeping,
        "memory_writes_blocked": len(blocked["memory"]),
        "mcp_writes_blocked": len(blocked["mcp"]),
        "turns": turns,
        "unprompted_text": unprompted_text,
        "error_samples": [r["msg"][:200] for r in errors[:10]],
        "blocked": blocked,
        "tools_called": [m["content"] for m in msgs if m["role"] == "tool"],
    }


def write_outputs(out: Path, rec: Recorder, logs: list[dict], steps: list[dict], metrics: dict) -> None:
    """Write raw events, logs, a readable transcript and the metrics."""
    with open(out / "events.jsonl", "w") as f:
        for e in rec.events:
            f.write(json.dumps(e) + "\n")
        for s in rec.speech:
            f.write(json.dumps({"t": round(s[0], 2), "kind": "robot_audio", "end": round(s[1], 2)}) + "\n")
    with open(out / "log.txt", "w") as f:
        for r in logs:
            f.write(f"{r['t']:8.2f} {r['level']:7s} {r['name']}: {r['msg']}\n")
    (out / "steps.json").write_text(json.dumps(steps, indent=2))
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2))

    lines = []
    timeline = sorted(
        [(e["t"], e) for e in rec.events if e["kind"] in ("msg", "said", "quiet", "ambient")], key=lambda x: x[0]
    )
    for t, e in timeline:
        if e["kind"] == "said":
            lines.append(f"[{t:6.1f}s] USER says: {e['text']}")
        elif e["kind"] == "quiet":
            lines.append(f"[{t:6.1f}s] (room is quiet for {e['seconds']}s; nobody is talking to the robot)")
        elif e["kind"] == "ambient":
            lines.append(f"[{t:6.1f}s] (background, NOT addressed to the robot: {e['text']})")
        elif e.get("tool_echo"):
            lines.append(f"[{t:6.1f}s] (dashboard shows tool result: {e['content'][:100]})")
        elif e["role"] == "user":
            lines.append(f"[{t:6.1f}s] robot heard: {e['content']}")
        elif e["role"] == "assistant":
            lines.append(f"[{t:6.1f}s] ROBOT: {e['content']}")
        elif e["role"] in ("tool", "search"):
            lines.append(f"[{t:6.1f}s] (robot used {e['role']}: {e['content'][:100]})")
        elif e["role"] == "memory":
            lines.append(f"[{t:6.1f}s] (status: {e['content'][:80]})")
    (out / "transcript.txt").write_text("\n".join(lines) + "\n")


def main() -> None:
    """Parse arguments and run."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--expectations", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--profile-overlay", help="directory of profile files to lay over the robot's profile")
    ap.add_argument("--camera-image", help="JPEG the fake camera returns (default: a gray frame)")
    sys.exit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
