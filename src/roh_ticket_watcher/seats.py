from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .models import Seat


class SeatDataError(ValueError):
    """The JSON did not contain the expected authoritative seat records."""


@dataclass(frozen=True)
class RequestedSeatDiagnostics:
    """Compact facts about requested seats found in one RBO response."""

    expected: frozenset[Seat]
    statuses_by_seat: dict[Seat, int | None]

    @property
    def found_count(self) -> int:
        return len(self.statuses_by_seat)

    @property
    def missing(self) -> frozenset[Seat]:
        return self.expected - self.statuses_by_seat.keys()

    @property
    def available(self) -> frozenset[Seat]:
        return frozenset(
            seat for seat, status in self.statuses_by_seat.items() if status == 0
        )

    @property
    def status_counts(self) -> dict[int | None, int]:
        return dict(Counter(self.statuses_by_seat.values()))


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _walk(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def diagnose_requested_seats(
    payload: Any, requested: frozenset[Seat], screen_id: int = 2
) -> RequestedSeatDiagnostics:
    """Inspect requested-seat records without retaining the raw response.

    The recursive walk tolerates the seat array moving within the response while
    still requiring all four authoritative fields on the same object.
    """
    statuses_by_seat: dict[Seat, int | None] = {}
    recognized_records = 0
    required_fields = {"ScreenId", "SeatRow", "SeatNumber", "SeatStatusId"}
    for item in _walk(payload):
        if not required_fields.issubset(item):
            continue
        recognized_records += 1
        if _as_int(item["ScreenId"]) != screen_id:
            continue
        row_raw = item["SeatRow"]
        number = _as_int(item["SeatNumber"])
        if not isinstance(row_raw, str) or number is None:
            continue
        seat = Seat(row=row_raw.strip().upper(), number=number)
        if seat not in requested:
            continue
        status = _as_int(item["SeatStatusId"])
        if seat in statuses_by_seat and statuses_by_seat[seat] != status:
            raise SeatDataError(
                f"conflicting SeatStatusId values for requested seat {seat.key}"
            )
        statuses_by_seat[seat] = status
    if recognized_records == 0:
        raise SeatDataError("JSON contained no recognizable seat records; state left unchanged")
    return RequestedSeatDiagnostics(requested, statuses_by_seat)


def available_requested_seats(
    payload: Any, requested: frozenset[Seat], screen_id: int = 2
) -> set[Seat]:
    """Return only explicitly available requested seats in Stalls Circle."""
    return set(diagnose_requested_seats(payload, requested, screen_id).available)


def adjacent_groups(seats: set[Seat]) -> list[list[Seat]]:
    """Return maximal same-row consecutive groups containing at least two seats."""
    groups: list[list[Seat]] = []
    for row in sorted({seat.row for seat in seats}):
        numbers = sorted(seat.number for seat in seats if seat.row == row)
        if not numbers:
            continue
        run = [numbers[0]]
        for number in numbers[1:]:
            if number == run[-1] + 1:
                run.append(number)
            else:
                if len(run) >= 2:
                    groups.append([Seat(row, n) for n in run])
                run = [number]
        if len(run) >= 2:
            groups.append([Seat(row, n) for n in run])
    return groups


def seats_by_row(seats: set[Seat]) -> list[tuple[str, list[int]]]:
    return [
        (row, sorted(seat.number for seat in seats if seat.row == row))
        for row in sorted({seat.row for seat in seats})
    ]
