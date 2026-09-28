from pathlib import Path

from roh_ticket_watcher.models import MonitoredPerformance, Performance, Production, Seat
from roh_ticket_watcher.notification import NotificationError
from roh_ticket_watcher.state import StateStore
from roh_ticket_watcher.watcher import Watcher


REQUESTED = frozenset({Seat("A", 4), Seat("A", 5), Seat("B", 100)})


def target(slug: str, name: str, performance_id: int) -> MonitoredPerformance:
    production = Production(
        slug,
        name,
        f"https://www.rbo.org.uk/production/{slug}",
        True,
        (Performance(performance_id, "2026-10-13T19:30:00+01:00"),),
    )
    return MonitoredPerformance(production, production.performances[0])


def payload(*seats: tuple[str, int]) -> dict:
    records = [
        {"ScreenId": 2, "SeatRow": row, "SeatNumber": number, "SeatStatusId": 0}
        for row, number in seats
    ]
    if not records:
        records.append({"ScreenId": 2, "SeatRow": "A", "SeatNumber": 4, "SeatStatusId": 1})
    return {"Seats": records}


class MutableClient:
    def __init__(self, value: dict) -> None:
        self.value = value

    def fetch(self, performance_id: int) -> dict:
        del performance_id
        return self.value


class MapClient:
    def __init__(self, values: dict[int, dict]) -> None:
        self.values = values

    def fetch(self, performance_id: int) -> dict:
        return self.values[performance_id]


class RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, message: str) -> None:
        self.messages.append(message)


class FailingNotifier:
    def send(self, message: str) -> None:
        del message
        raise NotificationError("temporary Telegram failure")


def make_watcher(client, notifier, state: StateStore, **kwargs) -> Watcher:
    return Watcher(
        client=client,
        notifier=notifier,
        state=state,
        requested_seats=REQUESTED,
        request_delay_seconds=0,
        sleep=lambda _: None,
        **kwargs,
    )


def test_alerts_once_then_again_after_disappearance_and_reappearance(tmp_path: Path) -> None:
    client = MutableClient(payload(("A", 4)))
    notifier = RecordingNotifier()
    watcher = make_watcher(client, notifier, StateStore(tmp_path / "state.json"))
    performances = [target("manon", "Manon", 74500)]

    watcher.run(performances)
    watcher.run(performances)
    assert len(notifier.messages) == 1

    client.value = payload()
    watcher.run(performances)
    client.value = payload(("A", 4))
    watcher.run(performances)
    assert len(notifier.messages) == 2


def test_notification_puts_adjacent_options_before_new_seats(tmp_path: Path) -> None:
    notifier = RecordingNotifier()
    watcher = make_watcher(
        MutableClient(payload(("A", 4), ("A", 5), ("B", 100))),
        notifier,
        StateStore(tmp_path / "state.json"),
    )
    watcher.run([target("manon", "Manon (Kenneth MacMillan)", 74500)])

    message = notifier.messages[0]
    assert "Production: Manon (Kenneth MacMillan)" in message
    assert "Adjacent pair exists: YES" in message
    assert "Row A: seats 4, 5" in message
    assert message.index("ADJACENT OPTIONS") < message.index("NEWLY AVAILABLE")
    assert "https://www.rbo.org.uk/seatmap?performanceId=74500" in message


def test_multiple_productions_are_checked_and_identified(tmp_path: Path) -> None:
    notifier = RecordingNotifier()
    watcher = make_watcher(
        MapClient({74500: payload(("A", 4)), 88001: payload(("B", 100))}),
        notifier,
        StateStore(tmp_path / "state.json"),
    )
    result = watcher.run(
        [
            target("manon", "Manon", 74500),
            target("carmen", "Carmen", 88001),
        ]
    )

    assert result.checked == 2
    assert result.alerts == 2
    assert "Production: Manon" in notifier.messages[0]
    assert "Production: Carmen" in notifier.messages[1]


def test_dry_run_does_not_write_state(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    watcher = make_watcher(
        MutableClient(payload(("A", 4))),
        None,
        StateStore(state_path),
        dry_run=True,
    )
    result = watcher.run([target("manon", "Manon", 74500)])
    assert result.alerts == 1
    assert not state_path.exists()


def test_failed_notification_retains_old_state_for_retry(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    client = MutableClient(payload(("A", 4)))
    result = make_watcher(client, FailingNotifier(), StateStore(state_path)).run(
        [target("manon", "Manon", 74500)]
    )
    assert result.errors == 1
    assert not state_path.exists()

    notifier = RecordingNotifier()
    make_watcher(client, notifier, StateStore(state_path)).run(
        [target("manon", "Manon", 74500)]
    )
    assert len(notifier.messages) == 1

