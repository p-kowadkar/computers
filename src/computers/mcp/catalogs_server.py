"""The `catalogs` MCP server: the local VSX table (plan 8.5, spec S8 R5).

Run: `uv run --group mcp python -m computers.mcp.catalogs_server` (stdio). SIMBAD, Gaia and ZTF have no tool until their data is pulled.
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from computers.mcp._cache import Store

__all__ = ["build_server", "main"]


def build_server(store: Store | None = None) -> FastMCP:
    store = store or Store()
    mcp = FastMCP("catalogs")

    @mcp.tool()
    def vsx_search(ra: float, dec: float, radius_arcsec: float = 10.0, tile: str | None = None) -> dict[str, Any]:
        """Known variables (AAVSO VSX) within `radius_arcsec` of a position (degrees), nearest first, at most 50."""
        return store.vsx(ra, dec, radius_arcsec, tile)

    return mcp


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
