from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import urlparse

import requests


LOG = logging.getLogger(__name__)
RBO_HOST = "www.rbo.org.uk"
SEATS_URL_TEMPLATE = "https://www.rbo.org.uk/api/v2-proxy/TXN/Performances/{performance_id}/Seats"
TRANSIENT_STATUSES = {408, 500, 502, 503, 504}
CHALLENGE_MARKERS = (
    "captcha",
    "queue-it",
    "waiting room",
    "interstitial",
    "access denied",
    "authentication required",
    "login required",
    "cf-chl-",
)


class FetchError(RuntimeError):
    """A performance could not be checked after ordinary safe retries."""


class SafeStopError(RuntimeError):
    """The sweep must stop rather than trying to bypass an access control."""


class RBOSeatClient:
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
                "Accept": "application/json",
                "User-Agent": "roh-ticket-watcher/2.0 (read-only availability notifier)",
            }
        )

    def fetch(self, performance_id: int) -> Any:
        url = SEATS_URL_TEMPLATE.format(performance_id=performance_id)
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != RBO_HOST
            or not parsed.path.endswith("/Seats")
        ):
            raise RuntimeError("Refusing an unexpected RBO URL")
        params = {
            "performanceId": performance_id,
            "modeOfSaleId": 4,
            "constituentId": 0,
        }

        for attempt in range(1, self.retries + 1):
            try:
                # This is intentionally the only RBO request in the project.
                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.timeout_seconds,
                    allow_redirects=False,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == self.retries:
                    raise FetchError(f"network error after {attempt} attempts: {exc}") from exc
                self._wait_before_retry(attempt, f"network error: {exc}")
                continue

            if response.status_code in {403, 429}:
                retry_after = response.headers.get("Retry-After")
                detail = f"HTTP {response.status_code}"
                if retry_after:
                    detail += f" (Retry-After: {retry_after})"
                raise SafeStopError(f"{detail}; stopping without bypass attempts")

            if response.status_code == 401:
                raise SafeStopError("HTTP 401; endpoint may now require authentication")
            if 300 <= response.status_code < 400:
                raise SafeStopError("RBO seat endpoint redirected; stopping without following it")

            payload = self._validated_json(response)

            if response.status_code in TRANSIENT_STATUSES:
                if attempt == self.retries:
                    raise FetchError(f"HTTP {response.status_code} after {attempt} attempts")
                self._wait_before_retry(attempt, f"HTTP {response.status_code}")
                continue
            if not 200 <= response.status_code < 300:
                raise FetchError(f"unexpected HTTP {response.status_code}")
            return payload

        raise AssertionError("retry loop ended unexpectedly")

    def _validated_json(self, response: requests.Response) -> Any:
        content_type = response.headers.get("Content-Type", "").lower()
        body_preview = response.text[:50_000].lower()
        if any(marker in body_preview for marker in CHALLENGE_MARKERS):
            raise SafeStopError("CAPTCHA, queue, or access interstitial detected; stopping safely")
        if "json" not in content_type:
            raise SafeStopError(
                f"unexpected non-JSON response ({content_type or 'no Content-Type'}); stopping safely"
            )
        try:
            payload = response.json()
        except (requests.JSONDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise SafeStopError(
                "response claimed to be JSON but could not be decoded; stopping safely"
            ) from exc
        serialized = json.dumps(payload, ensure_ascii=False).lower()[:50_000]
        if any(marker in serialized for marker in CHALLENGE_MARKERS):
            raise SafeStopError(
                "CAPTCHA, queue, or access interstitial detected in JSON; stopping safely"
            )
        return payload

    def _wait_before_retry(self, attempt: int, reason: str) -> None:
        delay = min(2 ** (attempt - 1), 8)
        LOG.warning("Transient RBO error (%s); retrying in %ss", reason, delay)
        self.sleep(delay)
