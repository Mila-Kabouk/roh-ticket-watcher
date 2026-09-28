import json
from pathlib import Path

from roh_ticket_watcher.cli import main
from roh_ticket_watcher.config import ConfigStore
from roh_ticket_watcher.discovery import DiscoveryResult
from roh_ticket_watcher.models import Performance


URL = "https://www.rbo.org.uk/production/new-show"


def empty_config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "seat_selection": {"screen_id": 2, "rows": {"A": [[4, 5]]}},
                "productions": [],
            }
        )
    )


def discovered() -> DiscoveryResult:
    return DiscoveryResult(
        "New Show",
        URL,
        (Performance(99001, "2027-04-01T19:30:00+01:00"),),
        (),
    )


def test_add_production_displays_review_then_cancels_without_writing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    path = tmp_path / "config.json"
    empty_config(path)
    monkeypatch.setattr(
        "roh_ticket_watcher.cli.ProductionPageClient.discover",
        lambda self, url: discovered(),
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "")

    assert main(["--config", str(path), "add-production", URL]) == 0
    output = capsys.readouterr().out
    assert "PUBLIC/BOOKABLE PERFORMANCES (1)" in output
    assert "Cancelled; configuration was not changed." in output
    assert ConfigStore(path).load().productions == ()


def test_add_production_requires_explicit_yes_before_writing(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "config.json"
    empty_config(path)
    monkeypatch.setattr(
        "roh_ticket_watcher.cli.ProductionPageClient.discover",
        lambda self, url: discovered(),
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")

    assert main(["--config", str(path), "add-production", URL]) == 0
    productions = ConfigStore(path).load().productions
    assert len(productions) == 1
    assert productions[0].slug == "new-show"
    assert productions[0].active is True
