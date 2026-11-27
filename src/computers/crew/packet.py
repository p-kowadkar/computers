"""Packet v0: the context an agent sees for one candidate (plan 8.4, spec S8 R1).

Built deterministically from the L1 result of a star, its candidate row and its star-table row.
This version has blocks 1 (identity), 2 (century summary), 3 (the event) and 9 (the L1
checklist), mode `lightcurve_only`. Every fact has an evidence id, and ids are assigned in a
fixed order so the same input gives the same packet. The packet is bounded: block 3 is capped,
and a token budget thins the packet and records every cut.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

import numpy as np

from computers.crew.schemas import ContextPacket, EventDescription, Evidence, StarIdentity
from computers.l1.tags import Tag
from computers.l1.triggers import Trigger, TriggerInput
from computers.thresholds import THRESHOLDS

__all__ = ["BLOCK_ORDER", "MAX_EVENT_POINTS", "TOKEN_BUDGET", "build_packet", "estimate_tokens", "render"]

TOKEN_BUDGET = 2500  # plan 8.4 [start]
MAX_EVENT_POINTS = 30  # block 3 never lists more points than this
CHARS_PER_TOKEN = 3.5
BLOCK_ORDER = {"maury": (9, 3, 2, 1)}  # which blocks a role reads first (plan 8.4)
FIXED_BLOCKS = (1, 9)  # never cut by the budget
MAD_TO_SIGMA = 1.4826


def _r(x: float, n: int = 3) -> float | None:
    return None if x is None or not math.isfinite(x) else round(float(x), n)


def _tag_names(mask: int) -> list[str]:
    return [t.name for t in Tag if mask & int(t)]


def _thin(rows: np.ndarray, score: np.ndarray, keep: int) -> np.ndarray:
    """At most `keep` of `rows`: the half with the largest `score`, then an even sample of the rest, in the original order."""
    if len(rows) <= keep:
        return rows
    n_top = keep // 2
    order = np.argsort(-score, kind="stable")
    chosen = set(order[:n_top].tolist())
    rest = [i for i in range(len(rows)) if i not in chosen]
    pick = np.linspace(0, len(rest) - 1, keep - n_top).round().astype(int)
    chosen |= {rest[i] for i in pick}
    return rows[sorted(chosen)]


def estimate_tokens(text: str) -> int:
    """A documented estimate: characters over 3.5, rounded up. It is not any model's tokenizer."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


# ------------------------------------------------------------------------------ blocks


def _block1(candidate: Mapping[str, Any], star: Mapping[str, Any], trig: Trigger) -> list[tuple[int, str, dict]]:
    return [
        (1, "identity", {
            "ref_number": int(star["ref_number"]), "tile": star["tile"], "ra_deg": _r(star["ra_deg"], 5),
            "dec_deg": _r(star["dec_deg"], 5), "apass_b": _r(star.get("apass_b"), 2), "color": _r(star.get("color"), 2),
            "candidate_id": candidate["candidate_id"],
        }),
        (1, "event", {
            "candidate": True, "kind": trig.kind, "epoch_start": _r(trig.epoch_start, 2), "epoch_end": _r(trig.epoch_end, 2),
            "amplitude_mag": _r(trig.amplitude_mag, 2), "amplitude_is_lower_limit": trig.lower_limit,
            "single_plate": trig.single_plate, "read_detrended_curve": trig.detrended,
        }),
        (1, "score", {"l2_score": _r(candidate.get("l2_score"), 4), "note": "orders candidates for the budget; not a probability"}),
    ]  # fmt: skip


def _block2(inp: TriggerInput) -> list[tuple[int, str, dict]]:
    g = inp.good
    off = inp.mag[g] - inp.baseline
    year = inp.year[g]
    series = inp.curve.series[g]
    out: list[tuple[int, str, dict]] = []
    dec = np.floor(year / 10).astype(int) * 10
    for d in sorted(set(dec.tolist())):
        x = off[dec == d]
        mad = float(np.median(np.abs(x - np.median(x)))) * MAD_TO_SIGMA
        out.append((2, "decade_summary", {"decade": int(d), "n": len(x), "median_offset_mag": _r(np.median(x)), "scatter_mag": _r(mad)}))
    for s in sorted(set(series.tolist())):
        x = off[series == s]
        out.append((2, "series_zero_point", {"series": str(s), "n": len(x), "median_offset_mag": _r(np.median(x))}))
    return out


