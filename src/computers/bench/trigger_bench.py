"""What the L1 triggers recover and what they raise falsely, on the dev split (spec R10 to R12).

1. False alarms per 1,000 constants at the L1 exit, per trigger type, with what each kill
   reason removed.
2. Completeness per family, by any matching trigger and by a trigger of the right type, and
   the confusion matrix.
3. Sweeps of the starting thresholds, one at a time, on one replicate of the injections.
4. Ablations: no detrending, no seam rule, the other meteor policy.
5. The known variables of the dev tiles.

The test tile is not touched. Nothing here changes a threshold: it shows what each choice
costs, and you choose.
"""

from __future__ import annotations

import os
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from computers.bench import leavitt as L
from computers.bench import metrics as M
from computers.bench import recovery as R
from computers.bench.harness import HostCurve
from computers.bench.inject import SignalSpec
from computers.bench.l1_adapter import l1_hosts
from computers.bench.run import specs_from_rows
from computers.data import silver
from computers.l1.analyse import analyse_star
from computers.l1.context import TileContext
from computers.l1.triggers import KINDS, Trigger, TriggerParams

__all__ = ["SWEEPS", "Dev", "format_trigger_report", "load_dev", "run_trigger_report"]

DEV_TILES = L.DEV_TILES

# One parameter at a time around the starting values. "series" sets the minimum number of series
# for that trigger kind only (the per-kind override). The families are the injections that kind
# is meant to recover.
SWEEPS: dict[str, dict[str, Any]] = {
    "outburst": {
        "families": ("outburst",),
        "grid": [
            ("outburst_amplitude_mag", [1.0, 1.5, 2.0]),
            ("outburst_min_points", [2, 3]),
            ("series", [1, 2]),
            ("outburst_sigma", [5.0, 8.0]),
            ("outburst_single_point_mag", [2.0, 3.0]),
            ("return_points", [0, 3, 5]),
            ("return_recovery", [0.1, 0.25, 0.5]),
        ],
    },
    "dip": {
        "families": ("dip",),
        "grid": [
            ("dip_min_points", [3, 4, 5]),
            ("dip_amplitude_mag", [0.5, 0.7, 1.0]),
            ("series", [1, 2]),
            ("return_points", [0, 3, 5]),
            ("return_recovery", [0.1, 0.25, 0.5]),
        ],
    },
    "step": {
        "families": ("step",),
        "grid": [("step_shift_mag", [0.5, 0.7, 1.0]), ("step_min_points", [20, 30])],
    },
    "long_term": {
        "families": ("linear", "wave"),
        "grid": [("long_term_span_mag", [0.5, 0.7, 1.0]), ("long_term_min_decades", [4, 5])],
    },
}


@dataclass
class Dev:
    """The dev split loaded for a meteor policy: contexts, host curves and the planned injections."""

    policy: str
    contexts: dict[int, TileContext]  # host id -> its tile's context
    hosts: dict[int, HostCurve]
    rows: list[dict]  # dev injection rows

    def specs(self, families: tuple[str, ...] | None = None, replicate: int | None = None) -> list[SignalSpec]:
        rows = [r for r in self.rows if (families is None or r["family"] in families)
                and (replicate is None or r["replicate"] == replicate)]  # fmt: skip
        return specs_from_rows(rows)


def load_dev(data_dir: str | os.PathLike[str], policy: str = "reject") -> Dev:
    data_dir = Path(data_dir)
    out = L.set_dir(data_dir)
    cons = [r for r in pq.read_table(out / "constants.parquet").to_pylist() if r["split"] == "dev" and r["is_constant"]]
    rows = [r for r in pq.read_table(out / "injections.parquet").to_pylist() if r["split"] == "dev"]
    contexts: dict[int, TileContext] = {}
    hosts: dict[int, HostCurve] = {}
    for tile in DEV_TILES:
        refs = [r["ref_number"] for r in cons if r["tile"] == tile]
        ctx = TileContext.from_silver(data_dir, tile, meteor_policy=policy)
        h = l1_hosts(ctx, refs)
        hosts.update(h)
        contexts.update(dict.fromkeys(h, ctx))
    return Dev(policy, contexts, hosts, rows)


# ---------------------------------------------------------------- false alarms


def false_alarm_table(base: dict[int, tuple[Trigger, ...]]) -> dict[str, Any]:
    """Per trigger kind (and any): constants with a trigger, by what killed it, and constants with a survivor."""
    n = len(base)
    out: dict[str, Any] = {"hosts": n}
    for kind in (*KINDS, "any"):
        pick = [[t for t in ts if kind in ("any", t.kind)] for ts in base.values()]
        with_any = sum(bool(ts) for ts in pick)
        survivors = sum(any(t.survives for t in ts) for ts in pick)
        reasons = Counter(r for ts in pick for r in {r for t in ts for r in t.kill_reasons if r != "known_variable"})
        rate = M.false_alarm_rate(survivors, n)
        out[kind] = {
            "found": with_any, "survive": survivors, "per_1000": rate.per_1000,
            "low": rate.low_per_1000, "high": rate.high_per_1000, "killed_by": dict(sorted(reasons.items())),
        }  # fmt: skip
    singles = [t for ts in base.values() for t in ts if t.kind == "outburst" and t.single_plate]
    out["single_plate_outbursts"] = {"found": len(singles), "survive": sum(t.survives for t in singles)}
    return out


