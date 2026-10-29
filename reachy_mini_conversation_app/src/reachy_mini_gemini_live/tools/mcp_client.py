"""MCP client pool — manages connections to MCP servers for a profile.

Reads per-profile mcp_servers.json, connects on demand, auto-reconnects.
Supports stdio and SSE transports.

mcp_servers.json format:
[
  {
    "name": "filesystem",
    "transport": "stdio",
    "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
  },
  {
    "name": "my-server",
    "transport": "sse",
    "url": "http://localhost:8765/sse"
  }
]
"""

from __future__ import annotations
import os
import json
import asyncio
import logging
from typing import Any
from pathlib import Path
from dataclasses import field, dataclass


logger = logging.getLogger(__name__)


@dataclass
class MCPToolSpec:
    """A tool advertised by an MCP server."""

    server_name: str
    tool_name: str
    description: str
    parameters_schema: dict[str, Any]


@dataclass
class _ServerConfig:
    name: str
    transport: str  # "stdio" | "sse" | "streamablehttp"
    command: list[str] = field(default_factory=list)  # stdio only
    url: str = ""  # sse / streamablehttp
    headers: dict = field(default_factory=dict)  # optional auth headers


class MCPServerConnection:
    """Single live MCP server connection.

    The MCP transports use anyio cancel scopes, which must be entered and exited in
    the same task. Each connection therefore lives in one owner task that opens the
    transport, holds it until close() is requested, then exits it — instead of
    entering in whichever task called connect() and exiting in another.
    """

    CONNECT_TIMEOUT_S = 20.0
    CLOSE_TIMEOUT_S = 5.0

    def __init__(self, cfg: _ServerConfig) -> None:
        """Hold the config; connect lazily."""
        self._cfg = cfg
        self._session: Any = None
        self._tools: list[MCPToolSpec] = []
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()

    async def connect(self) -> list[MCPToolSpec]:
        """Connect and return available tools. Idempotent."""
        if self._session is not None:
            return self._tools
        if self._task is None or self._task.done():
            self._ready = asyncio.Event()
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name=f"mcp-{self._cfg.name}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self.CONNECT_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning("MCP server %r connect timed out after %.0fs", self._cfg.name, self.CONNECT_TIMEOUT_S)
            await self.close()
        return self._tools

    def _transport(self) -> Any:
        """Return the transport's async context manager for this server."""
        if self._cfg.transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=self._cfg.command[0],
                args=self._cfg.command[1:],
                env=os.environ.copy(),  # explicitly pass env so tokens are available in subprocess
            )
            return stdio_client(params)
        if self._cfg.transport == "sse":
            from mcp.client.sse import sse_client

            return sse_client(self._cfg.url, headers=self._cfg.headers or {})
        if self._cfg.transport == "streamablehttp":
            from mcp.client.streamable_http import streamablehttp_client

            return streamablehttp_client(self._cfg.url, headers=self._cfg.headers or {})
        raise ValueError(f"Unknown MCP transport: {self._cfg.transport}")

    async def _run(self) -> None:
        """Own the connection: open it, publish the session, hold until stopped."""
        from mcp import ClientSession

        try:
            async with self._transport() as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    self._tools = await self._list_tools()
                    self._ready.set()
                    await self._stop.wait()
        except Exception as exc:
            logger.warning("MCP server %r connection ended: %s", self._cfg.name, exc)
        finally:
            self._session = None
            self._ready.set()

    async def _list_tools(self) -> list[MCPToolSpec]:
        result = await self._session.list_tools()
        specs = []
        for t in result.tools:
            specs.append(
                MCPToolSpec(
                    server_name=self._cfg.name,
                    tool_name=t.name,
                    description=t.description or "",
                    parameters_schema=t.inputSchema if isinstance(t.inputSchema, dict) else {},
                )
            )
        logger.info("MCP %r: loaded %d tools", self._cfg.name, len(specs))
        return specs

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on this server, connecting first if needed."""
        if self._session is None:
            await self.connect()
        if self._session is None:
            return f"MCP server {self._cfg.name!r} not connected"
        try:
            result = await self._session.call_tool(tool_name, arguments)
            parts = []
            for content in result.content:
                if hasattr(content, "text"):
                    parts.append(content.text)
            return "\n".join(parts) if parts else "(no output)"
        except Exception as exc:
            return f"MCP tool error: {exc}"

    async def close(self) -> None:
        """Ask the owner task to exit the session and transport, and wait for it."""
        task, self._task = self._task, None
        if task is None or task.done():
            return
        self._stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=self.CLOSE_TIMEOUT_S)
        except asyncio.TimeoutError:
            task.cancel()
        except asyncio.CancelledError:
            task.cancel()
            raise
        except Exception:
            pass


class MCPClientPool:
    """Manages all MCP server connections for a profile.

    Usage:
        pool = MCPClientPool.from_profile(profile, profiles_root)
        tools = await pool.connect_all()   # list[MCPToolSpec]
        result = await pool.call_tool(server_name, tool_name, args)
        await pool.close()
    """

    def __init__(self, configs: list[_ServerConfig]) -> None:
        """Create one connection per configured server."""
        self._conns: dict[str, MCPServerConnection] = {cfg.name: MCPServerConnection(cfg) for cfg in configs}

    @classmethod
    def from_profile(cls, profile: str, profiles_root: Path) -> "MCPClientPool":
        """Load mcp_servers.json for a profile. Returns empty pool if file absent."""
        cfg_file = profiles_root / profile / "mcp_servers.json"
        if not cfg_file.exists():
            return cls([])
        try:
            import re as _re

            def _expand(val: Any) -> Any:
                if isinstance(val, str):
                    return _re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), m.group(0)), val)
                if isinstance(val, dict):
                    return {k: _expand(v) for k, v in val.items()}
                if isinstance(val, list):
                    return [_expand(v) for v in val]
                return val

            raw: list[dict[str, Any]] = json.loads(cfg_file.read_text())
            configs = [_ServerConfig(**{k: _expand(v) for k, v in entry.items()}) for entry in raw]
            logger.info("MCP: loaded %d server(s) for profile %r", len(configs), profile)
            return cls(configs)
        except Exception as exc:
            logger.warning("MCP config load failed for %r: %s", profile, exc)
            return cls([])

    async def connect_all(self) -> list[MCPToolSpec]:
        """Connect to all servers concurrently and return all tool specs."""
        if not self._conns:
            return []
        results = await asyncio.gather(
            *[conn.connect() for conn in self._conns.values()],
            return_exceptions=True,
        )
        tools: list[MCPToolSpec] = []
        for res in results:
            if isinstance(res, list):
                tools.extend(res)
        return tools

    async def call_tool(self, server_name: str, tool_name: str, args: dict[str, Any]) -> str:
        """Call a tool on the named server."""
        conn = self._conns.get(server_name)
        if conn is None:
            return f"Unknown MCP server: {server_name!r}"
        return await conn.call_tool(tool_name, args)

    async def close(self) -> None:
        """Close every server connection."""
        await asyncio.gather(*[c.close() for c in self._conns.values()], return_exceptions=True)

    def is_empty(self) -> bool:
        """Return True when no servers are configured."""
        return not self._conns
