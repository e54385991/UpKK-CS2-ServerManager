"""Command-line entry for isolated performance measurement."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Sequence

from scripts.perf.catalog_bytes import catalog_byte_report
from scripts.perf.download_bench import compare_chunks
from scripts.perf.env import IsolatedPorts, apply_isolated_env, isolated_environ
from scripts.perf.profiles import FLEETS, MARKETS, MEASURES
from scripts.perf.report import empty_report, write_report
from scripts.perf.stubs import StubServer

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORTS = PROJECT_ROOT / "reports" / "perf"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Isolated performance harness")
    parser.add_argument(
        "command",
        choices=("catalog-bytes", "download-bench", "stub", "seed", "measure-api", "report"),
    )
    parser.add_argument("--fleet", default="fleet-10", choices=sorted(FLEETS))
    parser.add_argument("--market", default="market-100", choices=sorted(MARKETS))
    parser.add_argument("--history", default="daily", choices=("empty", "daily", "max"))
    parser.add_argument("--mode", default="smoke", choices=sorted(MEASURES))
    parser.add_argument("--out", default="")
    parser.add_argument("--manifest", default="")
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
        return asyncio.run(_run_measure(args))
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
    report["download"] = {"chunks": await compare_chunks(), "prune_scans": 2}
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
    measured = await run_api_rounds(
        tokens=tokens,
        online_users=FLEETS[args.fleet].online_users,
        mode=args.mode,
        transport=ASGITransport(app=app),
        base_url="http://perf.local",
    )
    report = empty_report(profile=args.fleet, mode=args.mode, cwd=PROJECT_ROOT)
    report["catalog_bytes"] = catalog_byte_report()
    report["api"] = measured
    report["backend"] = measured.get("backend", {})
    path = _output_path(args, f"api-{args.fleet}-{args.mode}.json")
    write_report(path, report)
    print(path)
    return 0


def _write_shell_report(args: argparse.Namespace) -> int:
    report = empty_report(profile=args.fleet, mode=args.mode, cwd=PROJECT_ROOT)
    report["catalog_bytes"] = catalog_byte_report()
    path = _output_path(args, f"shell-{args.fleet}.json")
    write_report(path, report)
    print(path)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    apply_isolated_env(isolated_environ(IsolatedPorts()))
    return dispatch(args)


if __name__ == "__main__":
    raise SystemExit(main())
