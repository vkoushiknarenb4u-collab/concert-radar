from dataclasses import replace
from datetime import date, timedelta

from config import Settings
from database import Database
import scanner
from scanner import run_scan


def test_demo_mode_works_without_api_credentials_and_sends_no_email(tmp_path):
    settings = Settings(database_path=tmp_path / "data" / "radar.db", demo_mode=True)
    result = run_scan(settings)
    assert result["status"] == "success"
    assert result["events_found"] == 2
    assert result["notifications_sent"] == 0
    from database import Database
    db = Database(settings.database_path)
    events = db.get_events()
    assert len(events) == 2
    assert all(event["is_demo"] for event in events)
    assert all(event["source"] == "demo" for event in events)


def test_live_scan_without_ticketmaster_key_records_setup_status(tmp_path):
    settings = Settings(database_path=tmp_path / "data" / "radar.db", demo_mode=False)
    result = run_scan(settings)
    assert result["status"] == "needs_setup"
    assert "API key is missing" in result["message"]


def test_live_scan_matches_persists_and_deduplicates_alerts(tmp_path, monkeypatch):
    database_path = tmp_path / "data" / "radar.db"
    db = Database(database_path)
    db.save_preferences(city="Chennai", country_code="IN", max_budget_inr=2000, max_travel_km=100)
    db.add_artist("The Example Band", source="manual")
    db.set_setting("alerts_enabled", "true")
    settings = Settings(
        ticketmaster_api_key="test-key",
        alert_email="listener@example.test",
        smtp_host="smtp.example.test",
        smtp_username="listener@example.test",
        smtp_password="test-only",
        database_path=database_path,
        demo_mode=False,
    )
    event = {
        "event_id": "workflow-e2e-001",
        "source": "ticketmaster",
        "is_demo": False,
        "name": "The Example Band - Chennai Live",
        "attractions": ["The Example Band"],
        "attraction_ids": ["tm-example-artist"],
        "venue": "Example Arena",
        "city": "Chennai",
        "country": "India",
        "country_code": "IN",
        "event_date": (date.today() + timedelta(days=60)).isoformat(),
        "event_time": "19:00:00",
        "ticket_url": "https://example.test/tickets",
        "price_min": 900,
        "price_max": 1500,
        "currency": "INR",
        "presale": None,
        "genres": ["rock"],
        "raw_json": {"_embedded": {"attractions": [{"name": "The Example Band", "id": "tm-example-artist"}]}},
    }

    class FakeTicketmaster:
        def __init__(self, *_args, **_kwargs):
            pass

        def search_events(self, **_kwargs):
            return [dict(event)]

    alert_attempts = []

    def record_mock_alert(db_arg, settings_arg, event_arg, **_kwargs):
        alert_attempts.append(event_arg["event_id"])
        db_arg.record_notification(event_arg["event_id"], settings_arg.alert_email, "sent", "Mock delivery")
        return True, "Mock delivery; no email was sent."

    monkeypatch.setattr(scanner, "TicketmasterClient", FakeTicketmaster)
    monkeypatch.setattr(scanner, "notification_explanation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scanner, "send_event_email", record_mock_alert)

    first = run_scan(settings, db=db, demo_mode=False)
    saved_event = db.get_event("workflow-e2e-001")
    saved_matches = db.get_matches(include_demo=False)
    second = run_scan(settings, db=db, demo_mode=False)

    assert first["status"] == "success"
    assert (first["events_found"], first["events_new"], first["notifications_sent"]) == (1, 1, 1)
    assert saved_event["matched_artist"] == "The Example Band"
    assert len(saved_matches) == 1
    assert saved_matches[0]["event_id"] == "workflow-e2e-001"
    assert second["status"] == "success"
    assert (second["events_found"], second["events_new"], second["notifications_sent"]) == (1, 0, 0)
    assert alert_attempts == ["workflow-e2e-001"]
