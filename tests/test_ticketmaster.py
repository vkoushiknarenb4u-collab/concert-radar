from datetime import date
from unittest.mock import Mock

import requests

from ticketmaster_client import ProviderError, TicketmasterClient


class Response:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body or {}
        self.headers = headers or {}

    def json(self):
        return self._body


def test_empty_results_are_an_empty_list():
    session = Mock()
    session.get.return_value = Response(200, {})
    assert TicketmasterClient("key", session=session).search_events(city="Chennai") == []


def test_search_maps_only_provider_fields_and_ticket_link():
    payload = {"id": "id-1", "name": "A show", "url": "https://tickets.example/event",
        "dates": {"start": {"localDate": "2027-02-03"}},
        "_embedded": {"venues": [{"name": "Hall", "city": {"name": "Chennai"},
                                      "country": {"name": "India", "countryCode": "IN"}}],
                       "attractions": [{"id": "artist-1", "name": "An Artist"}]},
        "priceRanges": [{"min": 500, "max": 1200, "currency": "INR"}],
        "presales": [{"name": "Artist presale"}]}
    session = Mock()
    session.get.return_value = Response(200, {"_embedded": {"events": [payload]}, "page": {"totalPages": 1}})
    event = TicketmasterClient("key", session=session).search_events(
        artist="An Artist", city="Chennai", country_code="IN", start_date=date(2027, 1, 1))[0]
    assert event["event_id"] == "id-1"
    assert event["ticket_url"] == "https://tickets.example/event"
    assert event["price_min"] == 500
    assert event["event_time"] is None
    assert event["presale"] == "Artist presale"


def test_rate_limit_retries_then_returns_empty(monkeypatch):
    monkeypatch.setattr("ticketmaster_client.time.sleep", lambda _delay: None)
    session = Mock()
    session.get.side_effect = [Response(429, headers={"Retry-After": "0"}), Response(200, {})]
    assert TicketmasterClient("key", session=session).search_events(city="Chennai") == []
    assert session.get.call_count == 2


def test_rate_limit_after_retries_is_reported(monkeypatch):
    monkeypatch.setattr("ticketmaster_client.time.sleep", lambda _delay: None)
    session = Mock()
    session.get.return_value = Response(429)
    try:
        TicketmasterClient("key", session=session).search_events(city="Chennai")
    except ProviderError as exc:
        assert "rate limit" in str(exc).casefold()
    else:
        raise AssertionError("expected a rate-limit error")


def test_network_failure_is_sanitized():
    session = Mock()
    session.get.side_effect = requests.Timeout("key=should-not-appear")
    try:
        TicketmasterClient("secret-key", session=session).search_events(city="Chennai")
    except ProviderError as exc:
        assert "should-not-appear" not in str(exc)
    else:
        raise AssertionError("expected provider error")


def test_get_event_details_maps_event_record():
    session = Mock()
    session.get.return_value = Response(200, {"id": "detail-1", "name": "Detailed show", "url": "https://ticket.example"})
    result = TicketmasterClient("key", session=session).get_event("detail-1")
    assert result["event_id"] == "detail-1"
    assert result["ticket_url"] == "https://ticket.example"


def test_get_event_details_sanitizes_provider_denial():
    session = Mock()
    session.get.return_value = Response(403)
    try:
        TicketmasterClient("key", session=session).get_event("detail-1")
    except ProviderError as exc:
        assert "rejected" in str(exc)
    else:
        raise AssertionError("expected provider authorization error")