def _block3(trig: Trigger, inp: TriggerInput) -> tuple[list[tuple[int, str, dict]], str | None]:
    ev = np.array(sorted(trig.evidence), dtype=int)
    c = inp.curve
    if len(ev) == 0:
        return [], None
    dmag = np.where(np.isfinite(inp.mag[ev]), inp.mag[ev] - inp.baseline, np.nan)
    score = np.where(np.isfinite(dmag), np.abs(dmag), 0.0)
    kept = _thin(ev, score, MAX_EVENT_POINTS)
    out = []
    for i in kept:
        i = int(i)
        det = bool(np.isfinite(inp.mag[i]))
        out.append((3, "measurement", {
            "year": _r(inp.year[i], 3), "series": str(c.series[i]), "plate": str(c.exposure_id[i]),
            "offset_mag": _r(inp.mag[i] - inp.baseline, 3) if det else None,
            "limiting_mag_offset": _r(inp.limit[i] - inp.baseline, 3) if np.isfinite(inp.limit[i]) else None,
            "detection": det, "tags": _tag_names(int(c.tags[i])),
        }))  # fmt: skip
    note = None
    if len(kept) < len(ev):
        note = f"block 3 lists {len(kept)} of the {len(ev)} points of the trigger (the strongest and an even sample)"
        out.append((3, "points_omitted", {"listed": len(kept), "total": len(ev), "rule": "strongest half, even sample of the rest"}))
    return out, note


def _checks(cand: Mapping[str, Any], trig: Trigger, inp: TriggerInput) -> list[tuple[int, str, dict]]:
    c = inp.curve
    ev = np.array(sorted(trig.evidence), dtype=int)
    n = len(ev)
    tags = c.tags[ev] if n else np.zeros(0, dtype=np.uint64)

    def share(tag: Tag) -> tuple[int, int]:
        return int(((tags & np.uint64(int(tag))) != 0).sum()), n

    rows: list[tuple[str, bool, dict]] = []
    for name, tag in (
        ("emulsion_non_blue", Tag.NON_BLUE), ("emulsion_unknown", Tag.EMULSION_UNKNOWN), ("blend_suspect", Tag.BLEND_SUSPECT),
        ("source_merged", Tag.SOURCE_MERGED), ("shape_outlier", Tag.SHAPE_OUTLIER), ("plate_edge", Tag.PLATE_EDGE),
        ("deep_limit_suspect", Tag.DEEP_LIMIT_SUSPECT), ("soft_rejected_points", Tag.STD_SOFT), ("no_plate_offset", Tag.NO_OFFSET),
    ):  # fmt: skip
        k, tot = share(tag)
        rows.append((name, k > 0, {"flagged": k, "of": tot}))
    n_series = len(set(c.series[ev].tolist())) if n else 0
    rows.append(("series_count", n_series < int(THRESHOLDS["trigger_min_series"].value), {"series": n_series}))
    rows.append(("detrended", not trig.detrended, {"detrended": trig.detrended}))
    rows.append(("known_variable", "known_variable" in (cand.get("l1_flags") or []), {}))
    rejected = int(c.rejected.sum())
    rows.append(("rows_rejected_by_cleaning", False, {"rejected": rejected, "of_rows": len(c)}))
    return [(9, "check", {"check": name, "status": "flag" if flag else "pass", **detail}) for name, flag, detail in rows]


# ----------------------------------------------------------------------------- packet


def _assemble(items: list[tuple[int, str, dict]]) -> list[Evidence]:
    """Ids in fixed order: block 1, 2, 3, then 9, each in the order built."""
    ordered = sorted(enumerate(items), key=lambda t: (t[1][0], t[0]))
    return [Evidence(id=f"E-{k + 1:03d}", block=b, kind=kind, payload=p) for k, (_, (b, kind, p)) in enumerate(ordered)]


def _tokens(packet: ContextPacket) -> int:
    return estimate_tokens(render(packet, "maury"))


