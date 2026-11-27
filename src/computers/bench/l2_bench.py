"""The L2 benchmark: three arms on the dev split (spec S5 R4).

Arm 1 is L1 strict (the registry). Arm 2 is L1 relaxed (`trigger_min_series` 1, nothing else
changed). Arm 3 is arm 2 followed by the ranker. Every arm sees the same constants and the
same planted signals. `collect` runs L1 once per signal under both parameter sets and keeps,
for each, the surviving triggers that matched the signal, with the star's features. The
anomaly score and the ranker are cross-fitted by host star, so nothing is scored by a model
that saw its own host. The registry is never changed here.
"""
from __future__ import annotations

import pickle
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import roc_auc_score

from computers.bench import metrics as M
from computers.bench.inject import inject
from computers.bench.metrics import Outcome
from computers.bench.recovery import FAMILY_TYPE, host_input, matches, signal_span
from computers.bench.trigger_bench import Dev
from computers.data.timeconv import jd_to_year
from computers.l1.analyse import analyse_curve
from computers.l1.triggers import Trigger, TriggerParams
from computers.l2 import anomaly as A
from computers.l2 import ranker as R
from computers.l2.features import FEATURES, feature_vector, features
from computers.l2.ranker import trigger_row

__all__ = ["Collected", "arms", "collect", "cross_fit", "fit_final", "format_l2_report", "outburst_gain_interval", "outburst_tradeoff", "run_l2_report", "trigger_row"]

ARMS = ("strict", "relaxed")


@dataclass
class Collected:
    """What one pass of L1 over the dev split left behind. Plain data, so it can be pickled."""
    hosts: dict[int, dict[str, Any]] = field(default_factory=dict)  # host id -> features, survivors per arm
    signals: list[dict[str, Any]] = field(default_factory=list)  # one per planted signal
    seconds: float = 0.0


def collect(
    dev: Dev,
    strict: TriggerParams,
    relaxed: TriggerParams,
    *,
    replicate: int | None = None,
    say: Callable[[str], None] = lambda _: None,
) -> Collected:
    """Run both parameter sets over every constant and every planted signal of the dev split."""
    import time

    t0 = time.perf_counter()
    out = Collected()
    params = {"strict": strict, "relaxed": relaxed}
    base_triggers: dict[int, dict[str, tuple[Trigger, ...]]] = {}
    cols: dict[int, np.ndarray] = {}
    for n, (hid, h) in enumerate(dev.hosts.items()):
        ctx = dev.contexts[hid]
        cols[hid] = ctx.columns_of(h.curve)
        built = host_input(ctx, h, h.mag, strict, columns=cols[hid])
        rec: dict[str, Any] = {"features": None, "arms": {a: [] for a in ARMS}}
        base_triggers[hid] = {a: () for a in ARMS}
        if built is not None:
            curve, inp = built
            rec["features"] = feature_vector(features(inp))
            for a in ARMS:
                res = analyse_curve(curve, inp, None, params[a])
                base_triggers[hid][a] = res.triggers
                rec["arms"][a] = [trigger_row(t, inp) for t in res.triggers if t.survives]
        out.hosts[hid] = rec
        if (n + 1) % 100 == 0:
            say(f"  hosts {n + 1}/{len(dev.hosts)}")
    specs = dev.specs(replicate=replicate)
    for i, spec in enumerate(specs):
        hid = spec.host_id
        h, ctx = dev.hosts[hid], dev.contexts[hid]
        first, last = float(jd_to_year(h.time[0])), float(jd_to_year(h.time[-1]))
        span = signal_span(spec.signal, first, last)
        mag = inject(h.time, h.mag, h.limit, spec.signal).mag
        rec = {
            "host_id": hid, "family": spec.family, "amplitude_index": spec.amplitude_index,
            "amplitude": spec.signal.amplitude, "expected_missed": spec.expected_missed,
            "features": None, "arms": {a: [] for a in ARMS},
        }  # fmt: skip
        built = host_input(ctx, h, mag, strict, columns=cols[hid])
        if built is not None:
            curve, inp = built
            rec["features"] = feature_vector(features(inp))
            for a in ARMS:
                found = analyse_curve(curve, inp, None, params[a]).triggers
                hit = matches(found, base_triggers[hid][a], span, spec.family)
                rec["arms"][a] = [trigger_row(t, inp) for t in hit]
        out.signals.append(rec)
        if (i + 1) % 2000 == 0:
            say(f"  signals {i + 1}/{len(specs)}  {time.perf_counter() - t0:.0f} s")
    out.seconds = time.perf_counter() - t0
    return out


