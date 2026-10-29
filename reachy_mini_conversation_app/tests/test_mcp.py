"""Tests for MCP tool adapter and registry integration."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from reachy_mini_gemini_live.tools.core_tools import ALL_TOOLS, ALL_TOOL_SPECS
from reachy_mini_gemini_live.tools.mcp_client import MCPToolSpec, MCPClientPool
from reachy_mini_gemini_live.tools.mcp_adapter import (
    MCPToolAdapter,
    register_mcp_tools,
    deregister_mcp_tools,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def fake_pool() -> MCPClientPool:
    pool = MCPClientPool([])
    return pool


@pytest.fixture()
def fake_spec(fake_pool: MCPClientPool) -> MCPToolSpec:
    return MCPToolSpec(
        server_name="test_server",
        tool_name="read_file",
        description="Read a file",
        parameters_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )


@pytest.fixture(autouse=True)
def cleanup_registry(fake_pool: MCPClientPool):
    """Remove any MCP adapters added during tests."""
    yield
    deregister_mcp_tools(fake_pool)


# ── MCPToolAdapter ────────────────────────────────────────────────────────────


def test_adapter_name_includes_server_and_tool(fake_spec: MCPToolSpec, fake_pool: MCPClientPool) -> None:
    adapter = MCPToolAdapter(fake_spec, fake_pool)
    assert adapter.name == "test_server__read_file"


def test_adapter_description_includes_server(fake_spec: MCPToolSpec, fake_pool: MCPClientPool) -> None:
    adapter = MCPToolAdapter(fake_spec, fake_pool)
    assert "test_server" in adapter.description
    assert "Read a file" in adapter.description


def test_adapter_spec_is_valid(fake_spec: MCPToolSpec, fake_pool: MCPClientPool) -> None:
    adapter = MCPToolAdapter(fake_spec, fake_pool)
    spec = adapter.spec()
    assert spec["name"] == "test_server__read_file"
    assert "description" in spec
    assert "parameters" in spec


@pytest.mark.asyncio
async def test_adapter_call_delegates_to_pool(fake_spec: MCPToolSpec, fake_pool: MCPClientPool) -> None:
    fake_pool.call_tool = AsyncMock(return_value="file contents here")
    adapter = MCPToolAdapter(fake_spec, fake_pool)
    deps = MagicMock()
    result = await adapter(deps, path="/tmp/test.txt")
    fake_pool.call_tool.assert_awaited_once_with("test_server", "read_file", {"path": "/tmp/test.txt"})
    assert result["result"] == "file contents here"


# ── register / deregister ─────────────────────────────────────────────────────


def test_register_adds_to_all_tools(fake_spec: MCPToolSpec, fake_pool: MCPClientPool) -> None:
    initial_count = len(ALL_TOOLS)
    added = register_mcp_tools(fake_pool, [fake_spec])
    assert added == 1
    assert "test_server__read_file" in ALL_TOOLS
    assert len(ALL_TOOLS) == initial_count + 1


def test_register_adds_spec_to_all_tool_specs(fake_spec: MCPToolSpec, fake_pool: MCPClientPool) -> None:
    initial_count = len(ALL_TOOL_SPECS)
    register_mcp_tools(fake_pool, [fake_spec])
    assert len(ALL_TOOL_SPECS) == initial_count + 1
    names = [s["name"] for s in ALL_TOOL_SPECS]
    assert "test_server__read_file" in names


def test_register_skips_duplicate(fake_spec: MCPToolSpec, fake_pool: MCPClientPool) -> None:
    register_mcp_tools(fake_pool, [fake_spec])
    added_second = register_mcp_tools(fake_pool, [fake_spec])
    assert added_second == 0


def test_deregister_removes_from_registry(fake_spec: MCPToolSpec, fake_pool: MCPClientPool) -> None:
    register_mcp_tools(fake_pool, [fake_spec])
    assert "test_server__read_file" in ALL_TOOLS
    deregister_mcp_tools(fake_pool)
    assert "test_server__read_file" not in ALL_TOOLS


# ── MCPClientPool.from_profile ────────────────────────────────────────────────


def test_from_profile_empty_when_no_file(tmp_path: Path) -> None:
    (tmp_path / "myprofile").mkdir()
    pool = MCPClientPool.from_profile("myprofile", tmp_path)
    assert pool.is_empty()


def test_from_profile_loads_stdio_config(tmp_path: Path) -> None:
    profile_dir = tmp_path / "myprofile"
    profile_dir.mkdir()
    (profile_dir / "mcp_servers.json").write_text(
        json.dumps([{"name": "fs", "transport": "stdio", "command": ["npx", "server-fs", "/tmp"]}])
    )
    pool = MCPClientPool.from_profile("myprofile", tmp_path)
    assert not pool.is_empty()
    assert "fs" in pool._conns


def test_from_profile_loads_sse_config(tmp_path: Path) -> None:
    profile_dir = tmp_path / "myprofile"
    profile_dir.mkdir()
    (profile_dir / "mcp_servers.json").write_text(
        json.dumps([{"name": "remote", "transport": "sse", "url": "http://localhost:8765"}])
    )
    pool = MCPClientPool.from_profile("myprofile", tmp_path)
    assert "remote" in pool._conns


def test_from_profile_handles_invalid_json_gracefully(tmp_path: Path) -> None:
    profile_dir = tmp_path / "myprofile"
    profile_dir.mkdir()
    (profile_dir / "mcp_servers.json").write_text("NOT JSON")
    pool = MCPClientPool.from_profile("myprofile", tmp_path)
    assert pool.is_empty()
