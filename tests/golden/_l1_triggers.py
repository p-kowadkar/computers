"""Helpers for the trigger goldens: run L1 triggers on a cleaned fixture (no detrending: excerpts have no neighbours)."""
from __future__ import annotations

import json
from pathlib import Path

from _l1 import clean, fixtures, load

from computers.l1.analyse import analyse_curve
from computers.l1.triggers import TriggerParams, prepare

EXPECTED_TRIGGERS = Path(__file__).parent / "expected" / "l1_triggers.json"


def analyse_fixture(path: Path, policy: str = "reject"):
    d, table = load(path)
    curve = clean(table, d["ref_number"], policy)
    inp = prepare(curve, detrended=False)
    return d, analyse_curve(curve, inp, None, TriggerParams.from_registry())


def summary(path: Path, policy: str) -> dict:
    d, r = analyse_fixture(path, policy)
    return {
        "star": d.get("star"),
        "n_triggers": len(r.triggers),
        "n_survivors": len(r.survivors),
        "triggers": [
            {
                "kind": t.kind,
                "epoch_start": round(t.epoch_start, 2),
                "epoch_end": round(t.epoch_end, 2),
                "amplitude_mag": round(t.amplitude_mag, 2),
                "n_evidence": len(t.evidence),
                "kill_reasons": sorted(t.kill_reasons),
            }
            for t in r.triggers
        ],
    }


def current() -> dict:
    return {str(load(p)[0]["ref_number"]): {pol: summary(p, pol) for pol in ("reject", "keep")} for p in fixtures()}


if __name__ == "__main__":
    EXPECTED_TRIGGERS.write_text(json.dumps(current(), indent=1, sort_keys=True) + "\n")
    print(f"wrote {EXPECTED_TRIGGERS}")
