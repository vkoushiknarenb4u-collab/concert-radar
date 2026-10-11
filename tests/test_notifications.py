import smtplib
from unittest.mock import MagicMock

import notifications
from config import Settings
from notifications import meaningful_update_fields, send_event_email


def event():
    return {"event_id": "tm-1", "source": "ticketmaster", "is_demo": False,
            "name": "A show", "venue": "Hall", "city": "Chennai", "event_date": "2027-01-02",
            "ticket_url": "https://tickets.example"}


def configured_settings():
    return Settings(alert_email="listener@example.com", smtp_host="smtp.example.com", smtp_port=587,
                    smtp_username="listener@example.com", smtp_password="private")


def test_success_is_recorded_and_repeat_is_blocked(db, monkeypatch):
    db.upsert_event(event())
    server = MagicMock()
    server.__enter__.return_value = server
    server.send_message.return_value = {}
    monkeypatch.setattr(notifications.smtplib, "SMTP", lambda *args, **kwargs: server)
    sent, _ = send_event_email(db, configured_settings(), event())
    assert sent
    sent_again, message = send_event_email(db, configured_settings(), event())
    assert not sent_again
    assert "already delivered" in message
    assert server.send_message.call_count == 1


def test_smtp_failure_is_not_marked_delivered(db, monkeypatch):
    db.upsert_event(event())
    server = MagicMock()
    server.__enter__.return_value = server
    server.send_message.side_effect = smtplib.SMTPException("rejected")
    monkeypatch.setattr(notifications.smtplib, "SMTP", lambda *args, **kwargs: server)
    sent, _ = send_event_email(db, configured_settings(), event())
    assert not sent
    assert not db.notification_sent("tm-1")


def test_refused_recipient_return_is_failure(db, monkeypatch):
    db.upsert_event(event())
    server = MagicMock()
    server.__enter__.return_value = server
    server.send_message.return_value = {"listener@example.com": (550, b"rejected")}
    monkeypatch.setattr(notifications.smtplib, "SMTP", lambda *args, **kwargs: server)
    sent, _ = send_event_email(db, configured_settings(), event())
    assert not sent
    assert not db.notification_sent("tm-1")


def test_demo_email_is_always_blocked(db):
    demo = {**event(), "event_id": "demo-1", "source": "demo", "is_demo": True}
    db.upsert_event(demo)
    sent, message = send_event_email(db, configured_settings(), demo)
    assert not sent
    assert "never send" in message


def test_only_material_provider_changes_trigger_update_alerts():
    old = {"name": "A show", "event_date": "2027-01-02", "price_min": 500}
    same = {**old, "raw_json": {"unrelated": "changed"}}
    changed = {**old, "event_date": "2027-01-03"}
    assert meaningful_update_fields(old, same) == []
    assert "event_date" in meaningful_update_fields(old, changed)
