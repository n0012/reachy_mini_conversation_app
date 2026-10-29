"""MCPToolAdapter — wraps a remote MCP tool as a first-class Tool.

Also provides register_mcp_tools() to inject adapters into the global registry
after the static tools are initialized.
"""

from __future__ import annotations
import logging
from typing import Any, Dict

from reachy_mini_gemini_live.tools.core_tools import ALL_TOOLS, ALL_TOOL_SPECS, Tool, ToolDependencies
from reachy_mini_gemini_live.tools.mcp_client import MCPToolSpec, MCPClientPool


logger = logging.getLogger(__name__)


class MCPToolAdapter(Tool):
    """Wraps one MCP tool as a Tool for the existing dispatch system."""

    def __init__(self, spec: MCPToolSpec, pool: MCPClientPool) -> None:
        """Wrap an MCP tool spec as a local Tool."""
        self.name = f"{spec.server_name}__{spec.tool_name}"
        self.description = f"[MCP:{spec.server_name}] {spec.description}"
        self.parameters_schema: Dict[str, Any] = spec.parameters_schema or {
            "type": "object",
            "properties": {},
            "required": [],
        }
        self._server_name = spec.server_name
        self._tool_name = spec.tool_name
        self._pool = pool

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Forward the call to the MCP server."""
        result = await self._pool.call_tool(self._server_name, self._tool_name, kwargs)
        return {"result": result}


def register_mcp_tools(pool: MCPClientPool, specs: list[MCPToolSpec]) -> int:
    """Inject MCP adapters into the global tool registry. Returns count added."""
    added = 0
    for spec in specs:
        adapter = MCPToolAdapter(spec, pool)
        if adapter.name in ALL_TOOLS:
            logger.debug("MCP tool %r already registered — skipping", adapter.name)
            continue
        ALL_TOOLS[adapter.name] = adapter
        ALL_TOOL_SPECS.append(adapter.spec())
        logger.info("✓ Registered MCP tool: %s", adapter.name)
        added += 1
    return added


def deregister_mcp_tools(pool: MCPClientPool) -> None:
    """Remove all adapters from this pool from the global registry on session close."""
    to_remove = [name for name, tool in ALL_TOOLS.items() if isinstance(tool, MCPToolAdapter) and tool._pool is pool]
    for name in to_remove:
        ALL_TOOLS.pop(name, None)
    ALL_TOOL_SPECS[:] = [s for s in ALL_TOOL_SPECS if s.get("name") not in to_remove]
    if to_remove:
        logger.info("Deregistered %d MCP tool(s)", len(to_remove))
