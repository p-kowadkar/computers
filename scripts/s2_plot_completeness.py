"""Plot completeness against amplitude from a `computers bench run` result file.

Needs the spike group (matplotlib): `uv run --group spike python scripts/s2_plot_completeness.py`.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import NullFormatter, ScalarFormatter


def main(result_path: str, out_path: str) -> None:
    result = json.loads(Path(result_path).read_text())
    by_family: dict[str, list[dict]] = defaultdict(list)
    for b in result["bins"]:
        by_family[b["family"]].append(b)
    fig, axes = plt.subplots(1, len(by_family), figsize=(3.2 * len(by_family), 3.4), sharey=True)
    for ax, (family, bins) in zip(axes, sorted(by_family.items()), strict=True):
        bins.sort(key=lambda b: b["amplitude"])
        x = [b["amplitude"] for b in bins]
        ax.errorbar(
            x, [b["fraction"] for b in bins],
            yerr=[[max(0.0, b["fraction"] - b["low"]) for b in bins], [max(0.0, b["high"] - b["fraction"]) for b in bins]],
            marker="o", ms=4, capsize=2, lw=1,
        )
        for level in (0.5, 0.9):
            ax.axhline(level, color="0.8", lw=0.8, zorder=0)
        ax.set_xscale("log")
        ax.xaxis.set_major_formatter(ScalarFormatter())
        ax.xaxis.set_minor_formatter(NullFormatter())
        ax.set_xticks([round(v, 2) for v in x[::2]])
        ax.set_title(family)
        ax.set_xlabel("mag per century" if family == "linear" else "amplitude (mag)")
        ax.set_ylim(-0.03, 1.03)
    axes[0].set_ylabel("recovered fraction")
    fig.suptitle(
        f"{result['detector']}, {result['split']} split: {result['hosts']} hosts, "
        f"{result['injections']} injections (expected-missed signals left out)",
        fontsize=9,
    )
    fig.tight_layout()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=130)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main(*(sys.argv[1:3] or ["data/reports/bench-dev.json", "docs/decisions/assets/s2/completeness_trivial_dev.png"]))
