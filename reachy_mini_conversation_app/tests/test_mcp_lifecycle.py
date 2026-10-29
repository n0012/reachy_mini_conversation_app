"""Real stdio MCP server: connect, call and close from different tasks without leaks."""

import sys
import asyncio
import logging
from pathlib import Path

import pytest

from reachy_mini_gemini_live.tools.mcp_client import MCPClientPool, _ServerConfig


SERVER = """
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("echo")

@mcp.tool()
def echo(text: str) -> str:
    \"\"\"Echo text back.\"\"\"
    return "echo:" + text

mcp.run()
"""


@pytest.fixture()
def pool(tmp_path: Path) -> MCPClientPool:
    script = tmp_path / "echo_server.py"
    script.write_text(SERVER)
    return MCPClientPool([_ServerConfig(name="echo", transport="stdio", command=[sys.executable, str(script)])])


def _children() -> set[int]:
    import psutil

    return {p.pid for p in psutil.Process().children(recursive=True)}


@pytest.mark.asyncio
async def test_connect_call_close_across_tasks(pool: MCPClientPool, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    before = _children()

    # Connect inside a gather sub-task, exactly as session start does.
    (specs,) = await asyncio.gather(asyncio.create_task(pool.connect_all()))
    assert [s.tool_name for s in specs] == ["echo"]
    assert _children() - before, "server subprocess should be running"

    # Call from another task.
    assert await asyncio.create_task(pool.call_tool("echo", "echo", {"text": "hi"})) == "echo:hi"

    # Reconnecting a live pool reuses the connection (no second subprocess).
    running = _children() - before
    assert [s.tool_name for s in await pool.connect_all()] == ["echo"]
    assert _children() - before == running

    # Close from yet another task.
    await asyncio.create_task(pool.close())
    for _ in range(50):
        if not (_children() - before):
            break
        await asyncio.sleep(0.1)
    assert not (_children() - before), "server subprocess should exit on close"
    assert "cancel scope" not in caplog.text
    assert "connection ended" not in caplog.text


@pytest.mark.asyncio
async def test_failed_server_reports_no_tools(tmp_path: Path) -> None:
    pool = MCPClientPool(
        [_ServerConfig(name="bad", transport="stdio", command=[sys.executable, "-c", "raise SystemExit(1)"])]
    )
    assert await pool.connect_all() == []
    assert "not connected" in await pool.call_tool("bad", "x", {})
    await pool.close()


@pytest.mark.asyncio
async def test_handler_reuses_pool_across_sessions_and_closes_on_profile_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    from unittest.mock import MagicMock

    from reachy_mini_gemini_live import gemini_live
    from reachy_mini_gemini_live.gemini_live import GeminiLiveHandler
    from reachy_mini_gemini_live.tools.core_tools import ToolDependencies

    script = tmp_path / "echo_server.py"
    script.write_text(SERVER)
    for profile in ("yuki", "other"):
        (tmp_path / profile).mkdir()
        (tmp_path / profile / "mcp_servers.json").write_text(
            json.dumps([{"name": "echo", "transport": "stdio", "command": [sys.executable, str(script)]}])
        )
    monkeypatch.setattr(gemini_live.config, "PROFILES_DIRECTORY", tmp_path)
    handler = GeminiLiveHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    before = _children()

    # Two sessions on the same profile share one pool and one server process.
    first = await handler._mcp_pool_for("yuki")
    # Use the handler's own references: other tests reload the tool modules.
    gemini_live.register_mcp_tools(first, await first.connect_all())
    registry = gemini_live.deregister_mcp_tools.__globals__["ALL_TOOLS"]
    second = await handler._mcp_pool_for("yuki")
    await second.connect_all()
    assert second is first
    assert len(_children() - before) == 1

    # Switching profile closes the old pool and drops its tools from the registry.
    third = await handler._mcp_pool_for("other")
    assert third is not first
    assert not any(getattr(t, "_pool", None) is first for t in registry.values())
    for _ in range(50):
        if not (_children() - before):
            break
        await asyncio.sleep(0.1)
    assert not (_children() - before), "old profile's server should exit"
    await third.close()
