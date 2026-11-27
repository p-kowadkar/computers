"""The Sprint 8 exit run: Maury on N packets, on NOOA and on the in-house base (spec S8 R6).

`select_packets` builds the packets from the local tables. `run_paths` is independent of them, so
a test can run it on saved packets. Valid means the reply parsed as an `Argument` and, after the
validator, at least one claim is left. Replies that needed the validator are counted separately.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from computers.crew import llm as L
from computers.crew.maury import FallbackMaury
from computers.crew.packet import build_packet
from computers.crew.schemas import ContextPacket
from computers.crew.validate import validate

__all__ = ["load_packets", "run_paths", "save_packets", "select_packets", "write_fixtures"]

PATHS = ("nooa", "fallback")


# ------------------------------------------------------------------------- packets


def select_packets(data_dir: str | os.PathLike[str], n: int = 10) -> tuple[list[ContextPacket], ContextPacket | None]:
    """The `n` best-scored selected stars of the last L2 run, and T CrB's 1946 eruption as a reference (or None)."""
    import pyarrow.parquet as pq

    from computers.l1.analyse import analyse_star
    from computers.l1.context import TileContext

    data_dir = Path(data_dir)
    topo = json.loads((data_dir / "reports" / "topology.json").read_text())
    chosen = sorted((c | {"tile": t} for t, v in topo["tiles"].items() for c in v["selected"]), key=lambda c: (-c["score"], c["candidate_id"]))[:n]
    wanted = {c["candidate_id"] for c in chosen}
    ref_pick = ("tcrb", 415593021255513, "outburst", 1946.11)
    ctxs: dict[str, tuple[TileContext, dict[int, dict], dict[str, dict]]] = {}
    out: dict[str, ContextPacket] = {}
    reference = None
    for tile in {c["tile"] for c in chosen} | {ref_pick[0]}:
        ctx = TileContext.from_silver(data_dir, tile)
        stars = {int(r["ref_number"]): r for r in pq.read_table(data_dir / "silver" / "stars" / f"tile={tile}.parquet").to_pylist()}
        cands = {r["candidate_id"]: r for r in pq.read_table(data_dir / "silver" / "candidates" / f"tile={tile}.parquet").to_pylist()}
        ctxs[tile] = (ctx, stars, cands)
    for tile, (ctx, stars, cands) in ctxs.items():
        for cid, row in cands.items():
            is_ref = (tile, int(row["ref_number"]), row["event_type"]) == ref_pick[:3] and abs(row["epoch_start"] - ref_pick[3]) < 0.01
            if cid not in wanted and not is_ref:
                continue
            res = analyse_star(ctx, int(row["ref_number"]))
            trig = next(t for t in res.survivors if t.kind == row["event_type"] and round(t.epoch_start, 2) == round(row["epoch_start"], 2)
                        and round(t.epoch_end, 2) == round(row["epoch_end"], 2))  # fmt: skip
            pk = build_packet(row, stars[int(row["ref_number"])], trig, res.inp)
            if cid in wanted:
                out[cid] = pk
            if is_ref:
                reference = pk
    return [out[c["candidate_id"]] for c in chosen if c["candidate_id"] in out], reference


def save_packets(packets: Sequence[ContextPacket], directory: str | os.PathLike[str]) -> None:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    for p in packets:
        (d / f"{p.candidate_id}.json").write_text(p.model_dump_json(indent=1) + "\n")


def load_packets(directory: str | os.PathLike[str]) -> list[ContextPacket]:
    return [ContextPacket.model_validate_json(f.read_text()) for f in sorted(Path(directory).glob("*.json"))]


# ----------------------------------------------------------------------------- runs


async def _nooa_all(gateway: L.Gateway, packets: Sequence[ContextPacket]) -> list[tuple[Any, str | None]]:
    from computers.crew.base import nooa_client
    from computers.crew.maury_nooa import NooaMaury

    with L.Shim(gateway) as shim:
        client = nooa_client(shim.base_url)
        out: list[tuple[Any, str | None]] = []
        try:
            for pk in packets:
                try:
                    out.append((await NooaMaury(llm=client).run(pk), None))
                except Exception as exc:  # noqa: BLE001  one packet failing must not stop the others; it is recorded in the report
                    out.append((None, f"{type(exc).__name__}: {str(exc)[:160]}"))
        finally:
            await client.aclose()
    return out


