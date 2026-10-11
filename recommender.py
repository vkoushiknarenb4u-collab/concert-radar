"""Deterministic event ranking; AI is used only to explain these results."""

from __future__ import annotations

from datetime import date
from typing import Any

from matcher import match_artist


def rank_events(events: list[dict[str, Any]], artists: list[dict[str, Any]],
                preferences: dict[str, Any] | None = None, *, today: date | None = None) -> list[dict[str, Any]]:
    prefs = preferences or {}
    today = today or date.today()
    city = (prefs.get("city") or "").strip().casefold()
    country = (prefs.get("country_code") or "").strip().casefold()
    budget = prefs.get("max_budget_inr")
    artist_by_name = {a.get("name", "").casefold(): a for a in artists}
    ranked = []
    for event in events:
        score = 0
        reasons: list[str] = []
        matching_artist = None
        for artist in artists:
            matched, match_reasons = match_artist(artist, event)
            if matched:
                matching_artist = artist
                score += 55
                reasons.extend(match_reasons)
                break
        if not matching_artist and event.get("matched_artist"):
            matching_artist = artist_by_name.get(str(event["matched_artist"]).casefold())
            if matching_artist:
                score += 55
                reasons.append(f"Matched artist: {matching_artist['name']}")

        event_city = (event.get("city") or "").strip().casefold()
        event_country = (event.get("country_code") or "").strip().casefold()
        if city and event_city and event_city == city:
            score += 20
            reasons.append(f"In your preferred city ({prefs.get('city')})")
        elif country and event_country and event_country == country:
            score += 8
            reasons.append("In your preferred country")

        genres = {g.casefold() for g in prefs.get("genres", []) if isinstance(g, str)}
        event_genres = {str(g).casefold() for g in event.get("genres", [])}
        if genres & event_genres:
            score += 10
            reasons.append("Matches a preferred genre")

        event_date = event.get("event_date")
        try:
            parsed_date = date.fromisoformat(str(event_date)[:10]) if event_date else None
        except ValueError:
            parsed_date = None
        if parsed_date and parsed_date >= today:
            score += 10
            reasons.append("Upcoming event")
        elif parsed_date:
            score -= 30
            reasons.append("Event date has passed")

        minimum, maximum = event.get("price_min"), event.get("price_max")
        # The user's budget is INR. Only compare provider prices explicitly denominated in INR.
        if budget is not None and (minimum is not None or maximum is not None) and str(event.get("currency") or "").upper() == "INR":
            cheapest = minimum if minimum is not None else maximum
            if cheapest is not None and float(cheapest) <= float(budget):
                score += 10
                reasons.append("Verified listed price starts within your budget")
            else:
                score -= 10
                reasons.append("Verified listed price starts above your budget")

        ranked.append({**event, "score": score, "reasons": list(dict.fromkeys(reasons)),
                       "matched_artist_record": matching_artist})
    ranked.sort(key=lambda item: (-item["score"], item.get("event_date") or "9999", item.get("name") or ""))
    return ranked
