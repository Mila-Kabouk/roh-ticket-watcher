from pathlib import Path

import pytest
import responses

from roh_ticket_watcher.client import SafeStopError
from roh_ticket_watcher.config import ConfigurationError
from roh_ticket_watcher.discovery import ProductionPageClient, parse_production_page


FIXTURE = Path(__file__).parent / "fixtures" / "production_page.html"
URL = "https://www.rbo.org.uk/production/example-production"


def test_discovers_public_ids_and_reports_unavailable_dates_separately() -> None:
    result = parse_production_page(FIXTURE.read_text(), URL)

    assert result.name == "Example Production"
    assert [(item.id, item.starts_at) for item in result.bookable] == [
        (9001, "2027-01-02T19:30:00+00:00")
    ]
    assert len(result.unavailable) == 3
    assert result.unavailable[0].performance_id is None
    assert "no public performance ID" in result.unavailable[0].reason
    assert "restricted activity type" in result.unavailable[1].reason
    assert "not currently exposed" in result.unavailable[2].reason


@responses.activate
def test_discovery_client_uses_one_get_only() -> None:
    responses.get(URL, body=FIXTURE.read_text(), status=200, content_type="text/html")
    result = ProductionPageClient(retries=1).discover(URL)
    assert result.bookable[0].id == 9001
    assert len(responses.calls) == 1
    assert responses.calls[0].request.method == "GET"


@pytest.mark.parametrize("status", [401, 403, 429])
@responses.activate
def test_discovery_stops_safely_on_access_controls(status: int) -> None:
    responses.get(URL, body="blocked", status=status, content_type="text/html")
    with pytest.raises(SafeStopError):
        ProductionPageClient(retries=3, sleep=lambda _: None).discover(URL)
    assert len(responses.calls) == 1


def test_discovery_rejects_non_rbo_url() -> None:
    with pytest.raises(ConfigurationError):
        ProductionPageClient().discover("https://example.com/production/not-rbo")

