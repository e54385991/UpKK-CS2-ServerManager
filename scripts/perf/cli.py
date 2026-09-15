"""Command-line entry for isolated performance measurement."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Sequence

from scripts.perf.catalog_bytes import catalog_byte_report
from scripts.perf.download_bench import compare_chunks
from scripts.perf.env import apply_isolated_env, isolated_environ, ports_from_environ
from scripts.perf.profiles import CACHE_PRUNE_SCANS_TODAY, FLEETS, MARKETS, MEASURES
from scripts.perf.report import empty_report, write_report
from scripts.perf.stubs import StubServer

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORTS = PROJECT_ROOT / "reports" / "perf"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Isolated performance harness")
    parser.add_argument(
        "command",
        choices=(
            "soak",
            "catalog-bytes",
            "download-bench",
            "stub",
            "seed",
            "measure-api",
            "measure-realtime",
            "fingerprint",
            "explain-indexes",
            "report",
        ),
    )
    parser.add_argument(
        "--with-index-candidates",
        action="store_true",
        help=(
            "measure-api only: CREATE the marketplace btree candidates, "
            "measure, then DROP them. Isolated target required."
        ),
    )
    parser.add_argument("--fleet", default="fleet-10", choices=sorted(FLEETS))
    parser.add_argument("--market", default="market-100", choices=sorted(MARKETS))
    parser.add_argument("--history", default="daily", choices=("empty", "daily", "max"))
    parser.add_argument("--mode", default="smoke", choices=sorted(MEASURES))
    parser.add_argument(
        "--route",
        action="append",
        dest="routes",
        choices=("inbox", "overview", "market", "servers"),
        help="Repeat to isolate one or more routes. Default is the mixed four-route load.",
    )
    parser.add_argument("--out", default="")
    parser.add_argument("--manifest", default="")
    parser.add_argument(
        "--seconds",
        type=int,
        default=0,
        help="Soak duration. 0 uses the profile soak_seconds (3600 for baseline).",
    )
    parser.add_argument(
        "--paced",
        action="store_true",
        help="Mixed/single-route load at 80 percent of error-free discovery (or --arrival-from).",
    )
    parser.add_argument(
        "--arrival-from",
        default="",
        help="Reuse arrival targets from a previous upkk-isolated-perf report.",
    )
    parser.add_argument(
        "--eval-from",
        default="",
        help="explain-indexes: reuse a previous indexes payload instead of connecting.",
    )
    parser.add_argument(
        "--http-before",
        default="",
        help="explain-indexes: market report measured without candidate indexes.",
    )
    parser.add_argument(
        "--http-after",
        default="",
        help="explain-indexes: market report measured with --with-index-candidates.",
    )
    parser.add_argument(
        "--sessions",
        type=int,
        default=0,
        help="Realtime SSE sessions. 0 uses the fleet online_users count.",
    )
    return parser.parse_args(argv)


def dispatch(args: argparse.Namespace) -> int:
    if args.command == "catalog-bytes":
        return _write_catalog(args)
    if args.command == "download-bench":
        return asyncio.run(_write_download(args))
    if args.command == "stub":
        return _run_stub()
    if args.command == "seed":
        return asyncio.run(_run_seed(args))
    if args.command == "measure-api":
        return asyncio.run(_run_measure_guarded(args))
    if args.command == "soak":
        return asyncio.run(_run_soak(args))
    if args.command == "explain-indexes":
        return asyncio.run(_write_index_eval(args))
    if args.command == "fingerprint":
        return _write_fingerprint(args)
    if args.command == "measure-realtime":
        return asyncio.run(_run_realtime(args))
    return _write_shell_report(args)


def _output_path(args: argparse.Namespace, default_name: str) -> Path:
    if args.out:
        return Path(args.out)
    return REPORTS / "raw" / default_name


def _write_catalog(args: argparse.Namespace) -> int:
    report = empty_report(profile="catalog", mode=args.mode, cwd=PROJECT_ROOT)
    report["catalog_bytes"] = catalog_byte_report()
    path = _output_path(args, "catalog-bytes.json")
    write_report(path, report)
    print(path)
    return 0


async def _write_download(args: argparse.Namespace) -> int:
    report = empty_report(profile="download", mode=args.mode, cwd=PROJECT_ROOT)
    report["download"] = {
        "chunks": await compare_chunks(),
        "prune_scans": CACHE_PRUNE_SCANS_TODAY,
    }
    path = _output_path(args, "download-bench.json")
    write_report(path, report)
    print(path)
    return 0


def _run_stub() -> int:
    server = StubServer()
    server.start()
    print(server.origin)
    try:
        threading_event().wait()
    except KeyboardInterrupt:
        server.stop()
    return 0


def threading_event() -> Any:
    import threading

    return threading.Event()


async def _run_seed(args: argparse.Namespace) -> int:
    from modules.config import get_settings
    from scripts.perf.seed import run_seed

    settings = get_settings()
    manifest = await run_seed(
        fleet_name=args.fleet,
        market_name=args.market,
        history=args.history,
        settings=settings,
    )
    path = Path(args.manifest) if args.manifest else REPORTS / "raw" / "seed-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(path)
    return 0


async def _write_index_eval(args: argparse.Namespace) -> int:
    from scripts.perf.index_eval import attach_http_from_reports, evaluate_market_indexes
    from scripts.perf.report import load_report

    if args.eval_from:
        payload = load_report(Path(args.eval_from))
        evaluation = payload.get("indexes", payload)
        if not isinstance(evaluation, dict):
            raise SystemExit("eval-from is missing an indexes object")
        evaluation = dict(evaluation)
    else:
        evaluation = await evaluate_market_indexes()
    if bool(args.http_before) != bool(args.http_after):
        raise SystemExit("--http-before and --http-after must be used together")
    if args.http_before and args.http_after:
        evaluation = attach_http_from_reports(
            evaluation,
            load_report(Path(args.http_before)),
            load_report(Path(args.http_after)),
        )
    report = empty_report(profile="indexes", mode=args.mode, cwd=PROJECT_ROOT)
    report["indexes"] = evaluation
    path = _output_path(args, "explain-indexes.json")
    write_report(path, report)
    print(path)
    return 0


async def _run_measure_guarded(args: argparse.Namespace) -> int:
    if not args.with_index_candidates:
        return await _run_measure(args)
    from scripts.perf.index_eval import commit_candidate_indexes, drop_candidate_indexes

    await commit_candidate_indexes()
    try:
        return await _run_measure(args)
    finally:
        await drop_candidate_indexes()


async def _run_measure(args: argparse.Namespace) -> int:
    from httpx import ASGITransport

    from api.application import create_app
    from modules.config import get_settings
    from scripts.perf.api_measure import run_api_rounds
    from scripts.perf.seed import run_seed

    settings = get_settings()
    manifest_path = Path(args.manifest) if args.manifest else REPORTS / "raw" / "seed-manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = await run_seed(
            fleet_name=args.fleet,
            market_name=args.market,
            history=args.history,
            settings=settings,
        )
    app = create_app(lifespan=None)
    tokens = manifest["tokens"]
    arrival_targets = None
    if args.arrival_from:
        from scripts.perf.arrival import load_arrival_targets
        from scripts.perf.report import load_report

        arrival_targets = load_arrival_targets(load_report(Path(args.arrival_from)))
        if arrival_targets is None:
            raise SystemExit("arrival report is missing arrival.targets")
    measured = await run_api_rounds(
        tokens=tokens,
        online_users=FLEETS[args.fleet].online_users,
        mode=args.mode,
        transport=ASGITransport(app=app),
        base_url="http://perf.local",
        routes=args.routes,
        paced=args.paced,
        arrival_targets=arrival_targets,
    )
    report = empty_report(profile=args.fleet, mode=args.mode, cwd=PROJECT_ROOT)
    report["catalog_bytes"] = catalog_byte_report()
    report["api"] = measured
    report["backend"] = measured.get("backend", {})
    report["arrival"] = measured.get("arrival", {})
    report["segments"] = measured.get("segments", {})
    isolated = "-".join(args.routes) if args.routes else args.mode
    default_name = (
        f"api-{args.fleet}-{args.mode}.json"
        if not args.routes
        else f"api-{args.fleet}-{args.mode}-{isolated}.json"
    )
    if args.with_index_candidates:
        report["indexes"] = {
            "candidates_present_during_measure": True,
            "indexes_submitted": False,
        }
    path = _output_path(args, default_name)
    write_report(path, report)
    print(path)
    return 0


async def _run_soak(args: argparse.Namespace) -> int:
    from httpx import ASGITransport

    from api.application import create_app
    from modules.config import get_settings
    from scripts.perf.api_measure import run_soak
    from scripts.perf.seed import run_seed

    seconds = args.seconds or MEASURES[args.mode].soak_seconds
    if seconds <= 0:
        raise SystemExit("soak needs --seconds or --mode baseline")
    settings = get_settings()
    manifest_path = Path(args.manifest) if args.manifest else REPORTS / "raw" / "seed-manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = await run_seed(
            fleet_name=args.fleet,
            market_name=args.market,
            history=args.history,
            settings=settings,
        )
    app = create_app(lifespan=None)
    measured = await run_soak(
        tokens=manifest["tokens"],
        online_users=FLEETS[args.fleet].online_users,
        transport=ASGITransport(app=app),
        base_url="http://perf.local",
        routes=args.routes,
        seconds=seconds,
    )
    report = empty_report(profile=args.fleet, mode="soak", cwd=PROJECT_ROOT)
    report["catalog_bytes"] = catalog_byte_report()
    report["api"] = measured
    report["backend"] = measured.get("backend", {})
    path = _output_path(args, f"api-{args.fleet}-soak.json")
    write_report(path, report)
    print(path)
    return 0


def _write_fingerprint(args: argparse.Namespace) -> int:
    from scripts.perf.fingerprint import capture_fingerprint

    report = empty_report(profile="fingerprint", mode=args.mode, cwd=PROJECT_ROOT)
    report["fingerprint"] = capture_fingerprint(PROJECT_ROOT)
    path = _output_path(args, "round-fingerprint.json")
    write_report(path, report)
    print(path)
    return 0


async def _run_realtime(args: argparse.Namespace) -> int:
    from httpx import ASGITransport

    from api.application import create_app
    from modules.config import get_settings
    from scripts.perf.hub_inject import HubInboxLifecycle, pick_injectable_server_id
    from scripts.perf.realtime import run_realtime_rounds
    from scripts.perf.seed import run_seed

    settings = get_settings()
    manifest_path = Path(args.manifest) if args.manifest else REPORTS / "raw" / "seed-manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = await run_seed(
            fleet_name=args.fleet,
            market_name=args.market,
            history=args.history,
            settings=settings,
        )
    actor_id = int(manifest["users"]["admin"]["id"])
    server_id = pick_injectable_server_id(await _admin_server_ids(actor_id))
    sessions = args.sessions or FLEETS[args.fleet].online_users
    seconds = args.seconds or (15 if args.mode == "smoke" else 60)
    app = create_app(lifespan=None)
    measured = await run_realtime_rounds(
        token=manifest["tokens"]["admin"],
        sessions=sessions,
        seconds=seconds,
        transport=ASGITransport(app=app),
        base_url="http://perf.local",
        injector=HubInboxLifecycle(server_id, actor_id),
    )
    report = empty_report(profile=args.fleet, mode=args.mode, cwd=PROJECT_ROOT)
    report["realtime"] = measured
    path = _output_path(args, f"realtime-{args.fleet}-{args.mode}.json")
    write_report(path, report)
    print(path)
    return 0


async def _admin_server_ids(actor_id: int) -> list[int]:
    from sqlmodel import select

    from modules.database import async_session_maker
    from modules.models import Server

    async with async_session_maker() as session:
        result = await session.execute(select(Server.id).where(Server.user_id == actor_id))
        return [int(row) for row in result.scalars().all() if row is not None]


def _write_shell_report(args: argparse.Namespace) -> int:
    report = empty_report(profile=args.fleet, mode=args.mode, cwd=PROJECT_ROOT)
    report["catalog_bytes"] = catalog_byte_report()
    path = _output_path(args, f"shell-{args.fleet}.json")
    write_report(path, report)
    print(path)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    apply_isolated_env(isolated_environ(ports_from_environ()))
    return dispatch(args)


if __name__ == "__main__":
    raise SystemExit(main())
