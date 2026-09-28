from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .models import MonitoredPerformance, Performance, Production, Seat


class ConfigurationError(ValueError):
    """Raised for invalid local configuration."""


@dataclass(frozen=True)
class WatchConfig:
    screen_id: int
    requested_seats: frozenset[Seat]
    productions: tuple[Production, ...]

    def monitored_performances(self) -> list[MonitoredPerformance]:
        return [
            MonitoredPerformance(production, performance)
            for production in self.productions
            if production.active
            for performance in production.performances
        ]


def validate_production_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.hostname not in {"www.rbo.org.uk", "rbo.org.uk"}:
        raise ConfigurationError("Production URL must use HTTPS on www.rbo.org.uk")
    path_parts = parsed.path.rstrip("/").split("/")
    if len(path_parts) != 3 or path_parts[1] != "production" or not path_parts[2]:
        raise ConfigurationError("URL must be an official RBO /production/<slug> page")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", path_parts[2]):
        raise ConfigurationError("RBO production URL contains an invalid production slug")
    return f"https://www.rbo.org.uk{parsed.path.rstrip('/')}"


def slug_from_url(url: str) -> str:
    return urlparse(validate_production_url(url)).path.rstrip("/").split("/")[-1]


def _positive_int(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigurationError(f"{context} must be a positive integer")
    return value


def _parse_performance(raw: Any, context: str) -> Performance:
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{context} must be an object")
    performance_id = _positive_int(raw.get("id"), f"{context}.id")
    starts_at = raw.get("datetime")
    if not isinstance(starts_at, str):
        raise ConfigurationError(f"{context}.datetime must be an ISO 8601 string")
    try:
        parsed = Performance(performance_id, starts_at).start_datetime
    except ValueError as exc:
        raise ConfigurationError(f"{context}.datetime is not valid ISO 8601") from exc
    if parsed.tzinfo is None:
        raise ConfigurationError(f"{context}.datetime must include a UTC offset")
    return Performance(performance_id, starts_at)


def _parse_production(raw: Any, index: int) -> Production:
    context = f"productions[{index}]"
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{context} must be an object")
    slug = raw.get("slug")
    name = raw.get("name")
    url = raw.get("url")
    active = raw.get("active")
    performances_raw = raw.get("performances")
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ConfigurationError(f"{context}.slug must be lowercase hyphenated text")
    if not isinstance(name, str) or not name.strip():
        raise ConfigurationError(f"{context}.name must be non-empty text")
    if not isinstance(url, str) or slug_from_url(url) != slug:
        raise ConfigurationError(f"{context}.url must be an RBO production URL matching its slug")
    if not isinstance(active, bool):
        raise ConfigurationError(f"{context}.active must be true or false")
    if not isinstance(performances_raw, list) or not performances_raw:
        raise ConfigurationError(f"{context}.performances must be a non-empty list")
    performances = tuple(
        _parse_performance(item, f"{context}.performances[{item_index}]")
        for item_index, item in enumerate(performances_raw)
    )
    return Production(slug, name.strip(), validate_production_url(url), active, performances)


def parse_config(raw: Any) -> WatchConfig:
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ConfigurationError("config.json must be an object with version 1")
    seat_selection = raw.get("seat_selection")
    if not isinstance(seat_selection, dict):
        raise ConfigurationError("seat_selection must be an object")
    screen_id = _positive_int(seat_selection.get("screen_id"), "seat_selection.screen_id")
    rows = seat_selection.get("rows")
    if not isinstance(rows, dict) or not rows:
        raise ConfigurationError("seat_selection.rows must be a non-empty object")
    requested: set[Seat] = set()
    for row, ranges in rows.items():
        if not isinstance(row, str) or not re.fullmatch(r"[A-Z]+", row):
            raise ConfigurationError("seat row names must contain uppercase letters")
        if not isinstance(ranges, list) or not ranges:
            raise ConfigurationError(f"seat_selection.rows.{row} must contain ranges")
        for seat_range in ranges:
            if (
                not isinstance(seat_range, list)
                or len(seat_range) != 2
                or any(isinstance(value, bool) or not isinstance(value, int) for value in seat_range)
                or seat_range[0] <= 0
                or seat_range[1] < seat_range[0]
            ):
                raise ConfigurationError(f"Invalid seat range for row {row}: {seat_range}")
            requested.update(
                Seat(row, number) for number in range(seat_range[0], seat_range[1] + 1)
            )

    productions_raw = raw.get("productions")
    if not isinstance(productions_raw, list):
        raise ConfigurationError("productions must be a list")
    productions = tuple(
        _parse_production(item, index) for index, item in enumerate(productions_raw)
    )
    slugs = [production.slug for production in productions]
    if len(slugs) != len(set(slugs)):
        raise ConfigurationError("production slugs must be unique")
    performance_ids = [
        performance.id for production in productions for performance in production.performances
    ]
    if len(performance_ids) != len(set(performance_ids)):
        raise ConfigurationError("performance IDs must be unique across all productions")
    return WatchConfig(screen_id, frozenset(requested), productions)


class ConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load_raw(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigurationError(f"Configuration file not found: {self.path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(f"Cannot read {self.path}: {exc}") from exc
        parse_config(raw)
        return raw

    def load(self) -> WatchConfig:
        return parse_config(self.load_raw())

    def save_raw(self, raw: dict[str, Any]) -> None:
        parse_config(raw)
        temp_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                json.dump(raw, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except OSError as exc:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise ConfigurationError(f"Cannot save {self.path}: {exc}") from exc

    def add_production(self, production: Production) -> None:
        raw = self.load_raw()
        if any(item.get("slug") == production.slug for item in raw["productions"]):
            raise ConfigurationError(f"Production already exists: {production.slug}")
        raw["productions"].append(
            {
                "slug": production.slug,
                "name": production.name,
                "url": production.url,
                "active": production.active,
                "performances": [
                    {"datetime": performance.starts_at, "id": performance.id}
                    for performance in production.performances
                ],
            }
        )
        self.save_raw(raw)

    def set_active(self, slug: str, active: bool) -> None:
        raw = self.load_raw()
        for production in raw["productions"]:
            if production.get("slug") == slug:
                production["active"] = active
                self.save_raw(raw)
                return
        raise ConfigurationError(f"Unknown production slug: {slug}")


def env_float(name: str, default: float, minimum: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if not math.isfinite(value) or value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}")
    return value


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value