def build_packet(
    candidate: Mapping[str, Any], star: Mapping[str, Any], trigger: Trigger, inp: TriggerInput, *, budget: int = TOKEN_BUDGET
) -> ContextPacket:
    """The packet of one candidate. `candidate` has `candidate_id`, `l2_score` and `l1_flags`; `star` is a stars-table row."""
    b1, b2 = _block1(candidate, star, trigger), _block2(inp)
    b3, note3 = _block3(trigger, inp)
    b9 = _checks(candidate, trigger, inp)
    cuts: list[str] = [note3] if note3 else []
    event = EventDescription(
        kind=trigger.kind, epoch_start=float(trigger.epoch_start), epoch_end=float(trigger.epoch_end),
        amplitude_mag=float(trigger.amplitude_mag), lower_limit=trigger.lower_limit, single_plate=trigger.single_plate,
    )  # fmt: skip
    ident = StarIdentity(
        ref_number=int(star["ref_number"]), tile=str(star["tile"]), ra_deg=float(star["ra_deg"]), dec_deg=float(star["dec_deg"]),
        apass_b=star.get("apass_b"), color=star.get("color"),
    )  # fmt: skip

    def make() -> ContextPacket:
        return ContextPacket(
            candidate_id=candidate["candidate_id"], star=ident, event=event, evidence=_assemble(b1 + b2 + b3 + b9),
            mode="lightcurve_only", cuts=list(cuts),
        )  # fmt: skip

    pk = make()
    # thin, least important first: the event points, then the quiet decades, then the series zero-points
    for keep in (15, 8):
        if _tokens(pk) <= budget:
            break
        meas = [x for x in b3 if x[1] == "measurement"]
        if len(meas) > keep:
            drop = len(meas) - keep
            idx = np.linspace(0, len(meas) - 1, keep).round().astype(int)
            kept_rows = {id(meas[i]) for i in idx}
            b3 = [x for x in b3 if x[1] != "measurement" or id(x) in kept_rows]
            cuts.append(f"block 3 thinned by {drop} points to fit the token budget")
            pk = make()
    if _tokens(pk) > budget:
        n = sum(1 for x in b2 if x[1] == "decade_summary")
        b2 = [x for x in b2 if x[1] != "decade_summary" or x[2]["n"] >= 50]
        cuts.append(f"block 2 decade summaries with fewer than 50 points dropped ({n - sum(1 for x in b2 if x[1] == 'decade_summary')})")
        pk = make()
    if _tokens(pk) > budget:
        series = sorted((x for x in b2 if x[1] == "series_zero_point"), key=lambda x: -x[2]["n"])
        keep_s = {id(x) for x in series[:4]}
        dropped = len(series) - min(4, len(series))
        b2 = [x for x in b2 if x[1] != "series_zero_point" or id(x) in keep_s]
        cuts.append(f"block 2 series zero-points limited to the 4 largest series ({dropped} dropped)")
        pk = make()
    if _tokens(pk) > budget:
        cuts.append(f"still over the budget of {budget} tokens after every cut; blocks 1 and 9 are never cut")
        pk = make()
    return pk.model_copy(update={"token_estimate": _tokens(pk)})


def render(packet: ContextPacket, role: str = "maury") -> str:
    """The packet as text for an agent: the blocks it reads first come first. Deterministic, and this is the text that is hashed."""
    order = BLOCK_ORDER.get(role, (1, 2, 3, 9))
    names = {1: "IDENTITY AND EVENT (a candidate, not a result)", 2: "CENTURY SUMMARY", 3: "THE EVENT'S POINTS", 9: "KNOWN-ISSUE CHECKLIST"}
    lines = [f"PACKET {packet.packet_version} for candidate {packet.candidate_id}, mode {packet.mode}"]
    for b in order:
        rows = [e for e in packet.evidence if e.block == b]
        if not rows:
            continue
        lines.append(f"## Block {b}: {names[b]}")
        lines += [f"[{e.id}] {e.kind} {json.dumps(e.payload, separators=(',', ':'), sort_keys=True)}" for e in rows]
    if packet.cuts:
        lines.append("## Cuts made to fit the budget")
        lines += [f"- {c}" for c in packet.cuts]
    return "\n".join(lines)
