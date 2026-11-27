"""The `dasch` MCP server: bounded, read-only access to the local DASCH tables (plan 8.5, spec S8 R5).

Run: `uv run --group mcp python -m computers.mcp.dasch_server` (stdio). No tool touches the network.
Tools exist only for data that is pulled: cutouts and plate photos have no tool yet.
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from computers.mcp._cache import Store

__all__ = ["build_server", "main"]


def build_server(store: Store | None = None) -> FastMCP:
    store = store or Store()
    mcp = FastMCP("dasch")

    @mcp.tool()
    def get_lightcurve(
        tile: str, ref_number: int, start_year: float | None = None, end_year: float | None = None,
        series: str | None = None, limit: int = 200,
    ) -> dict[str, Any]:  # fmt: skip
        """Measurements of one star (DASCH `ref_number`) in one tile, in time order, as measured (not detrended).

        At most `limit` rows (maximum 500) come back, thinned evenly when more match; the reply says how many matched.
        """
        return store.lightcurve(tile, ref_number, start_year, end_year, series, limit)

    @mcp.tool()
    def list_exposures(
        tile: str, start_year: float | None = None, end_year: float | None = None, series: str | None = None, limit: int = 200
    ) -> dict[str, Any]:
        """Plate exposures covering a tile, optionally within a year range or one series. Bounded like `get_lightcurve`."""
        return store.exposures(tile, start_year, end_year, series, limit)

    @mcp.tool()
    def get_plate_info(exposure_id: str) -> dict[str, Any]:
        """One exposure record: series, plate number, date, plate class, limiting magnitude and colour term."""
        return store.plate_info(exposure_id)

    return mcp


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
