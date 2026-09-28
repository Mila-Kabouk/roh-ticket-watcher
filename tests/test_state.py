import json
from pathlib import Path

import pytest

from roh_ticket_watcher.models import Seat
from roh_ticket_watcher.state import StateError, StateStore


def test_state_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "state.json"
    store = StateStore(path)
    store.update("manon:74500", {Seat("A", 4), Seat("B", 100)})
    store.save()

    loaded = StateStore(path)
    loaded.load()
    assert loaded.available_for("manon:74500") == {Seat("A", 4), Seat("B", 100)}


def test_malformed_state_stops_instead_of_resetting_history(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 1, "performances": {"manon:74500": "bad"}}))
    store = StateStore(path)
    store.load()
    with pytest.raises(StateError):
        store.available_for("manon:74500")
