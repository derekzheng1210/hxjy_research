"""Verify that the MCP server starts and exposes its tools without querying Oracle."""

from __future__ import annotations

import asyncio
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    plugin_root = Path(__file__).resolve().parents[1]
    python = plugin_root.parents[1] / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise RuntimeError(f"Project virtual environment not found: {python}")
    params = StdioServerParameters(
        command=str(python),
        args=["./server.py"],
        cwd=str(plugin_root),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
    names = {tool.name for tool in tools.tools}
    expected = {"get_latest_market_dates", "get_yield_curve", "get_cnbd_valuations"}
    if names != expected:
        raise RuntimeError(f"Unexpected MCP tools: {sorted(names)}")
    print("MCP smoke test passed: " + ", ".join(sorted(names)))


if __name__ == "__main__":
    asyncio.run(main())