def save_collected(c: Collected, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(c, f)


def load_collected(path: str | Path) -> Collected:
    with open(path, "rb") as f:
        return pickle.load(f)  # our own file, written by `save_collected`


# ------------------------------------------------------------------ cross-fitting


def _quiet_matrix(c: Collected, host_ids: Iterable[int]) -> np.ndarray:
    rows = [c.hosts[h]["features"] for h in host_ids if c.hosts[h]["features"] is not None]
    return np.vstack(rows)


def cross_fit(
    c: Collected,
    variant: str,
    *,
    k: int = 5,
    seed: int = 0,
    arm: str = "relaxed",
) -> dict[str, Any]:
    """Anomaly scores and ranker scores for every relaxed survivor, each from models that never saw its host.

    Returns `neg` (survivors on constants, with scores), `pos` (survivors that matched a signal, with scores,
    primary first), and the AUC of the pooled held-out scores of the primary rows.
    """
    columns = R.VARIANTS[variant]
    fold_of = R.group_folds(sorted(c.hosts), k, seed)
    forests = {}
    for f in range(k):
        train = [h for h, fo in fold_of.items() if fo != f]
        forests[f] = A.fit(_quiet_matrix(c, train), seed=seed)

    def anomaly_of(host_id: int, feats: np.ndarray | None) -> float:
        if feats is None:
            return float("nan")
        return float(A.score(forests[fold_of[host_id]], feats)[0])

    neg: list[dict] = []
    for hid, rec in c.hosts.items():
        a = anomaly_of(hid, rec["features"])
        neg += [{**r, "host_id": hid, "anomaly": a} for r in rec["arms"][arm]]
    pos: list[dict] = []
    for si, s in enumerate(c.signals):
        a = anomaly_of(s["host_id"], s["features"])
        hits = [{**r, "host_id": s["host_id"], "anomaly": a, "signal": si} for r in s["arms"][arm]]
        # the primary row is the one of the family's own type if any, else the first
        hits.sort(key=lambda r: r["kind"] != FAMILY_TYPE[s["family"]])
        for j, r in enumerate(hits):
            r["primary"] = j == 0
        pos += hits
    primary = [r for r in pos if r["primary"] and not c.signals[r["signal"]]["expected_missed"]]
    x_neg, x_pos = R.design(neg, columns), R.design(primary, columns)
    f_neg = np.array([fold_of[r["host_id"]] for r in neg])
    f_pos = np.array([fold_of[r["host_id"]] for r in primary])
    s_neg, s_pos = np.full(len(neg), np.nan), np.full(len(primary), np.nan)
    f_all = np.array([fold_of[r["host_id"]] for r in pos])
    x_all = R.design(pos, columns)
    s_all = np.full(len(pos), np.nan)
    for f in range(k):
        xtr = np.vstack([x_neg[f_neg != f], x_pos[f_pos != f]])
        ytr = np.r_[np.zeros((f_neg != f).sum()), np.ones((f_pos != f).sum())]
        model = R.fit(xtr, ytr, columns, seed=seed)  # one model per fold, trained on the other folds' hosts
        if (f_neg == f).any():
            s_neg[f_neg == f] = R.predict(model, x_neg[f_neg == f])
        if (f_pos == f).any():
            s_pos[f_pos == f] = R.predict(model, x_pos[f_pos == f])
        if (f_all == f).any():  # every match, so arm 3 can use any of them
            s_all[f_all == f] = R.predict(model, x_all[f_all == f])
    for r, v in zip(neg, s_neg, strict=True):
        r["score"] = float(v)
    for r, v in zip(pos, s_all, strict=True):
        r["score"] = float(v)
    y = np.r_[np.zeros(len(neg)), np.ones(len(primary))]
    sc = np.r_[s_neg, s_pos]
    auc = float(roc_auc_score(y, sc)) if len(set(y)) == 2 and np.isfinite(sc).all() else float("nan")
    return {"neg": neg, "pos": pos, "auc": auc, "n_pos": len(primary), "n_neg": len(neg), "variant": variant,
            "calibration": calibration(y, sc)}  # fmt: skip


def calibration(y: np.ndarray, p: np.ndarray, bins: int = 5) -> list[dict[str, float]]:
    """Held-out scores in equal-count bins: the mean score against the share that was real."""
    order = np.argsort(p)
    out = []
    for chunk in np.array_split(order, bins):
        if len(chunk):
            out.append({"n": len(chunk), "mean_score": float(p[chunk].mean()), "share_real": float(y[chunk].mean())})
    return out


# ----------------------------------------------------------------------- the arms


def _outcomes(c: Collected, pick: Callable[[dict], tuple[bool, str | None]]) -> list[Outcome]:
    out = []
    for s in c.signals:
        hit, kind = pick(s)
        right = hit and kind == FAMILY_TYPE[s["family"]]
        out.append(Outcome(s["host_id"], s["family"], s["amplitude_index"], s["amplitude"], s["expected_missed"],
                           hit, right, kind))  # fmt: skip
    return out


def _arm_pick(arm: str) -> Callable[[dict], tuple[bool, str | None]]:
    def pick(s: dict) -> tuple[bool, str | None]:
        rows = s["arms"][arm]
        if not rows:
            return False, None
        right = [r for r in rows if r["kind"] == FAMILY_TYPE[s["family"]]]
        return True, (right[0] if right else rows[0])["kind"]

    return pick


def _scored_pick(c: Collected, pos: list[dict], thr: float) -> Callable[[dict], tuple[bool, str | None]]:
    by_signal: dict[int, list[dict]] = {}
    for r in pos:
        by_signal.setdefault(r["signal"], []).append(r)
    index = {id(s): i for i, s in enumerate(c.signals)}

    def pick(s: dict) -> tuple[bool, str | None]:
        rows = [r for r in by_signal.get(index[id(s)], []) if r["score"] >= thr]
        if not rows:
            return False, None
        right = [r for r in rows if r["kind"] == FAMILY_TYPE[s["family"]]]
        return True, (right[0] if right else rows[0])["kind"]

    return pick


def share(outcomes: Sequence[Outcome], family: str | None = None, right: bool = False) -> tuple[int, int]:
    """Recovered and total of the signals that were not planned as expected-missed."""
    sel = [o for o in outcomes if not o.expected_missed and (family is None or o.family == family)]
    return sum((o.right_type if right else o.recovered) for o in sel), len(sel)


def false_alarm_hosts(c: Collected, arm: str, neg: list[dict] | None = None, thr: float | None = None) -> dict[str, Any]:
    """Constants with a surviving trigger (of a kind, or any), after an optional score threshold."""
    n = len(c.hosts)
    kinds = ("outburst", "dip", "step", "long_term", "disappearance")
    per: dict[str, set[int]] = {k: set() for k in (*kinds, "any")}
    if neg is None:
        rows = [{**r, "host_id": hid} for hid, rec in c.hosts.items() for r in rec["arms"][arm]]
    else:
        rows = [r for r in neg if thr is None or r["score"] >= thr]
    for r in rows:
        per[r["kind"]].add(r["host_id"])
        per["any"].add(r["host_id"])
    out = {"hosts": n}
    for k, ids in per.items():
        rate = M.false_alarm_rate(len(ids), n)
        out[k] = {"survive": len(ids), "per_1000": rate.per_1000, "low": rate.low_per_1000, "high": rate.high_per_1000}
    return out


def arms(c: Collected, variant: str = "full", *, k: int = 5, seed: int = 0, strict_fa_cap: int | None = None, cf: dict | None = None) -> dict[str, Any]:
    """The three arms. The threshold of arm 3 is the lowest held-out score at which its false alarms do not exceed arm 1's."""
    cf = cf or cross_fit(c, variant, k=k, seed=seed)
    fa1 = false_alarm_hosts(c, "strict")
    fa2 = false_alarm_hosts(c, "relaxed")
    cap = fa1["any"]["survive"] if strict_fa_cap is None else strict_fa_cap
    cand = sorted({float(np.nextafter(r["score"], 2.0)) for r in cf["neg"]} | {0.0})  # just above each false trigger
    thr = 1.0 + 1e-9  # above every score: nothing survives, always meets the cap
    for t in [*cand, 1.0 + 1e-9]:
        if false_alarm_hosts(c, "relaxed", cf["neg"], t)["any"]["survive"] <= cap:
            thr = t
            break
    o1, o2 = _outcomes(c, _arm_pick("strict")), _outcomes(c, _arm_pick("relaxed"))
    o3 = _outcomes(c, _scored_pick(c, cf["pos"], thr))
    fa3 = false_alarm_hosts(c, "relaxed", cf["neg"], thr)
    fams = ("outburst", "dip", "step", "linear", "wave")
    table = {
        name: {
            "false_alarms": fa,
            "recovered": {f: share(o, f) for f in (*fams, None)},
            "right_type": {f: share(o, f, True) for f in (*fams, None)},
            "expected_missed": sum(x.recovered for x in o if x.expected_missed),
        }
        for name, o, fa in (("strict", o1, fa1), ("relaxed", o2, fa2), ("relaxed+l2", o3, fa3))
    }
    # signals strict recovered and relaxed+L2 does not: the cost of L2
    lost = sum(a.recovered and not b.recovered for a, b in zip(o1, o3, strict=True) if not a.expected_missed)
    gained = sum(b.recovered and not a.recovered for a, b in zip(o1, o3, strict=True) if not a.expected_missed)
    return {"variant": variant, "threshold": thr, "auc": cf["auc"], "n_pos": cf["n_pos"], "n_neg": cf["n_neg"],
            "calibration": cf["calibration"], "arms": table, "lost_vs_strict": lost, "gained_vs_strict": gained}  # fmt: skip



def outburst_tradeoff(c: Collected, variant: str, budgets: Sequence[int] = (0, 1, 2, 3, 5, 10), *, k: int = 5, seed: int = 0, cf: dict | None = None) -> dict[str, Any]:
    """Outbursts only, where L1's series rule costs the most: recovered share at each false-alarm budget (constants).

    The pooled AUC is dominated by trigger kind, so this is the number that says whether the ranker helps the class it is for.
    """
    cf = cf or cross_fit(c, variant, k=k, seed=seed)
    neg = [r for r in cf["neg"] if r["kind"] == "outburst"]
    pos = [r for r in cf["pos"] if r["kind"] == "outburst" and c.signals[r["signal"]]["family"] == "outburst"
           and not c.signals[r["signal"]]["expected_missed"]]  # fmt: skip
    total = sum(1 for s in c.signals if s["family"] == "outburst" and not s["expected_missed"])
    best: dict[int, float] = {}
    for r in pos:
        best[r["signal"]] = max(best.get(r["signal"], -1.0), r["score"])
    y = np.r_[np.zeros(len(neg)), np.ones(len(best))]
    # AUC between constants' false outbursts (best score per host) and planted outbursts (best score per signal)
    host_best: dict[int, float] = {}
    for r in neg:
        host_best[r["host_id"]] = max(host_best.get(r["host_id"], -1.0), r["score"])
    y = np.r_[np.zeros(len(host_best)), np.ones(len(best))]
    sc = np.r_[list(host_best.values()), list(best.values())]
    auc = float(roc_auc_score(y, sc)) if len(host_best) and len(best) else float("nan")
    rows = []
    cands = sorted({float(np.nextafter(v, 2.0)) for v in host_best.values()} | {0.0})
    for b in budgets:
        thr = next((t for t in [*cands, 2.0] if sum(v >= t for v in host_best.values()) <= b), 2.0)
        rows.append({"budget": b, "threshold": thr, "recovered": sum(v >= thr for v in best.values()), "total": total})
    return {"variant": variant, "auc_outburst_only": auc, "n_false_hosts": len(host_best), "n_signals_hit": len(best), "rows": rows}



def outburst_gain_interval(
    c: Collected, cf: dict, budget: int, *, n_boot: int = 300, seed: int = 0
) -> dict[str, float]:
    """Gain in outburst recovery (percentage points) over L1 strict at `budget` false-alarm constants, with a bootstrap interval.

    Constants are resampled with replacement and the signals follow their host. The threshold is
    re-chosen on every resample, so the interval includes that choice (it is still a little optimistic,
    because the scores were not refitted).
    """
    rng = np.random.default_rng(seed)
    hosts = list(c.hosts)
    pos_of = {h: j for j, h in enumerate(hosts)}
    host_best: dict[int, float] = {}
    for r in cf["neg"]:
        if r["kind"] == "outburst":
            host_best[r["host_id"]] = max(host_best.get(r["host_id"], -1.0), r["score"])
    hv = np.array([host_best.get(h, -1.0) for h in hosts])
    best: dict[int, float] = {}
    for r in cf["pos"]:
        if r["kind"] == "outburst":
            best[r["signal"]] = max(best.get(r["signal"], -1.0), r["score"])
    ids = [i for i, s in enumerate(c.signals) if s["family"] == "outburst" and not s["expected_missed"]]
    sv = np.array([best.get(i, -1.0) for i in ids])
    strict = np.array([bool(c.signals[i]["arms"]["strict"]) for i in ids])
    sh = np.array([pos_of[c.signals[i]["host_id"]] for i in ids])
    gains = []
    for _ in range(n_boot):
        draw = rng.integers(0, len(hosts), len(hosts))
        w = np.bincount(draw, minlength=len(hosts))[sh]
        if w.sum() == 0:
            continue
        vals = hv[draw]
        thr = 2.0
        for t in [*np.sort(np.nextafter(np.unique(vals[vals > -1]), 2.0)), 0.0]:
            if (vals >= t).sum() <= budget:
                thr = float(t)
                break
        gains.append(100.0 * ((w * (sv >= thr)).sum() - (w * strict).sum()) / w.sum())
    lo, mid, hi = np.percentile(gains, [2.5, 50, 97.5])
    return {"budget": budget, "median": float(mid), "low": float(lo), "high": float(hi)}



def fit_final(c: Collected, variant: str = "plain", *, seed: int = 0) -> R.RankerModel:
    """The ranker fitted on every dev row, for use on real tiles. Cross-validated numbers come from `cross_fit`, not from this."""
    columns = R.VARIANTS[variant]
    neg = [{**r, "host_id": hid} for hid, rec in c.hosts.items() for r in rec["arms"]["relaxed"]]
    pos = []
    for s in c.signals:
        rows = s["arms"]["relaxed"]
        if rows and not s["expected_missed"]:
            right = [r for r in rows if r["kind"] == FAMILY_TYPE[s["family"]]]
            pos.append((right or rows)[0])
    x = np.vstack([R.design(neg, columns), R.design(pos, columns)])
    y = np.r_[np.zeros(len(neg)), np.ones(len(pos))]
    return R.fit(x, y, columns, seed=seed)


# ------------------------------------------------------------------------ report


def run_l2_report(
    dev: Dev,
    strict: TriggerParams,
    relaxed: TriggerParams,
    *,
    collected: Collected | None = None,
    variants: Sequence[str] = tuple(R.VARIANTS),
    say: Callable[[str], None] = lambda _: None,
) -> dict[str, Any]:
    c = collected or collect(dev, strict, relaxed, say=say)
    say(f"collected in {c.seconds:.0f} s")
    fits = {v: cross_fit(c, v) for v in variants}
    return {
        "hosts": len(c.hosts), "signals": len(c.signals), "seconds": c.seconds,
        "feature_names": list(FEATURES), "variants": {v: arms(c, v, cf=fits[v]) for v in variants},
        "outburst": {v: outburst_tradeoff(c, v, cf=fits[v]) for v in variants},
        "outburst_gain": {v: [outburst_gain_interval(c, fits[v], b) for b in (0, 1)] for v in variants},
        "relaxed_survivor_kinds": dict(Counter(r["kind"] for rec in c.hosts.values() for r in rec["arms"]["relaxed"])),
    }  # fmt: skip


def _pct(a: tuple[int, int]) -> str:
    return f"{a[0]}/{a[1]} = {100 * a[0] / a[1]:.1f}%" if a[1] else "-"


def format_l2_report(r: dict[str, Any]) -> str:
    lines = [f"L2 on the dev split: {r['hosts']} constants, {r['signals']} signals, {r['seconds']:.0f} s"]
    lines.append(f"Relaxed survivors on constants by kind: {r['relaxed_survivor_kinds']}")
    for v, res in r["variants"].items():
        lines.append(f"\nVariant {v}: AUC {res['auc']:.3f} ({res['n_pos']} positives, {res['n_neg']} negatives), threshold {res['threshold']:.3f}")
        for name, a in res["arms"].items():
            fa = a["false_alarms"]
            lines.append(f"  {name:<11} false alarms any {fa['any']['survive']} of {fa['hosts']} ({fa['any']['per_1000']:.0f} per 1,000, {fa['any']['low']:.0f}-{fa['any']['high']:.0f})"
                         f"   expected-missed recovered {a['expected_missed']}")  # fmt: skip
            for fam in ("outburst", "dip", "step", "linear", "wave"):
                lines.append(f"     {fam:<9} any {_pct(a['recovered'][fam]):<18} right type {_pct(a['right_type'][fam])}")
        lines.append(f"  against strict: {res['gained_vs_strict']} signals gained, {res['lost_vs_strict']} lost")
        lines.append("  calibration (held-out, equal-count bins): " + ", ".join(f"{b['mean_score']:.2f}->{b['share_real']:.2f} (n {b['n']})" for b in res["calibration"]))
    lines.append("\nOutbursts only: recovered of planted outbursts at each false-alarm budget (constants with a surviving outburst)")
    for v, o in r.get("outburst", {}).items():
        cells = "  ".join(f"FA<={x['budget']}: {x['recovered']}/{x['total']} = {100 * x['recovered'] / x['total']:.1f}%" for x in o["rows"])
        lines.append(f"  {v:<14} AUC {o['auc_outburst_only']:.3f}  {cells}")
    lines.append("\nGain in outburst recovery over L1 strict, percentage points, bootstrap 95% interval (the pass mark is 3)")
    for v, gs in r.get("outburst_gain", {}).items():
        lines.append(f"  {v:<14} " + "  ".join(f"FA<={g['budget']}: {g['median']:+.1f} ({g['low']:+.1f} to {g['high']:+.1f})" for g in gs))
    return "\n".join(lines)


def run_l2_bench(data_dir: str | Path, *, reuse: bool = False, say: Callable[[str], None] = print) -> str:
    """Collect (or reuse) the dev pass, write the report, and fit and save the ranker used on real tiles."""
    from computers.bench.trigger_bench import load_dev
    from computers.l2.run import MODEL_NAME, VARIANT

    data_dir = Path(data_dir)
    pkl = data_dir / "reports" / "l2-collected.pkl"
    strict = TriggerParams.from_registry()
    relaxed = strict.but(trigger_min_series=1)
    if reuse and pkl.is_file():
        c, dev = load_collected(pkl), None
    else:
        dev = load_dev(data_dir)
        c = collect(dev, strict, relaxed, say=say)
        save_collected(c, pkl)
    result = run_l2_report(dev, strict, relaxed, collected=c, say=say)  # type: ignore[arg-type]
    import json

    (data_dir / "reports" / "bench-l2.json").write_text(json.dumps(result, indent=1, default=float) + "\n")
    text = format_l2_report(result)
    (data_dir / "reports" / "bench-l2.txt").write_text(text + "\n")
    R.save(fit_final(c, VARIANT), data_dir / "models" / MODEL_NAME)
    return text
