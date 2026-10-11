"""Ticketmaster Discovery API v2 provider."""

from __future__ import annotations

import time
import re
from datetime import date
from typing import Any
from urllib.parse import quote

import requests

BASE_URL = "https://app.ticketmaster.com/discovery/v2/events.json"


class ProviderError(RuntimeError):
    """Safe-to-display provider failure; contains no credential values."""


def _event_record(payload: dict[str, Any], matched_artist: str | None = None) -> dict[str, Any]:
    embedded = payload.get("_embedded") or {}
    venues = embedded.get("venues") or []
    venue = venues[0] if venues else {}
    city_obj = venue.get("city") or {}
    country_obj = venue.get("country") or {}
    dates = payload.get("dates") or {}
    start = dates.get("start") or {}
    ranges = payload.get("priceRanges") or []
    price = ranges[0] if ranges else {}
    presales = payload.get("presales") or []
    presale_parts = []
    for sale in presales:
        detail = sale.get("name") or sale.get("description")
        if detail:
            presale_parts.append(str(detail))
    attractions = embedded.get("attractions") or []
    classifications = payload.get("classifications") or []
    genres = [(c.get("genre") or {}).get("name") for c in classifications if (c.get("genre") or {}).get("name")]
    return {
        "event_id": payload.get("id"),
        "source": "ticketmaster",
        "is_demo": False,
        "name": payload.get("name"),
        "matched_artist": matched_artist,
        "attractions": [a.get("name", "") for a in attractions if a.get("name")],
        "attraction_ids": [a.get("id") for a in attractions if a.get("id")],
        "venue": venue.get("name"),
        "city": city_obj.get("name"),
        "country": country_obj.get("name"),
        "country_code": country_obj.get("countryCode"),
        "event_date": start.get("localDate"),
        "event_time": start.get("localTime"),
        "ticket_url": payload.get("url"),
        "price_min": price.get("min"),
        "price_max": price.get("max"),
        "currency": price.get("currency"),
        "presale": "; ".join(presale_parts) or None,
        "genres": genres,
        "raw_json": payload,
    }


class TicketmasterClient:
    def __init__(self, api_key: str, *, timeout: float = 12.0, session: requests.Session | None = None,
                 max_pages: int = 4):
        self.api_key = api_key
        self.timeout = timeout
        self.session = session or requests.Session()
        self.max_pages = max(1, min(max_pages, 10))

    def search_events(self, *, artist: str | None = None, city: str = "Chennai", country_code: str = "IN",
                      start_date: date | None = None, end_date: date | None = None,
                      keyword: str | None = None) -> list[dict[str, Any]]:
        if not self.api_key:
            raise ProviderError("Ticketmaster API key is not configured.")
        if len(city) > 80 or (country_code and not re.fullmatch(r"[A-Za-z]{2}", country_code)):
            raise ProviderError("Enter a city up to 80 characters and a two-letter country code.")
        if start_date and end_date and end_date < start_date:
            raise ProviderError("The end date must be on or after the start date.")
        query = keyword or artist
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for page in range(self.max_pages):
            params: dict[str, Any] = {"apikey": self.api_key, "size": 100, "page": page}
            if query:
                params["keyword"] = query[:100]
            if city:
                params["city"] = city[:80]
            if country_code:
                params["countryCode"] = country_code.upper()[:2]
            if not query:
                params["classificationName"] = "music"
            if start_date:
                params["startDateTime"] = f"{start_date.isoformat()}T00:00:00Z"
            if end_date:
                params["endDateTime"] = f"{end_date.isoformat()}T23:59:59Z"
            response = None
            for attempt in range(3):
                try:
                    response = self.session.get(BASE_URL, params=params, timeout=self.timeout)
                except requests.RequestException as exc:
                    if attempt == 2:
                        raise ProviderError("Ticketmaster could not be reached. Check the connection and try again.") from exc
                    time.sleep(0.4 * (attempt + 1))
                    continue
                if response.status_code == 429 and attempt < 2:
                    try:
                        delay = min(float(response.headers.get("Retry-After", "1")), 5.0)
                    except ValueError:
                        delay = 1.0
                    time.sleep(max(0.1, delay))
                    continue
                break
            if response is None:
                raise ProviderError("Ticketmaster did not return a response.")
            if response.status_code == 429:
                raise ProviderError("Ticketmaster rate limit reached. Wait a little before scanning again.")
            if response.status_code in (401, 403):
                raise ProviderError("Ticketmaster rejected the API key or its access is not enabled.")
            if response.status_code >= 400:
                raise ProviderError(f"Ticketmaster search failed (HTTP {response.status_code}).")
            try:
                body = response.json()
            except (ValueError, requests.JSONDecodeError) as exc:
                raise ProviderError("Ticketmaster returned an unreadable response.") from exc
            page_events = (body.get("_embedded") or {}).get("events", [])
            for payload in page_events:
                if not payload.get("id") or not payload.get("name"):
                    continue
                record = _event_record(payload)
                if record.get("event_id") and record["event_id"] not in seen:
                    seen.add(record["event_id"])
                    results.append(record)
            page_meta = body.get("page") or {}
            if not page_events or page + 1 >= page_meta.get("totalPages", 1):
                break
        return results

    def get_event(self, event_id: str) -> dict[str, Any]:
        """Retrieve one event's current detail record from Discovery API v2."""
        if not self.api_key:
            raise ProviderError("Ticketmaster API key is not configured.")
        try:
            response = self.session.get(
                f"https://app.ticketmaster.com/discovery/v2/events/{quote(event_id, safe='')}.json",
                params={"apikey": self.api_key}, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ProviderError("Ticketmaster could not be reached. Check the connection and try again.") from exc
        if response.status_code == 404:
            raise ProviderError("Ticketmaster no longer has this event record.")
        if response.status_code in (401, 403):
            raise ProviderError("Ticketmaster rejected the API key or its access is not enabled.")
        if response.status_code == 429:
            raise ProviderError("Ticketmaster rate limit reached. Wait a little before requesting event details.")
        if response.status_code >= 400:
            raise ProviderError(f"Ticketmaster event details failed (HTTP {response.status_code}).")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("Ticketmaster returned an unreadable event response.") from exc
        if not payload.get("id") or not payload.get("name"):
            raise ProviderError("Ticketmaster did not provide the requested event details.")
        return _event_record(payload)
