from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .client import FetchError, SafeStopError
from .models import MonitoredPerformance, Seat
from .notification import NotificationError, format_notification
from .seats import SeatDataError, adjacent_groups, diagnose_requested_seats
from .state import StateStore


LOG = logging.getLogger(__name__)


def _format_seats(seats: set[Seat] | frozenset[Seat]) -> str:
    return ", ".join(f"{seat.row}{seat.number}" for seat in sorted(seats)) or "none"


def _format_status_counts(counts: dict[int | None, int]) -> str:
    parts = [
        f"{status}: {count}"
        for status, count in sorted(
            (status, count) for status, count in counts.items() if status is not None
        )
    ]
    if None in counts:
        parts.append(f"invalid: {counts[None]}")
    return "{" + ", ".join(parts) + "}"


class SeatClient(Protocol):
    def fetch(self, performance_id: int) -> Any: ...


class Notifier(Protocol):
    def send(self, message: str) -> None: ...


@dataclass
class SweepResult:
    checked: int = 0
    alerts: int = 0
    errors: int = 0
    safe_stop: bool = False


class Watcher:
    def __init__(
        self,
        *,
        client: SeatClient,
        notifier: Notifier | None,
        state: StateStore,
        requested_seats: frozenset[Seat],
        screen_id: int = 2,
        dry_run: bool = False,
        request_delay_seconds: float = 1.5,
        sleep=time.sleep,
    ) -> None:
        self.client = client
        self.notifier = notifier
        self.state = state
        self.requested_seats = requested_seats
        self.screen_id = screen_id
        self.dry_run = dry_run
        self.request_delay_seconds = request_delay_seconds
        self.sleep = sleep

    def run(self, performances: list[MonitoredPerformance]) -> SweepResult:
        result = SweepResult()
        for index, target in enumerate(performances):
            if index:
                self.sleep(self.request_delay_seconds)
            performance = target.performance
            LOG.info(
                "Checking %s — %s (performance %s)",
                target.production.name,
                performance.label,
                performance.id,
            )
            try:
                payload = self.client.fetch(performance.id)
            except SafeStopError as exc:
                LOG.error("Safe stop for performance %s: %s", performance.id, exc)
                result.safe_stop = True
                result.errors += 1
                break
            except FetchError as exc:
                LOG.error("Could not check performance %s: %s", performance.id, exc)
                result.errors += 1
                continue

            try:
                diagnostics = diagnose_requested_seats(
                    payload, self.requested_seats, self.screen_id
                )
            except SeatDataError as exc:
                LOG.error("Invalid seat data for performance %s: %s", performance.id, exc)
                result.errors += 1
                continue
            result.checked += 1
            previous = set() if self.dry_run else self.state.available_for(target.state_key)
            observed_available = set(diagnostics.available)
            newly_available = observed_available - previous
            groups = adjacent_groups(observed_available)

            LOG.info(
                "%s | %s | requested found %d/%d | statuses: %s | available: %s",
                target.production.name,
                performance.label,
                diagnostics.found_count,
                len(self.requested_seats),
                _format_status_counts(diagnostics.status_counts),
                _format_seats(observed_available),
            )
            if diagnostics.missing:
                LOG.warning(
                    "%s | %s | missing requested seats: %s",
                    target.production.name,
                    performance.label,
                    _format_seats(diagnostics.missing),
                )

            LOG.info(
                "Performance %s: %d requested seat(s) available; %d new; adjacent=%s",
                performance.id,
                len(observed_available),
                len(newly_available),
                bool(groups),
            )

            if newly_available:
                message = format_notification(target, newly_available, observed_available)
                if self.dry_run:
                    LOG.info("DRY RUN notification preview:\n%s", message)
                    result.alerts += 1
                else:
                    assert self.notifier is not None
                    try:
                        self.notifier.send(message)
                    except NotificationError as exc:
                        # Retain the old state so the transition is retried next run.
                        LOG.error("Notification failed for performance %s: %s", performance.id, exc)
                        result.errors += 1
                        continue
                    result.alerts += 1

            if not self.dry_run:
                # A missing record is unknown, not evidence that a previously
                # available seat became unavailable. Retain that seat's prior
                # state until it is observed again with an explicit status.
                retained_missing = previous & set(diagnostics.missing)
                self.state.update(
                    target.state_key, observed_available | retained_missing
                )
                self.state.save()
        return result
