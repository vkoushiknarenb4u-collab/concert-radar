"""Email delivery with explicit success tracking and duplicate prevention."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

from config import Settings
from database import Database

MEANINGFUL_EVENT_FIELDS = ("name", "event_date", "event_time", "venue", "city", "country", "ticket_url",
                           "price_min", "price_max", "currency", "presale")


def meaningful_update_fields(previous: dict[str, Any] | None, current: dict[str, Any]) -> list[str]:
    if not previous:
        return []
    return [key for key in MEANINGFUL_EVENT_FIELDS
            if previous.get(key) != current.get(key) and (previous.get(key) is not None or current.get(key) is not None)]


def format_event_email(event: dict[str, Any], explanation: str | None = None, *, update: bool = False) -> tuple[str, str]:
    prefix = "Update — " if update else ""
    title = f"Concert Radar: {prefix}{event.get('name') or 'New concert'}"
    rows = [f"Event: {event.get('name') or 'Not provided'}"]
    if update:
        rows.append("Update: The provider changed one or more event details since the previous check.")
    if event.get("matched_artist"):
        rows.append(f"Matched artist: {event['matched_artist']}")
    if event.get("venue") or event.get("city"):
        rows.append("Venue and location: " + ", ".join(filter(None, (event.get("venue"), event.get("city"), event.get("country")))))
    if event.get("event_date") or event.get("event_time"):
        rows.append("Date and time: " + " ".join(filter(None, (event.get("event_date"), event.get("event_time")))))
    if event.get("ticket_url"):
        rows.append(f"Official tickets: {event['ticket_url']}")
    if event.get("price_min") is not None or event.get("price_max") is not None:
        rows.append(f"Listed price range: {event.get('price_min', 'not provided')}–{event.get('price_max', 'not provided')} {event.get('currency') or ''}".strip())
    if event.get("presale"):
        rows.append(f"Presale details: {event['presale']}")
    if explanation:
        rows.append(f"Why it matched: {explanation}")
    rows.append("\nConcert Radar only reports details supplied by the event provider.")
    return title, "\n".join(rows)


def send_event_email(db: Database, settings: Settings, event: dict[str, Any], *,
                     explanation: str | None = None, recipient: str | None = None,
                     force: bool = False, update: bool = False) -> tuple[bool, str]:
    if event.get("is_demo") or event.get("source") == "demo":
        return False, "Demo events never send email."
    destination = (recipient or settings.alert_email).strip()
    if not destination or not settings.smtp_host or not settings.smtp_username or not settings.smtp_password:
        db.record_notification(event["event_id"], destination or "(not configured)", "skipped", "SMTP is not configured.")
        return False, "SMTP is not configured."
    if not force and db.notification_sent(event["event_id"]):
        return False, "A notification for this event was already delivered."
    subject, body = format_event_email(event, explanation, update=update)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_username
    message["To"] = destination
    message.set_content(body)
    try:
        context = ssl.create_default_context()
        if settings.smtp_port == 465:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15, context=context) as server:
                server.login(settings.smtp_username, settings.smtp_password)
                refused = server.send_message(message)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(settings.smtp_username, settings.smtp_password)
                refused = server.send_message(message)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
    except (smtplib.SMTPException, OSError, TimeoutError):
        db.record_notification(event["event_id"], destination, "failed", "Email provider returned an error.")
        return False, "The email provider returned an error. Check SMTP configuration and try again."
    db.record_notification(event["event_id"], destination, "sent", "Delivered by SMTP provider.")
    return True, "Email sent."
