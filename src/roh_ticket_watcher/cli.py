from __future__ import annotations

import argparse
import json
import logging
import math
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from filelock import FileLock, Timeout

from .client import FetchError, RBOSeatClient, SafeStopError
from .config import ConfigStore, ConfigurationError, env_float, env_int
from .discovery import (
    DiscoveryError,
    ProductionPageClient,
    format_discovery,
)
from .notification import NotificationError, TelegramNotifier
from .state import StateError, StateStore
from .watcher import Watcher


LOG = logging.getLogger(__name__)


class SampleClient:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def fetch(self, performance_id: int) -> Any:
        del performance_id
        return self.payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reusable, read-only Royal Ballet and Opera ticket watcher"
    )
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--log-level", default=None)
    commands = parser.add_subparsers(dest="command", required=True)

    watch = commands.add_parser("watch", help="Run one availability sweep and exit")
    watch.add_argument("--state-file", type=Path, default=None)
    watch.add_argument("--production", action="append", help="Limit to an active production slug")
    watch.add_argument("--performance-id", type=int, action="append", help="Limit by performance ID")
    watch.add_argument("--dry-run", action="store_true", help="Preview; no Telegram or state change")
    watch.add_argument("--sample-file", type=Path, help="Use offline seat JSON; implies --dry-run")
    watch.add_argument("--request-delay", type=float, default=None)

    discover = commands.add_parser(
        "discover-production", help="Display public performances; never change configuration"
    )
    discover.add_argument("url")

    add = commands.add_parser(
        "add-production", help="Discover, review, then interactively add a production"
    )
    add.add_argument("url")

    status = commands.add_parser(
        "set-production-status", help="Enable or disable a configured production"
    )
    status.add_argument("slug")
    status.add_argument("status", choices=("active", "inactive"))

    commands.add_parser("list-productions", help="List configured productions")
    commands.add_parser("test-telegram", help="Test Telegram only; do not contact RBO")
    return parser


def _load_sample(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Sample file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Invalid sample JSON in {path}: {exc}") from exc


def _network_settings() -> tuple[float, int]:
    return (
        env_float("REQUEST_TIMEOUT_SECONDS", 15.0, 1.0),
        env_int("REQUEST_RETRIES", 3, 1, 5),
    )


def _run_discovery(url: str, timeout: float, retries: int):
    result = ProductionPageClient(timeout_seconds=timeout, retries=retries).discover(url)
    print(format_discovery(result))
    return result


def _run_watch(args: argparse.Namespace, store: ConfigStore, timeout: float, retries: int) -> int:
    config = store.load()
    targets = config.monitored_performances()
    if args.production:
        wanted_slugs = set(args.production)
        configured_slugs = {production.slug for production in config.productions}
        missing = wanted_slugs - configured_slugs
        if missing:
            raise ConfigurationError(f"Unknown production slug(s): {sorted(missing)}")
        inactive = wanted_slugs - {
            production.slug for production in config.productions if production.active
        }
        if inactive:
            raise ConfigurationError(f"Requested production(s) are inactive: {sorted(inactive)}")
        targets = [target for target in targets if target.production.slug in wanted_slugs]
    if args.performance_id:
        wanted_ids = set(args.performance_id)
        targets = [target for target in targets if target.performance.id in wanted_ids]
        missing_ids = wanted_ids - {target.performance.id for target in targets}
        if missing_ids:
            raise ConfigurationError(f"Unknown active performance ID(s): {sorted(missing_ids)}")
    if not targets:
        LOG.info("No active performances are configured; nothing to check")
        return 0

    delay = (
        args.request_delay
        if args.request_delay is not None
        else env_float("REQUEST_DELAY_SECONDS", 1.5, 0.5)
    )
    if not math.isfinite(delay) or delay < 0.5:
        raise ConfigurationError("Request delay must be at least 0.5 seconds")
    state_path = args.state_file or Path(os.getenv("STATE_FILE", "data/state.json"))
    dry_run = args.dry_run or args.sample_file is not None
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not dry_run and (not token or not chat_id):
        raise ConfigurationError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required (or use --dry-run)"
        )

    state = StateStore(state_path)
    if not dry_run:
        state.load()
    client = (
        SampleClient(_load_sample(args.sample_file))
        if args.sample_file
        else RBOSeatClient(timeout_seconds=timeout, retries=retries)
    )
    notifier = None if dry_run else TelegramNotifier(token, chat_id, timeout_seconds=timeout)
    watcher = Watcher(
        client=client,
        notifier=notifier,
        state=state,
        requested_seats=config.requested_seats,
        screen_id=config.screen_id,
        dry_run=dry_run,
        request_delay_seconds=delay,
    )

    if dry_run:
        result = watcher.run(targets)
    else:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StateError(f"Cannot create state directory {state_path.parent}: {exc}") from exc
        lock = FileLock(f"{state_path}.lock")
        try:
            with lock.acquire(timeout=0):
                result = watcher.run(targets)
        except Timeout:
            LOG.warning("Another watcher sweep is already running; exiting without overlap")
            return 0

    LOG.info(
        "Sweep complete: checked=%d alerts=%d errors=%d safe_stop=%s",
        result.checked,
        result.alerts,
        result.errors,
        result.safe_stop,
    )
    if result.safe_stop:
        return 2
    return 1 if result.errors else 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    log_level = (args.log_level or os.getenv("LOG_LEVEL", "INFO")).upper()
    numeric_log_level = getattr(logging, log_level, None)
    if not isinstance(numeric_log_level, int):
        print(f"Invalid log level: {log_level}")
        return 2
    logging.basicConfig(
        level=numeric_log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        store = ConfigStore(args.config)
        timeout, retries = _network_settings()
        if args.command == "watch":
            return _run_watch(args, store, timeout, retries)
        if args.command == "discover-production":
            _run_discovery(args.url, timeout, retries)
            print("\nReview only: config.json was not changed.")
            return 0
        if args.command == "add-production":
            result = _run_discovery(args.url, timeout, retries)
            if not result.bookable:
                raise DiscoveryError("Nothing public/bookable can currently be added")
            print(
                f"\nOnly the {len(result.bookable)} public/bookable performance(s) above "
                "will be added as an active production."
            )
            confirmation = input("Type 'yes' to update config.json, or press Enter to cancel: ")
            if confirmation.strip().lower() != "yes":
                print("Cancelled; configuration was not changed.")
                return 0
            store.add_production(result.to_production())
            print(f"Added active production: {result.name} ({result.slug})")
            return 0
        if args.command == "set-production-status":
            active = args.status == "active"
            store.set_active(args.slug, active)
            print(f"{args.slug} is now {args.status}.")
            return 0
        if args.command == "list-productions":
            config = store.load()
            if not config.productions:
                print("No productions configured.")
            for production in config.productions:
                status = "active" if production.active else "inactive"
                print(
                    f"{production.slug}: {production.name} — {status} — "
                    f"{len(production.performances)} performance(s)"
                )
            return 0
        if args.command == "test-telegram":
            notifier = TelegramNotifier(
                os.getenv("TELEGRAM_BOT_TOKEN", ""),
                os.getenv("TELEGRAM_CHAT_ID", ""),
                timeout_seconds=timeout,
            )
            notifier.send(
                "✅ RBO ticket watcher test successful. No RBO request or booking action was made."
            )
            LOG.info("Telegram test completed; RBO was not contacted")
            return 0
        raise AssertionError(f"Unhandled command: {args.command}")
    except (
        ConfigurationError,
        DiscoveryError,
        FetchError,
        NotificationError,
        SafeStopError,
        StateError,
        ValueError,
    ) as exc:
        LOG.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
