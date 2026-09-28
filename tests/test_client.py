import pytest
import requests
import responses

from roh_ticket_watcher.client import RBOSeatClient, SafeStopError


@responses.activate
def test_client_uses_only_documented_get_and_parameters() -> None:
    url = "https://www.rbo.org.uk/api/v2-proxy/TXN/Performances/74500/Seats"
    responses.get(url, json={"Seats": []}, status=200)
    client = RBOSeatClient(retries=1)

    assert client.fetch(74500) == {"Seats": []}
    request = responses.calls[0].request
    assert request.method == "GET"
    assert request.url is not None
    assert "performanceId=74500" in request.url
    assert "modeOfSaleId=4" in request.url
    assert "constituentId=0" in request.url


@pytest.mark.parametrize("status", [403, 429])
@responses.activate
def test_client_safely_stops_on_forbidden_or_rate_limit(status: int) -> None:
    responses.get(
        "https://www.rbo.org.uk/api/v2-proxy/TXN/Performances/74500/Seats",
        json={"error": "stop"},
        status=status,
    )
    with pytest.raises(SafeStopError):
        RBOSeatClient(retries=3, sleep=lambda _: None).fetch(74500)
    assert len(responses.calls) == 1


@responses.activate
def test_client_safely_stops_on_html_interstitial() -> None:
    responses.get(
        "https://www.rbo.org.uk/api/v2-proxy/TXN/Performances/74500/Seats",
        body="<html><title>Waiting room</title></html>",
        status=200,
        content_type="text/html",
    )
    with pytest.raises(SafeStopError):
        RBOSeatClient(retries=1).fetch(74500)


@responses.activate
def test_client_retries_transient_network_error() -> None:
    url = "https://www.rbo.org.uk/api/v2-proxy/TXN/Performances/74500/Seats"
    responses.get(url, body=requests.ConnectionError("temporary"))
    responses.get(url, json={"Seats": []}, status=200)
    assert RBOSeatClient(retries=2, sleep=lambda _: None).fetch(74500) == {"Seats": []}
    assert len(responses.calls) == 2
