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

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


@dataclass
class MCPToolSpec:
    server_name: str
    tool_name: str
    description: str
    parameters_schema: dict[str, Any]


@dataclass
class _ServerConfig:
    name: str
    transport: str                    # "stdio" | "sse"
    command: list[str] = field(default_factory=list)   # stdio only
    url: str = ""                     # sse only


class MCPServerConnection:
    """Single live MCP server connection."""

    def __init__(self, cfg: _ServerConfig) -> None:
        self._cfg = cfg
        self._session: Any = None
        self._cm: Any = None          # context manager keeping the connection alive
        self._tools: list[MCPToolSpec] = []

    async def connect(self) -> list[MCPToolSpec]:
        """Connect and return available tools. Idempotent."""
        if self._session is not None:
            return self._tools

        try:
            if self._cfg.transport == "stdio":
                self._tools = await self._connect_stdio()
            elif self._cfg.transport == "sse":
                self._tools = await self._connect_sse()
            else:
                logger.error("Unknown MCP transport: %s", self._cfg.transport)
        except Exception as exc:
            logger.warning("MCP server %r connect failed: %s", self._cfg.name, exc)

        return self._tools

    async def _connect_stdio(self) -> list[MCPToolSpec]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=self._cfg.command[0],
            args=self._cfg.command[1:],
        )
        self._cm = stdio_client(params)
        read, write = await self._cm.__aenter__()
        session = ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()
        self._session = session
        return await self._list_tools()

    async def _connect_sse(self) -> list[MCPToolSpec]:
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        self._cm = sse_client(self._cfg.url)
        read, write = await self._cm.__aenter__()
        session = ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()
        self._session = session
        return await self._list_tools()

    async def _list_tools(self) -> list[MCPToolSpec]:
        result = await self._session.list_tools()
        specs = []
        for t in result.tools:
            specs.append(MCPToolSpec(
                server_name=self._cfg.name,
                tool_name=t.name,
                description=t.description or "",
                parameters_schema=t.inputSchema if isinstance(t.inputSchema, dict) else {},
            ))
        logger.info("MCP %r: loaded %d tools", self._cfg.name, len(specs))
        return specs

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
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
        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
            self._session = None
        if self._cm:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._cm = None


class MCPClientPool:
    """Manages all MCP server connections for a profile.

    Usage:
        pool = MCPClientPool.from_profile(profile, profiles_root)
        tools = await pool.connect_all()   # list[MCPToolSpec]
        result = await pool.call_tool(server_name, tool_name, args)
        await pool.close()
    """

    def __init__(self, configs: list[_ServerConfig]) -> None:
        self._conns: dict[str, MCPServerConnection] = {
            cfg.name: MCPServerConnection(cfg) for cfg in configs
        }

    @classmethod
    def from_profile(cls, profile: str, profiles_root: Path) -> "MCPClientPool":
        """Load mcp_servers.json for a profile. Returns empty pool if file absent."""
        cfg_file = profiles_root / profile / "mcp_servers.json"
        if not cfg_file.exists():
            return cls([])
        try:
            raw: list[dict[str, Any]] = json.loads(cfg_file.read_text())
            configs = [_ServerConfig(**{k: v for k, v in entry.items()}) for entry in raw]
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
        conn = self._conns.get(server_name)
        if conn is None:
            return f"Unknown MCP server: {server_name!r}"
        return await conn.call_tool(tool_name, args)

    async def close(self) -> None:
        await asyncio.gather(*[c.close() for c in self._conns.values()], return_exceptions=True)

    def is_empty(self) -> bool:
        return not self._conns
