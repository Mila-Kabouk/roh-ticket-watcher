from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from .client import CHALLENGE_MARKERS, FetchError, SafeStopError, TRANSIENT_STATUSES
from .config import slug_from_url, validate_production_url
from .models import Performance, Production


class DiscoveryError(ValueError):
    """The public production page did not contain usable performance metadata."""


@dataclass(frozen=True)
class UnavailablePerformance:
    starts_at: str
    performance_id: int | None
    reason: str

    @property
    def label(self) -> str:
        try:
            value = datetime.fromisoformat(self.starts_at)
        except ValueError:
            return self.starts_at or "Unknown date/time"
        return f"{value.day} {value:%b %Y %H:%M}"


@dataclass(frozen=True)
class DiscoveryResult:
    name: str
    url: str
    bookable: tuple[Performance, ...]
    unavailable: tuple[UnavailablePerformance, ...]

    @property
    def slug(self) -> str:
        return slug_from_url(self.url)

    def to_production(self) -> Production:
        if not self.bookable:
            raise DiscoveryError("No currently public/bookable performances can be added")
        return Production(self.slug, self.name, self.url, True, self.bookable)


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _extract_dehydrated_state(html: str) -> Any:
    match = re.search(
        r"window\.__REACT_QUERY_DEHYDRATED_STATE__\s*=\s*(\{.*?\});\s*</script>",
        html,
        re.DOTALL,
    )
    if not match:
        raise DiscoveryError("RBO page did not contain the expected public performance data")
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise DiscoveryError("RBO embedded performance data was not valid JSON") from exc


def _query_payload(query: Any) -> Any:
    if not isinstance(query, dict):
        return None
    state = query.get("state")
    if not isinstance(state, dict):
        return None
    data = state.get("data")
    if not isinstance(data, dict):
        return None
    return data