async def _fallback_all(gateway: L.Gateway, packets: Sequence[ContextPacket]) -> tuple[list[tuple[Any, str | None]], int]:
    out: list[tuple[Any, str | None]] = []
    retries = 0
    for pk in packets:
        m = FallbackMaury(gateway)
        try:
            out.append((await m.run(pk), None))
        except Exception as exc:  # noqa: BLE001  recorded in the report, as above
            out.append((None, f"{type(exc).__name__}: {str(exc)[:160]}"))
        retries += m.retries_used
    return out, retries


def run_paths(
    packets: Sequence[ContextPacket],
    gateway_for: Callable[[str], L.Gateway],
    *,
    paths: Sequence[str] = PATHS,
) -> dict[str, Any]:
    """Run Maury on every packet along each path and report validity, stripping, tokens and cost per path."""
    report: dict[str, Any] = {"packets": len(packets), "paths": {}}
    for path in paths:
        gw = gateway_for(path)
        retries = 0
        if path == "nooa":
            results = asyncio.run(_nooa_all(gw, packets))
        else:
            results, retries = asyncio.run(_fallback_all(gw, packets))
        rows = []
        for pk, (arg, err) in zip(packets, results, strict=True):
            if arg is None:
                rows.append({"candidate_id": pk.candidate_id, "valid": False, "failure": err})
                continue
            v = validate(pk, arg)
            rows.append({
                "candidate_id": pk.candidate_id, "valid": not v.empty, "needed_stripping": bool(v.removed), "claims_kept": len(v.argument.claims),
                "claims_removed": [{"text": r.claim.text[:120], "reason": r.reason} for r in v.removed],
                "argument": v.argument.model_dump(),
            })  # fmt: skip
        log = [json.loads(x) for x in gw.log_path.read_text().splitlines()] if gw.log_path.exists() else []
        report["paths"][path] = {
            "model": gw.cfg.model, "provider": gw.cfg.provider,
            "valid": sum(r["valid"] for r in rows), "of": len(rows),
            "needed_stripping": sum(bool(r.get("needed_stripping")) for r in rows),
            "claims_removed": sum(len(r.get("claims_removed", [])) for r in rows),
            "failures": [{"candidate_id": r["candidate_id"], "failure": r["failure"]} for r in rows if r.get("failure")],
            "fallback_retries": retries,
            "calls": len(log), "cache_hits": sum(bool(x["hit"]) for x in log),
            "tokens_in": sum(x["tokens_in"] for x in log), "tokens_out": sum(x["tokens_out"] for x in log),
            "cost_usd": round(sum(x["cost_usd"] for x in log), 6), "cap_usd": gw.budget.cap_usd,
            "rows": rows,
        }  # fmt: skip
    return report


def write_fixtures(report_cache_dir: str | os.PathLike[str], logs: Sequence[str | os.PathLike[str]], dest: str | os.PathLike[str]) -> int:
    """Copy the cache entries that the given run logs refer to into a fixtures directory. Returns the number copied."""
    src, dest = Path(report_cache_dir), Path(dest)
    keys = {json.loads(x)["cache_key"] for lg in logs for x in Path(lg).read_text().splitlines() if json.loads(x).get("status") == "ok"}
    n = 0
    for k in sorted(keys):
        f = src / k[:2] / f"{k}.json"
        if f.is_file():
            (dest / k[:2]).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dest / k[:2] / f"{k}.json")
            n += 1
    return n


def format_report(r: dict[str, Any]) -> str:
    lines = [f"Maury on {r['packets']} packets"]
    for name, p in r["paths"].items():
        lines.append(
            f"\n{name}: {p['valid']} of {p['of']} valid ({p['model']} via {p['provider']}). {p['needed_stripping']} replies needed the validator "
            f"({p['claims_removed']} claims removed). Fallback retries {p['fallback_retries']}. Calls {p['calls']} ({p['cache_hits']} cache hits), "
            f"tokens {p['tokens_in']} in and {p['tokens_out']} out, cost ${p['cost_usd']:.4f} of a ${p['cap_usd']:.2f} cap."
        )
        lines += [f"  failed {f['candidate_id']}: {f['failure']}" for f in p["failures"]]
    return "\n".join(lines)


__all__ += ["PATHS", "format_report"]