# ------------------------------------------------------------------ completeness


def completeness_tables(outcomes: list[M.Outcome]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, field in (("any", "recovered"), ("right_type", "right_type")):
        bins = M.completeness(outcomes, field=field)
        out[name] = {"bins": [b.__dict__ for b in bins], "crossings": [c.__dict__ for c in M.crossings(bins)]}
    out["suspect"] = M.suspect_recoveries(outcomes)
    out["suspect_kinds"] = dict(Counter(o.match_kind for o in outcomes if o.expected_missed and o.recovered))
    return out


def _share(outcomes: list[M.Outcome], field: str = "recovered") -> tuple[int, int]:
    use = [o for o in outcomes if not o.expected_missed]
    return sum(bool(getattr(o, field)) for o in use), len(use)


# ------------------------------------------------------------------------ sweeps


def _params(base: TriggerParams, kind: str, name: str, value) -> TriggerParams:
    if name == "series":
        return base.but(min_series_overrides=((kind, int(value)),))
    return base.but(**{name: type(getattr(base, name))(value)})


def sweep(dev: Dev, say: Callable[[str], None] = lambda _: None) -> dict[str, Any]:
    base = TriggerParams.from_registry()
    out: dict[str, Any] = {}
    for kind, cfg in SWEEPS.items():
        specs = dev.specs(cfg["families"], replicate=0)
        rows = []
        for name, values in cfg["grid"]:
            for v in values:
                p = _params(base, kind, name, v)
                run = R.run_recovery(dev.contexts, dev.hosts, specs, p)
                fa = false_alarm_table(R.host_triggers(dev.contexts, dev.hosts, p))[kind]
                k, n = _share(run.outcomes)
                kr, _ = _share(run.outcomes, "right_type")
                top = [o for o in run.outcomes if not o.expected_missed and o.amplitude_index == max(x.amplitude_index for x in run.outcomes)]
                rows.append({
                    "parameter": name, "value": v, "false_alarms": fa["survive"], "false_alarms_per_1000": fa["per_1000"],
                    "recovered": k, "right_type": kr, "n": n,
                    "recovered_at_top_amplitude": sum(o.recovered for o in top), "n_top": len(top),
                })  # fmt: skip
        out[kind] = rows
        say(f"   sweep {kind}: {len(rows)} settings")
    return out


# ------------------------------------------------------------------- the report


def known_variables(data_dir: Path, policy: str, tiles=DEV_TILES) -> list[dict]:
    """Which triggers fire on the known variables and reference stars of the dev tiles."""
    out = []
    for tile in tiles:
        ctx = TileContext.from_silver(data_dir, tile, meteor_policy=policy)
        vsx = silver.read_table(silver.silver_path(data_dir, "vsx_match", tile))
        sep = dict(zip(vsx.column("ref_number").to_pylist(), vsx.column("sep_arcsec").to_pylist(), strict=True))
        known = [r for r in pq.read_table(L.set_dir(data_dir) / "variables.parquet").to_pylist() if r["tile"] == tile]
        for v in known:
            r = analyse_star(ctx, v["ref_number"], vsx_sep_arcsec=sep.get(v["ref_number"]))
            kinds = Counter((t.kind, t.survives) for t in r.triggers)
            out.append({
                "tile": tile, "ref_number": v["ref_number"], "name": v["vsx_name"], "type": v["vsx_type"],
                "stop_reason": r.stop_reason, "detrended": bool(r.detrended and r.detrended.detrended),
                "triggers": {f"{k}{'' if s else ' (killed)'}": n for (k, s), n in sorted(kinds.items())},
            })  # fmt: skip
    return out


def run_trigger_report(
    data_dir: str | os.PathLike[str], *, sweeps: bool = True, ablations: bool = True, say: Callable[[str], None] = print
) -> dict[str, Any]:
    data_dir = Path(data_dir)
    t0 = time.time()
    params = TriggerParams.from_registry()
    dev = load_dev(data_dir, "reject")
    specs = dev.specs()
    result: dict[str, Any] = {"hosts": len(dev.hosts), "injections": len(specs), "parameters": params.__dict__}
    run = R.run_recovery(dev.contexts, dev.hosts, specs, params)
    base = R.host_triggers(dev.contexts, dev.hosts, params)
    result["default"] = {
        "false_alarms": false_alarm_table(base),
        "completeness": completeness_tables(run.outcomes),
        "confusion": R.confusion(run.confusion),
    }
    say(f"   default run done ({time.time() - t0:.0f} s)")
    if ablations:
        result["ablations"] = {}
        configs = {
            "no_detrending": (params, {"detrend": False}),
            "no_seam_rule": (params, {"ignore_reasons": ("seam_only",)}),
            "no_return_check": (params.but(return_points=0), {}),
        }
        for name, (p, kw) in configs.items():
            r = R.run_recovery(dev.contexts, dev.hosts, specs, p, **kw)
            b = R.host_triggers(dev.contexts, dev.hosts, p, **kw)
            result["ablations"][name] = {"false_alarms": false_alarm_table(b), "completeness": completeness_tables(r.outcomes)}
            say(f"   ablation {name} done ({time.time() - t0:.0f} s)")
        keep = load_dev(data_dir, "keep")
        r = R.run_recovery(keep.contexts, keep.hosts, keep.specs(), params)
        b = R.host_triggers(keep.contexts, keep.hosts, params)
        result["ablations"]["meteor_keep"] = {"false_alarms": false_alarm_table(b), "completeness": completeness_tables(r.outcomes)}
        say(f"   ablation meteor_keep done ({time.time() - t0:.0f} s)")
    if sweeps:
        result["sweeps"] = sweep(dev, say)
    result["known_variables"] = known_variables(data_dir, "reject")
    result["seconds"] = round(time.time() - t0, 1)
    return result


def _fmt_cross(c: dict) -> str:
    if c["estimate"] is None:
        return "-"
    return f"{'<=' if c['at_or_below'] else ''}{c['estimate']:.2f}"


def _cell(d: dict, view: str, family: str) -> str:
    cs = {c["level"]: c for c in d["completeness"][view]["crossings"] if c["family"] == family}
    return f"{_fmt_cross(cs[0.5])} / {_fmt_cross(cs[0.9])}"


def format_trigger_report(r: dict[str, Any]) -> str:
    d = r["default"]
    lines = [f"L1 triggers on the dev split: {r['hosts']} constants, {r['injections']} injections, {r['seconds']} s", ""]
    fa = d["false_alarms"]
    lines.append("1. False alarms at the L1 exit (constants with a surviving trigger; per 1,000 with 95% interval)")
    for kind in (*KINDS, "any"):
        x = fa[kind]
        killed = ", ".join(f"{k} {v}" for k, v in x["killed_by"].items()) or "none"
        lines.append(f"   {kind:<14} found on {x['found']:>3}  survive {x['survive']:>3} = {x['per_1000']:>4.0f} ({x['low']:.0f}-{x['high']:.0f})   killed by: {killed}")
    s = fa["single_plate_outbursts"]
    lines.append(f"   single-plate outbursts found {s['found']}, surviving {s['survive']} (one plate is one series, so the series rule removes them all)")
    lines += ["", "2. Completeness: amplitude at 50% / 90% recovery (any matching trigger | trigger of the right type)"]
    fams = sorted({c["family"] for c in d["completeness"]["any"]["crossings"]})
    for fam in fams:
        lines.append(f"   {fam:<9} {_cell(d, 'any', fam):>16}  |  {_cell(d, 'right_type', fam):>16}")
    kinds = ", ".join(f"{k} {v}" for k, v in d["completeness"]["suspect_kinds"].items()) or "none"
    lines.append(f"   expected-missed signals recovered: {d['completeness']['suspect'][0]} of {d['completeness']['suspect'][1]} (matched by: {kinds})")
    lines += ["", "3. Confusion: injected family against the type of the trigger that matched"]
    for fam, row in d["confusion"].items():
        lines.append(f"   {fam:<9} " + ", ".join(f"{k} {v}" for k, v in row.items()))
    if "ablations" in r:
        lines += ["", "4. Ablations (constants with a surviving trigger of any kind | recovered share of all non-missed injections)"]
        default = d
        for name, a in {"default": default, **r["ablations"]}.items():
            tot = [o for b in a["completeness"]["any"]["bins"] for o in [b]]
            k, n = sum(b["k"] for b in tot), sum(b["n"] for b in tot)
            lines.append(f"   {name:<15} false alarms {a['false_alarms']['any']['survive']:>3} of {a['false_alarms']['hosts']}   recovered {k}/{n} = {k / n:.1%}   expected-missed recovered {a['completeness']['suspect'][0]}")
    if "sweeps" in r:
        lines += ["", "5. Sweeps, one parameter at a time (false alarms of that trigger on 361 constants | recovered of that family, one replicate)"]
        for kind, rows in r["sweeps"].items():
            lines.append(f"   {kind}")
            for x in rows:
                lines.append(f"     {x['parameter']:<26} {x['value']!s:>5}  FA {x['false_alarms']:>3} ({x['false_alarms_per_1000']:>4.0f}/1000)  recovered {x['recovered']:>4}/{x['n']:<4} = {x['recovered'] / x['n']:.1%}  top amplitude {x['recovered_at_top_amplitude']}/{x['n_top']}")
    lines += ["", "6. Known variables of the dev tiles"]
    for v in r["known_variables"]:
        lines.append(f"   {v['tile']:<5} {v['name']:<10} {v['type']!s:<8} {v['stop_reason']:<26} {v['triggers']}")
    return "\n".join(lines)
