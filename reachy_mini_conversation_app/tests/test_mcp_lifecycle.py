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
