"""Command-line entry point. Subcommands are added sprint by sprint."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from computers import __version__

SCHEMA_LOCK = Path("docs/schemas.lock.json")
SCHEMA_DOCS = Path("docs/schemas.md")


def _git_head_text(path: Path) -> str | None:
    """Contents of ``path`` at git HEAD, or None when there is no HEAD or no such file."""
    try:
        out = subprocess.run(
            ["git", "show", f"HEAD:{path.as_posix()}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return out.stdout if out.returncode == 0 else None


def cmd_schema(args: argparse.Namespace) -> int:
    from computers.data import schemas

    root = Path(args.root)
    lock_path, docs_path = root / SCHEMA_LOCK, root / SCHEMA_DOCS

    if args.action == "lock":
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(schemas.lock_text(), encoding="utf-8")
        print(f"wrote {lock_path}")
        return 0
    if args.action == "docs":
        docs_path.parent.mkdir(parents=True, exist_ok=True)
        docs_path.write_text(schemas.docs_text(), encoding="utf-8")
        print(f"wrote {docs_path}")
        return 0

    # check
    problems: list[str] = []
    if not lock_path.exists() or lock_path.read_text(encoding="utf-8") != schemas.lock_text():
        problems.append(f"{SCHEMA_LOCK} is stale: run `computers schema lock`")
    if not docs_path.exists() or docs_path.read_text(encoding="utf-8") != schemas.docs_text():
        problems.append(f"{SCHEMA_DOCS} is stale: run `computers schema docs`")
    head = _git_head_text(SCHEMA_LOCK)
    if head:
        try:
            problems += schemas.bump_violations(json.loads(head), schemas.lock_dict())
        except ValueError:
            problems.append(f"{SCHEMA_LOCK} at git HEAD is not valid JSON")
    for p in problems:
        print(f"schema check: {p}", file=sys.stderr)
    if not problems:
        print("schema check: ok")
    return 1 if problems else 0


API_ROOT = "https://api.starglass.cfa.harvard.edu"
PROBE_TIMEOUT_SECONDS = 20


def _http_get(url: str, timeout: float) -> tuple[int, bytes]:
    """The one place the probe touches the network. Tests replace it."""
    import requests

    resp = requests.get(url, headers={"Accept": "application/json"}, timeout=timeout)
    return resp.status_code, resp.content


def _client_version() -> str:
    import requests

    return f"computers/{__version__} requests/{requests.__version__}"


def cmd_snapshot(args: argparse.Namespace) -> int:
    from computers.data.snapshot import Request, SnapshotError, SnapshotStore

    store = SnapshotStore(Path(args.data_dir), client_version=_client_version())

    if args.action == "probe":
        request = Request("dasch-dr7", "/health", None, method="GET", api_base="public")
        try:
            status, body = _http_get(f"{API_ROOT}/public/health", PROBE_TIMEOUT_SECONDS)
            row = store.put(request, body, status=status)
        except SnapshotError as exc:
            print(f"snapshot probe failed: {exc}", file=sys.stderr)
            return 1
        except OSError as exc:  # includes requests' connection and timeout errors
            print(f"snapshot probe failed: cannot reach the API ({exc})", file=sys.stderr)
            return 1
        print(json.dumps(json.loads(row.to_line()), indent=2))
        return 0

    if args.action == "verify":
        report = store.verify()
        for line in report.lines():
            print(line, file=sys.stderr if not report.ok else sys.stdout)
        print("snapshot verify: ok" if report.ok else "snapshot verify: PROBLEMS FOUND")
        return 0 if report.ok else 1

    if args.action == "digest":
        print(store.digest())
        return 0

    # status
    status = store.status()
    if not status:
        print("no snapshots yet")
        return 0
    print(f"{'source':<20} {'rows':>7} {'requests':>9} {'bytes':>12} {'bad lines':>10}")
    for source, s in status.items():
        print(f"{source:<20} {s['rows']:>7} {s['requests']:>9} {s['bytes']:>12} {s['bad_lines']:>10}")
    return 0


def cmd_thresholds(args: argparse.Namespace) -> int:
    from computers import thresholds

    root = Path(args.root)
    lock_path = root / thresholds.LOCK_PATH
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"thresholds check: cannot read {thresholds.LOCK_PATH}: {exc}", file=sys.stderr)
        return 1
    found = thresholds.problems(lock, root)
    head = _git_head_text(thresholds.LOCK_PATH)
    if head:
        try:
            found += thresholds.change_violations(json.loads(head), lock)
        except ValueError:
            found.append(f"{thresholds.LOCK_PATH} at git HEAD is not valid JSON")
    for p in found:
        print(f"thresholds check: {p}", file=sys.stderr)
    if not found:
        print("thresholds check: ok")
    return 1 if found else 0


def cmd_pull(args: argparse.Namespace) -> int:
    from computers.data.pull import STAGES, run_pull
    from computers.data.tiles import TILES

    tiles = list(TILES) if args.tile == "all" else [args.tile]
    stages = list(STAGES) if args.stage == "all" else [args.stage]
    return run_pull(
        tiles,
        stages,
        Path(args.data_dir),
        pilot=args.pilot,
        max_stars=args.max_stars,
        concurrency=args.concurrency,
    )


def cmd_vsx(args: argparse.Namespace) -> int:
    from computers.data.tiles import TILES
    from computers.data.vsx import run_vsx_pull

    tiles = list(TILES) if args.tile == "all" else [args.tile]
    return run_vsx_pull(tiles, Path(args.data_dir))


def cmd_bench(args: argparse.Namespace) -> int:
    from computers.bench import l1_report, leavitt, run, tabby_check, trigger_bench

    data_dir = Path(args.data_dir)
    try:
        if args.smoke:
            result = run.run_bench(data_dir, split="dev", smoke=True)
            print(f"wrote {run.write_result(data_dir, result, 'bench-smoke.json')}")
            return 0
        if args.action == "build":
            manifest = leavitt.build_set(data_dir)
            print(f"built Leavitt Set {manifest['version']} in {leavitt.set_dir(data_dir)}")
            print(f"test split {manifest['test_split_sha256'][:16]}: {manifest['test_stars']} stars, "
                  f"{manifest['test_excluded_plates']} shared plates dropped")
            return 0
        if args.action == "check":
            problems = leavitt.check_set(data_dir)
            print("\n".join(problems) if problems else "bench check: ok")
            return 1 if problems else 0
        if args.action == "freeze":
            if not args.decision:
                print("freeze needs --decision <file under docs/decisions/> recording your approval")
                return 2
            print(f"wrote {leavitt.freeze_test_split(data_dir, decision=args.decision)}")
            return 0
        if args.action == "l1":
            result = l1_report.run_l1_report(data_dir)
            print(l1_report.format_l1_report(result))
            print(f"wrote {run.write_result(data_dir, result, 'bench-l1.json')}")
            return 0
        if args.action == "triggers":
            result = trigger_bench.run_trigger_report(data_dir, sweeps=not args.quick, ablations=not args.quick)
            print(trigger_bench.format_trigger_report(result))
            print(f"wrote {run.write_result(data_dir, result, 'bench-triggers.json')}")
            return 0
        if args.action == "l2":
            from computers.bench import l2_bench

            print(l2_bench.run_l2_bench(data_dir, reuse=args.reuse))
            return 0
        if args.action == "tabby":
            out = {la: tabby_check.run_tabby_check(data_dir, look_alikes=la) for la in tabby_check.LOOK_ALIKES}
            print(f"wrote {run.write_result(data_dir, out, 'bench-tabby.json')}")
            return 0
        if args.action == "run":
            result = run.run_bench(data_dir, split=args.split)
            print(f"wrote {run.write_result(data_dir, result, f'bench-{args.split}.json')}")
            return 0
    except leavitt.BuildError as exc:
        print(f"bench: {exc}")
        return 1
    print("choose an action (build, check, freeze, run, l1, triggers, l2, tabby) or --smoke")
    return 2


def cmd_l1(args: argparse.Namespace) -> int:
    from computers.data.tiles import TILES
    from computers.l1.candidates import run_l1

    tiles = list(TILES) if args.tile == "all" else [args.tile]
    code = 0
    for tile in tiles:
        code |= run_l1(Path(args.data_dir), tile, meteor_policy=args.meteor_policy)
    return code


def cmd_l2(args: argparse.Namespace) -> int:
    from computers.data.tiles import TILES
    from computers.l2.run import run_l2

    tiles = list(TILES) if args.tile == "all" else [args.tile]
    return run_l2(Path(args.data_dir), tiles, meteor_policy=args.meteor_policy)


def cmd_ml(args: argparse.Namespace) -> int:
    from computers.l1.candidates import TEST_TILES, check_test_tile_allowed
    from computers.ml import datacard, dataset, sequences

    data_dir = Path(args.data_dir)
    if args.action == "build":
        tiles = ["m44", "tcrb"] if args.tile == "dev" else [args.tile]
        by_tile = {}
        for tile in tiles:
            if tile in TEST_TILES:
                why = check_test_tile_allowed()
                if why:
                    print(f"ml: {tile} is the held-out test tile and is not built: {why}")
                    return 1
            stars = dataset.build_tile(data_dir, tile)
            out = data_dir / "sequences" / f"v{sequences.SPEC_VERSION}"
            dataset.save_stars(stars, out / f"tile={tile}.npz")
            (out / "sequence_spec.json").write_text(sequences.spec_json() + "\n")
            by_tile[tile] = stars
            print(f"{tile}: {len(stars)} stars, {sum(len(s) for s in stars):,} rows -> {out / f'tile={tile}.npz'}")
        if args.card:
            from computers.data.snapshot import SnapshotStore

            text = datacard.card_text(by_tile, digest=SnapshotStore(data_dir).digest())
            Path(args.card).write_text(text)
            print(f"wrote {args.card}")
        return 0
    if args.action in ("train", "eval"):
        try:
            from computers.ml.lc_encdec import report, train
        except ModuleNotFoundError as exc:
            print(f"ml: {exc}. Install the group with `uv sync --group ml`.")
            return 1
        if args.action == "train":
            if not args.config:
                print("ml train needs --config <file>")
                return 2
            cfg = train.load_config(args.config)
            print(f"run directory: {train.run_dir_for(cfg)}")
            train.train(cfg, resume=args.resume)
            return 0
        if not args.run:
            print("ml eval needs --run <run directory>")
            return 2
        result = report.evaluate_run(args.run)
        print(report.format_report(result))
        return 0
    return 2


def cmd_crew(args: argparse.Namespace) -> int:
    try:
        from computers.crew import llm as L
        from computers.crew import spike
    except ModuleNotFoundError as exc:
        print(f"crew: {exc}. Install the group with `uv sync --group crew`.")
        return 1
    import os
    import time

    data_dir = Path(args.data_dir)
    cfg = L.ModelConfig(args.model, args.provider, args.price_in, args.price_out, max_tokens=args.max_tokens,
                        reasoning={"enabled": False} if args.reasoning == "off" else None)
    cache_dir = Path(args.cache_dir) if args.cache_dir else data_dir / "llm_cache"
    if not os.environ.get(L.KEY_VAR):
        print(f"crew: {L.KEY_VAR} is not set. Set it in the environment and run again (a fully cached run needs no key, and this one is not cached yet).")
        return 1
    packets, reference = spike.select_packets(data_dir, args.n)
    print(f"model {cfg.model} on provider {cfg.provider}; {len(packets)} packets (plus {'1 reference' if reference else 'no reference'}), two paths; "
          f"cap ${L.RunBudget().cap_usd:.2f} per path; cache {cache_dir}")  # fmt: skip
    if not args.yes and input("Proceed with paid calls? [y/N] ").strip().lower() != "y":
        print("not run")
        return 1
    stamp = time.strftime("%Y%m%d-%H%M%S")
    logs: list[Path] = []

    def gateway_for(path: str) -> L.Gateway:
        log = data_dir / "llm_log" / f"spike-{path}-{stamp}.jsonl"
        logs.append(log)
        return L.Gateway(cfg, cache_dir=cache_dir, log_path=log, run_id=f"spike-{path}-{stamp}")

    report = spike.run_paths(packets, gateway_for)
    if reference is not None:
        report["reference"] = spike.run_paths([reference], gateway_for)
    out = data_dir / "reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / "crew-spike.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    text = spike.format_report(report)
    if "reference" in report:
        text += "\n\nReference (T CrB 1946 eruption, not counted):\n" + spike.format_report(report["reference"])
    (out / "crew-spike.txt").write_text(text + "\n")
    print(text)
    if args.fixtures:
        spike.save_packets([*packets, *([reference] if reference else [])], Path(args.fixtures) / "packets")
        n = spike.write_fixtures(cache_dir, logs, args.fixtures)
        print(f"wrote {n} cached responses and the packets to {args.fixtures}")
    return 0


def cmd_plates(args: argparse.Namespace) -> int:
    from computers.data import plates

    print(plates.report_text(plates.load_report(Path(args.data_dir))))
    return 0


def _concurrency(text: str) -> int:
    n = int(text)
    if not 1 <= n <= 4:
        raise argparse.ArgumentTypeError("concurrency must be between 1 and 4")
    return n


def _positive(text: str) -> int:
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="computers",
        description="Patrol Harvard plate lightcurves for stars that faded or flared.",
    )
    parser.add_argument("--version", action="version", version=f"computers {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_schema = sub.add_parser("schema", help="table schemas: lock, docs, check")
    p_schema.add_argument("action", choices=["lock", "docs", "check"])
    p_schema.add_argument("--root", default=".", help="repo root (default: current directory)")
    p_schema.set_defaults(func=cmd_schema)

    p_thr = sub.add_parser("thresholds", help="check thresholds against their decision records")
    p_thr.add_argument("action", choices=["check"])
    p_thr.add_argument("--root", default=".", help="repo root (default: current directory)")
    p_thr.set_defaults(func=cmd_thresholds)

    p_pull = sub.add_parser(
        "pull", help="pull a tile from DASCH into the snapshot store and silver tables"
    )
    p_pull.add_argument("--tile", required=True, choices=["m44", "tcrb", "tabby", "all"])
    p_pull.add_argument(
        "--stage",
        default="all",
        choices=["catalog", "exposures", "lightcurves", "neighbours", "all"],
        help="which stage to run (default: all, in order)",
    )
    p_pull.add_argument(
        "--pilot",
        action="store_true",
        help="lightcurves for a subset stratified by magnitude when the tile has more than --max-stars",
    )
    p_pull.add_argument("--max-stars", type=_positive, default=2000)
    p_pull.add_argument(
        "--concurrency", type=_concurrency, default=3, help="requests in flight, 1 to 4 (default 3)"
    )
    p_pull.add_argument("--data-dir", default="data", help="data directory (default: ./data)")
    p_pull.set_defaults(func=cmd_pull)

    p_vsx = sub.add_parser("vsx", help="known variables (VSX) for a tile and the cross-match")
    p_vsx.add_argument("action", choices=["pull"])
    p_vsx.add_argument("--tile", required=True, choices=["m44", "tcrb", "tabby", "all"])
    p_vsx.add_argument("--data-dir", default="data", help="data directory (default: ./data)")
    p_vsx.set_defaults(func=cmd_vsx)

    p_bench = sub.add_parser("bench", help="the Leavitt Set: build, check, freeze, run, l1, triggers, l2, tabby, --smoke")
    p_bench.add_argument("action", nargs="?", choices=["build", "check", "freeze", "run", "l1", "triggers", "l2", "tabby"])
    p_bench.add_argument("--reuse", action="store_true", help="for `l2`: reuse the saved dev pass")
    p_bench.add_argument("--smoke", action="store_true", help="a 2-minute subset on the dev split")
    p_bench.add_argument("--split", choices=["dev", "test"], default="dev", help="for `run` (test needs a frozen set)")
    p_bench.add_argument("--quick", action="store_true", help="for `triggers`: skip the sweeps and ablations")
    p_bench.add_argument("--decision", help="for `freeze`: the decision file that records your approval")
    p_bench.add_argument("--data-dir", default="data", help="data directory (default: ./data)")
    p_bench.set_defaults(func=cmd_bench)

    p_l1 = sub.add_parser("l1", help="run L1 (cleaning, detrending, triggers) over a tile")
    p_l1.add_argument("action", choices=["run"])
    p_l1.add_argument("--tile", required=True, choices=["m44", "tcrb", "tabby", "all"])
    p_l1.add_argument("--meteor-policy", choices=["reject", "keep"], default="reject")
    p_l1.add_argument("--data-dir", default="data", help="data directory (default: ./data)")
    p_l1.set_defaults(func=cmd_l1)

    p_l2 = sub.add_parser("l2", help="score L1 candidates with the ranker and choose the stars for L3")
    p_l2.add_argument("action", choices=["run"])
    p_l2.add_argument("--tile", required=True, choices=["m44", "tcrb", "tabby", "all"])
    p_l2.add_argument("--meteor-policy", choices=["reject", "keep"], default="reject")
    p_l2.add_argument("--data-dir", default="data", help="data directory (default: ./data)")
    p_l2.set_defaults(func=cmd_l2)

    p_ml = sub.add_parser("ml", help="E1 lightcurve model: build the sequence dataset")
    p_ml.add_argument("action", choices=["build", "train", "eval"])
    p_ml.add_argument("--config", help="for `train`: the run's config file")
    p_ml.add_argument("--resume", action="store_true", help="for `train`: continue from the last checkpoint")
    p_ml.add_argument("--run", help="for `eval`: a run directory")
    p_ml.add_argument("--tile", default="dev", choices=["dev", "m44", "tcrb", "tabby"])
    p_ml.add_argument("--card", help="write the data card to this path")
    p_ml.add_argument("--data-dir", default="data", help="data directory (default: ./data)")
    p_ml.set_defaults(func=cmd_ml)

    p_crew = sub.add_parser("crew", help="the crew spike: Maury on NOOA and on the in-house base, through the gateway")
    p_crew.add_argument("action", choices=["spike"])
    p_crew.add_argument("--model", default="anthropic/claude-haiku-5.5")
    p_crew.add_argument("--provider", default="anthropic", help="the OpenRouter provider tag to pin")
    p_crew.add_argument("--price-in", type=float, default=0.10, help="US dollars per million input tokens, used if a reply reports no cost")
    p_crew.add_argument("--price-out", type=float, default=0.50)
    p_crew.add_argument("--n", type=int, default=10)
    p_crew.add_argument("--max-tokens", type=int, default=4000, help="reply limit; the first live run used 1500 and some replies hit it")
    p_crew.add_argument("--reasoning", choices=["default", "off"], default="default", help="off sends reasoning {enabled: false} to OpenRouter")
    p_crew.add_argument("--cache-dir", help="response cache (default data/llm_cache)")
    p_crew.add_argument("--fixtures", help="copy the packets and the responses of this run here (tests/golden/llm)")
    p_crew.add_argument("--yes", action="store_true", help="do not ask before the paid calls")
    p_crew.add_argument("--data-dir", default="data", help="data directory (default: ./data)")
    p_crew.set_defaults(func=cmd_crew)

    p_plates = sub.add_parser("plates", help="plates shared between tiles, from the exposures tables")
    p_plates.add_argument("--data-dir", default="data", help="data directory (default: ./data)")
    p_plates.set_defaults(func=cmd_plates)

    p_snap = sub.add_parser("snapshot", help="raw snapshot store: probe, verify, digest, status")
    p_snap.add_argument("action", choices=["probe", "verify", "digest", "status"])
    p_snap.add_argument("--data-dir", default="data", help="data directory (default: ./data)")
    p_snap.set_defaults(func=cmd_snapshot)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    return int(func(args))


if __name__ == "__main__":
    raise SystemExit(main())
