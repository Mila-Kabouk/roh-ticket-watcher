from __future__ import annotations

import logging

import requests

from .models import MonitoredPerformance, Seat
from .seats import adjacent_groups, seats_by_row


LOG = logging.getLogger(__name__)


class NotificationError(RuntimeError):
    pass


def _number_list(numbers: list[int]) -> str:
    return ", ".join(str(number) for number in numbers)


def format_notification(
    target: MonitoredPerformance, new: set[Seat], current: set[Seat]
) -> str:
    groups = adjacent_groups(current)
    performance = target.performance
    lines = [
        "🎟️ RBO ticket availability",
        f"Production: {target.production.name}",
        f"Performance: {performance.label}",
        f"Performance ID: {performance.id}",
        f"Adjacent pair exists: {'YES' if groups else 'NO'}",
        "",
    ]
    if groups:
        lines.append("⭐ ADJACENT OPTIONS (shown first)")
        for group in groups:
            numbers = [seat.number for seat in group]
            lines.append(f"• Row {group[0].row}: seats {_number_list(numbers)}")
        lines.append("")

    lines.append("NEWLY AVAILABLE")
    for row, numbers in seats_by_row(new):
        noun = "seat" if len(numbers) == 1 else "seats"
        lines.append(f"• Row {row}: {noun} {_number_list(numbers)}")

    lines.extend(["", "ALL REQUESTED SEATS CURRENTLY AVAILABLE"])
    for row, numbers in seats_by_row(current):
        noun = "seat" if len(numbers) == 1 else "seats"
        lines.append(f"• Row {row}: {noun} {_number_list(numbers)}")

    lines.extend(
        [
            "",
            f"Production page: {target.production.url}",
            f"Book directly: {performance.booking_url}",
        ]
    )
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        timeout_seconds: float = 15.0,
        session: requests.Session | None = None,
    ) -> None:
        if not token or not chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    def send(self, message: str) -> None:
        try:
            response = self.session.post(
                self.url,
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "disable_web_page_preview": False,
                },
                timeout=self.timeout_seconds,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            # A requests exception can include the request URL, which contains
            # the bot token. Never copy that exception text into logs.
            raise NotificationError(
                f"Telegram network error ({type(exc).__name__}); token omitted"
            ) from exc
        if not 200 <= response.status_code < 300:
            raise NotificationError(
                f"Telegram returned HTTP {response.status_code}: {response.text[:300]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise NotificationError("Telegram returned invalid JSON") from exc
        if not payload.get("ok"):
            raise NotificationError(f"Telegram rejected the message: {payload}")
        LOG.info("Telegram notification sent")
