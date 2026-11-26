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
    from computers.bench import leavitt, run

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
        if args.action == "run":
            result = run.run_bench(data_dir, split=args.split)
            print(f"wrote {run.write_result(data_dir, result, f'bench-{args.split}.json')}")
            return 0
    except leavitt.BuildError as exc:
        print(f"bench: {exc}")
        return 1
    print("choose an action (build, check, freeze, run) or --smoke")
    return 2


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
        choices=["catalog", "exposures", "lightcurves", "all"],
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

    p_bench = sub.add_parser("bench", help="the Leavitt Set: build, check, freeze, run, --smoke")
    p_bench.add_argument("action", nargs="?", choices=["build", "check", "freeze", "run"])
    p_bench.add_argument("--smoke", action="store_true", help="a 2-minute subset on the dev split")
    p_bench.add_argument("--split", choices=["dev", "test"], default="dev", help="for `run` (test needs a frozen set)")
    p_bench.add_argument("--decision", help="for `freeze`: the decision file that records your approval")
    p_bench.add_argument("--data-dir", default="data", help="data directory (default: ./data)")
    p_bench.set_defaults(func=cmd_bench)

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
