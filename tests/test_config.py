import json
from pathlib import Path

from roh_ticket_watcher.config import ConfigStore, parse_config
from roh_ticket_watcher.models import Performance, Production, Seat


def raw_config() -> dict:
    return {
        "version": 1,
        "seat_selection": {"screen_id": 2, "rows": {"A": [[4, 5]]}},
        "productions": [
            {
                "slug": "one",
                "name": "Production One",
                "url": "https://www.rbo.org.uk/production/one",
                "active": True,
                "performances": [
                    {"datetime": "2027-01-01T19:30:00+00:00", "id": 80001}
                ],
            },
            {
                "slug": "two",
                "name": "Production Two",
                "url": "https://www.rbo.org.uk/production/two",
                "active": False,
                "performances": [
                    {"datetime": "2027-02-01T19:30:00+00:00", "id": 80002}
                ],
            },
        ],
    }


def test_config_supports_multiple_productions_and_ignores_inactive() -> None:
    config = parse_config(raw_config())
    assert config.requested_seats == frozenset({Seat("A", 4), Seat("A", 5)})
    targets = config.monitored_performances()
    assert len(config.productions) == 2
    assert [(item.production.slug, item.performance.id) for item in targets] == [
        ("one", 80001)
    ]


def test_store_adds_and_disables_without_python_edits(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw_config()))
    store = ConfigStore(path)
    production = Production(
        "three",
        "Production Three",
        "https://www.rbo.org.uk/production/three",
        True,
        (Performance(80003, "2027-03-01T19:30:00+00:00"),),
    )
    store.add_production(production)
    store.set_active("three", False)

    loaded = store.load()
    assert len(loaded.productions) == 3
    assert loaded.productions[-1].active is False


def test_migrated_manon_configuration_has_all_21_performances() -> None:
    config_path = Path(__file__).parents[1] / "config.json"
    config = ConfigStore(config_path).load()
    manon = config.productions[0]
    assert manon.slug == "manon-kenneth-macmillan"
    assert manon.active is True
    assert len(manon.performances) == 21
