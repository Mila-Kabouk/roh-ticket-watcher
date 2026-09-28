import pytest

from roh_ticket_watcher.models import Seat
from roh_ticket_watcher.seats import SeatDataError, adjacent_groups, available_requested_seats


REQUESTED_SEATS = frozenset(
    {Seat("A", number) for number in range(4, 9)}
    | {Seat("B", number) for number in range(98, 107)}
)


def test_filters_only_requested_available_stalls_circle_seats() -> None:
    payload = {
        "wrapper": {
            "seats": [
                {"ScreenId": "2", "SeatRow": "a", "SeatNumber": "4", "SeatStatusId": "0"},
                {"ScreenId": 2, "SeatRow": "A", "SeatNumber": 5, "SeatStatusId": 1},
                {"ScreenId": 1, "SeatRow": "A", "SeatNumber": 6, "SeatStatusId": 0},
                {"ScreenId": 2, "SeatRow": "A", "SeatNumber": 9, "SeatStatusId": 0},
                {"ScreenId": 2, "SeatRow": "B", "SeatNumber": 98, "SeatStatusId": 0},
            ]
        }
    }

    assert available_requested_seats(payload, REQUESTED_SEATS) == {
        Seat("A", 4),
        Seat("B", 98),
    }


def test_only_status_zero_is_available_even_for_false_or_missing_values() -> None:
    payload = [
        {"ScreenId": 2, "SeatRow": "A", "SeatNumber": 4, "SeatStatusId": False},
        {"ScreenId": 2, "SeatRow": "A", "SeatNumber": 5, "SeatStatusId": None},
        {"ScreenId": 2, "SeatRow": "A", "SeatNumber": 6},
    ]
    assert available_requested_seats(payload, REQUESTED_SEATS) == set()


def test_adjacent_groups_are_same_row_and_maximal() -> None:
    seats = {
        Seat("A", 4),
        Seat("A", 5),
        Seat("A", 6),
        Seat("A", 106),
        Seat("B", 6),
        Seat("B", 7),
    }
    assert adjacent_groups(seats) == [
        [Seat("A", 4), Seat("A", 5), Seat("A", 6)],
        [Seat("B", 6), Seat("B", 7)],
    ]


def test_unrecognized_json_does_not_look_like_no_availability() -> None:
    with pytest.raises(SeatDataError):
        available_requested_seats({"Seats": [], "message": "schema changed"}, REQUESTED_SEATS)
