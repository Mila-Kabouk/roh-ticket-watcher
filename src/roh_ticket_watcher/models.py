from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, order=True)
class Seat:
    row: str
    number: int

    @property
    def key(self) -> str:
        return f"{self.row}:{self.number}"

    @classmethod
    def from_key(cls, value: str) -> "Seat":
        row, number = value.split(":", 1)
        return cls(row=row, number=int(number))


@dataclass(frozen=True)
class Performance:
    id: int
    starts_at: str

    @property
    def start_datetime(self) -> datetime:
        return datetime.fromisoformat(self.starts_at)

    @property
    def label(self) -> str:
        value = self.start_datetime
        return f"{value.day} {value:%b %Y %H:%M}"

    @property
    def booking_url(self) -> str:
        return f"https://www.rbo.org.uk/seatmap?performanceId={self.id}"


@dataclass(frozen=True)
class Production:
    slug: str
    name: str
    url: str
    active: bool
    performances: tuple[Performance, ...]


@dataclass(frozen=True)
class MonitoredPerformance:
    production: Production
    performance: Performance

    @property
    def state_key(self) -> str:
        return f"{self.production.slug}:{self.performance.id}"
