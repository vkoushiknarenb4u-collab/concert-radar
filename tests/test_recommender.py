from datetime import date

from recommender import rank_events


def test_rank_is_deterministic_and_uses_preference_evidence():
    artists = [{"name": "Neon Banyan"}]
    events = [
        {"event_id": "match", "name": "Neon Banyan Live", "city": "Chennai", "country_code": "IN",
         "event_date": "2027-01-02", "price_min": 900, "currency": "INR"},
        {"event_id": "other", "name": "Another concert", "city": "Pune", "country_code": "IN",
         "event_date": "2027-01-02"},
    ]
    prefs = {"city": "Chennai", "country_code": "IN", "max_budget_inr": 2000}
    first = rank_events(events, artists, prefs, today=date(2026, 10, 11))
    second = rank_events(events, artists, prefs, today=date(2026, 10, 11))
    assert [e["event_id"] for e in first] == [e["event_id"] for e in second]
    assert first[0]["event_id"] == "match"
    assert first[0]["score"] > first[1]["score"]
    assert "Verified listed price starts within your budget" in first[0]["reasons"]


def test_unknown_or_non_inr_currency_is_not_compared_to_inr_budget():
    result = rank_events([{"event_id": "foreign", "name": "Neon Banyan Live", "price_min": 20,
                           "currency": "USD"}], [{"name": "Neon Banyan"}], {"max_budget_inr": 2000})[0]
    assert "Verified listed price starts within your budget" not in result["reasons"]