def parse_production_page(html: str, url: str) -> DiscoveryResult:
    normalized_url = validate_production_url(url)
    dehydrated = _extract_dehydrated_state(html)
    queries = dehydrated.get("queries") if isinstance(dehydrated, dict) else None
    if not isinstance(queries, list):
        raise DiscoveryError("RBO performance data did not contain a query list")

    page_data: dict[str, Any] | None = None
    availability: dict[str, Any] = {}
    for query in queries:
        key = query.get("queryKey") if isinstance(query, dict) else None
        payload = _query_payload(query)
        if isinstance(key, list) and key and key[0] == "singleProductionPage":
            candidate = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(candidate, dict):
                page_data = candidate
        for item in _walk(payload):
            if item.get("type") == "activityAvailability" and isinstance(item.get("id"), str):
                availability[item["id"]] = item.get("attributes")

    if page_data is None:
        raise DiscoveryError("RBO page did not contain a public production record")
    meta = page_data.get("meta")
    stage = page_data.get("stage")
    production = page_data.get("production")
    name = meta.get("title") if isinstance(meta, dict) else None
    if not isinstance(name, str) or not name.strip():
        name = stage.get("title") if isinstance(stage, dict) else None
    if not isinstance(name, str) or not name.strip():
        name = production.get("evergreenTitle") if isinstance(production, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise DiscoveryError("RBO page did not identify the production name")
    activities = stage.get("activities") if isinstance(stage, dict) else None
    if not isinstance(activities, list) or not activities:
        raise DiscoveryError("RBO page listed no on-stage performance dates")

    bookable: list[Performance] = []
    unavailable: list[UnavailablePerformance] = []
    seen_ids: set[int] = set()
    for activity in activities:
        if not isinstance(activity, dict):
            continue
        attributes = activity.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
        starts_at = attributes.get("date")
        if not isinstance(starts_at, str):
            starts_at = ""
        raw_id = activity.get("id")
        performance_id = int(raw_id) if isinstance(raw_id, str) and raw_id.isdigit() else None
        activity_type = attributes.get("activityType")

        reason: str | None = None
        if performance_id is None:
            reason = "no public performance ID"
        elif activity_type != "standard":
            reason = f"restricted activity type: {activity_type or 'unknown'}"
        else:
            availability_attributes = availability.get(str(performance_id))
            tickets_available = (
                availability_attributes.get("ticketsAvailable")
                if isinstance(availability_attributes, dict)
                else None
            )
            if isinstance(tickets_available, bool) or not isinstance(tickets_available, int):
                reason = "not currently exposed as publicly bookable"

        try:
            parsed_date = datetime.fromisoformat(starts_at)
            if parsed_date.tzinfo is None:
                raise ValueError
        except ValueError:
            reason = f"{reason + '; ' if reason else ''}missing or invalid date/time"

        if reason is not None:
            unavailable.append(UnavailablePerformance(starts_at, performance_id, reason))
            continue
        assert performance_id is not None
        if performance_id in seen_ids:
            raise DiscoveryError(f"Duplicate public performance ID: {performance_id}")
        seen_ids.add(performance_id)
        bookable.append(Performance(performance_id, starts_at))

    bookable.sort(key=lambda item: item.start_datetime)
    unavailable.sort(key=lambda item: item.starts_at)
    return DiscoveryResult(name.strip(), normalized_url, tuple(bookable), tuple(unavailable))


class ProductionPageClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        retries: int = 3,
        session: requests.Session | None = None,
        sleep=time.sleep,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.session = session or requests.Session()
        self.sleep = sleep
        self.session.headers.update(
            {
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "roh-ticket-watcher/2.0 (read-only production discovery)",
            }
        )

    def discover(self, url: str) -> DiscoveryResult:
        normalized_url = validate_production_url(url)
        html = self.fetch(normalized_url)
        return parse_production_page(html, normalized_url)

    def fetch(self, url: str) -> str:
        normalized_url = validate_production_url(url)
        for attempt in range(1, self.retries + 1):
            try:
                # Discovery is deliberately limited to one read-only page GET.
                response = self.session.get(
                    normalized_url,
                    timeout=self.timeout_seconds,
                    allow_redirects=False,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == self.retries:
                    raise FetchError(
                        f"production-page network error after {attempt} attempts: {exc}"
                    ) from exc
                self.sleep(min(2 ** (attempt - 1), 8))
                continue

            if response.status_code in {401, 403, 429}:
                raise SafeStopError(
                    f"HTTP {response.status_code} during discovery; stopping without bypass attempts"
                )
            if 300 <= response.status_code < 400:
                raise SafeStopError(
                    "RBO redirected the production page; stopping without following it"
                )
            body_lower = response.text[:100_000].lower()
            if any(marker in body_lower for marker in CHALLENGE_MARKERS):
                raise SafeStopError(
                    "CAPTCHA, queue, authentication, or access interstitial detected; stopping safely"
                )
            if response.status_code in TRANSIENT_STATUSES:
                if attempt == self.retries:
                    raise FetchError(
                        f"production page returned HTTP {response.status_code} after {attempt} attempts"
                    )
                self.sleep(min(2 ** (attempt - 1), 8))
                continue
            if not 200 <= response.status_code < 300:
                raise FetchError(f"production page returned unexpected HTTP {response.status_code}")
            final_url = validate_production_url(response.url)
            if final_url != normalized_url:
                raise SafeStopError("RBO redirected discovery to a different production page")
            content_type = response.headers.get("Content-Type", "").lower()
            if "html" not in content_type:
                raise SafeStopError(
                    f"unexpected discovery response type ({content_type or 'missing'}); stopping safely"
                )
            return response.text
        raise AssertionError("retry loop ended unexpectedly")


def format_discovery(result: DiscoveryResult) -> str:
    lines = [f"Production: {result.name}", f"URL: {result.url}", ""]
    lines.append(f"PUBLIC/BOOKABLE PERFORMANCES ({len(result.bookable)})")
    if result.bookable:
        for performance in result.bookable:
            lines.append(f"  + {performance.label} — performance ID {performance.id}")
    else:
        lines.append("  (none)")
    lines.extend(["", f"NOT CURRENTLY PUBLIC/BOOKABLE ({len(result.unavailable)})"])
    if result.unavailable:
        for performance in result.unavailable:
            identifier = (
                f"performance ID {performance.performance_id}"
                if performance.performance_id is not None
                else "no public performance ID"
            )
            lines.append(f"  - {performance.label} — {identifier} — {performance.reason}")
    else:
        lines.append("  (none)")
    return "\n".join(lines)
