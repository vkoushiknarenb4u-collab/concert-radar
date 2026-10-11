"""Single-run scanner shared by the CLI, Streamlit dashboard and Task Scheduler."""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from config import Settings, load_settings
from database import Database
from gemini_client import notification_explanation
from matcher import match_artist
from matcher import normalize_name
from notifications import meaningful_update_fields, send_event_email
from recommender import rank_events
from ticketmaster_client import ProviderError, TicketmasterClient


def _seed_demo(db: Database) -> None:
    """Insert unmistakably fictional examples for offline exploration."""
    samples = [
        {"name": "Neon Banyan", "genre": "indie pop"},
        {"name": "Velvet Metro", "genre": "alternative rock"},
    ]
    for artist in samples:
        db.add_artist(artist["name"], genre=artist["genre"], source="demo")
    day_one = (date.today() + timedelta(days=24)).isoformat()
    day_two = (date.today() + timedelta(days=51)).isoformat()
    examples = [
        {"event_id": "demo-neon-banyan-chennai", "source": "demo", "is_demo": True,
         "name": "Neon Banyan — Moonlit Marina Session", "matched_artist": "Neon Banyan",
         "attractions": ["Neon Banyan"], "attraction_ids": [], "venue": "Fictional Marina Hall",
         "city": "Chennai", "country": "India", "country_code": "IN", "event_date": day_one,
         "event_time": "19:30:00", "ticket_url": None, "price_min": 899, "price_max": 1499,
         "currency": "INR", "presale": None, "genres": ["indie pop"], "raw_json": {"attractions": ["Neon Banyan"],
         "classifications": [{"genre": {"name": "indie pop"}}]}},
        {"event_id": "demo-velvet-metro-bengaluru", "source": "demo", "is_demo": True,
         "name": "Velvet Metro — Signal Lights Tour", "matched_artist": "Velvet Metro",
         "attractions": ["Velvet Metro"], "attraction_ids": [], "venue": "Fictional Garden Arena",
         "city": "Bengaluru", "country": "India", "country_code": "IN", "event_date": day_two,
         "event_time": "20:00:00", "ticket_url": None, "price_min": 2400, "price_max": 3200,
         "currency": "INR", "presale": "Example only: early access opens in demo mode", "genres": ["alternative rock"],
         "raw_json": {"attractions": ["Velvet Metro"], "classifications": [{"genre": {"name": "alternative rock"}}]}},
    ]
    for example in examples:
        db.upsert_event(example)
    artists = db.list_artists()
    for event in examples:
        for artist in artists:
            matched, reasons = match_artist(artist, event)
            if matched:
                score = 75 if event.get("city", "").casefold() == "chennai" else 65
                if event.get("price_min") is not None and event["price_min"] <= 2000:
                    reasons.append("Fictional sample price is within the default budget")
                    score += 10
                db.save_match(artist["id"], event["event_id"], score, reasons)


def _scan_lock(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR)
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except (OSError, BlockingIOError) as exc:
        os.close(fd)
        raise FileExistsError from exc


def _release_lock(fd: int) -> None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        os.close(fd)


def run_scan(settings: Settings | None = None, *, db: Database | None = None,
             demo_mode: bool | None = None) -> dict[str, Any]:
    settings = settings or load_settings()
    db = db or Database(settings.database_path)
    demo = settings.demo_mode if demo_mode is None else demo_mode
    lock_path = settings.database_path.parent / "scan.lock"
    try:
        lock_fd = _scan_lock(lock_path)
    except FileExistsError:
        return {"status": "skipped", "message": "A scan is already running.", "events_found": 0, "events_new": 0}
    try:
        os.write(lock_fd, str(os.getpid()).encode("ascii"))
        scan_id = db.begin_scan("demo" if demo else "ticketmaster")
        found = new = sent = 0
        warnings: list[str] = []
        try:
            prefs = db.get_preferences()
            if not prefs:
                db.save_preferences(city=settings.preferred_city, country_code=settings.preferred_country_code,
                    max_budget_inr=settings.max_ticket_budget_inr, max_travel_km=settings.max_travel_distance_km)
                prefs = db.get_preferences()
            artists = db.list_artists(include_demo=demo)
            if demo:
                _seed_demo(db)
                events = [event for event in db.get_events(include_demo=True) if event["is_demo"]]
                artists = [a for a in db.list_artists() if a["source"] == "demo"]
                found = len(events)
                db.finish_scan(scan_id, status="success", events_found=found, events_new=0,
                               message="Demo sample data refreshed. No real provider or email was used.")
                return {"status": "success", "message": "Demo sample data is ready. No emails were sent.",
                        "events_found": found, "events_new": 0, "notifications_sent": 0}

            if not settings.ticketmaster_configured:
                message = "Ticketmaster API key is missing. Add it to .env or use demo mode."
                db.finish_scan(scan_id, status="needs_setup", message=message)
                return {"status": "needs_setup", "message": message, "events_found": 0, "events_new": 0}

            client = TicketmasterClient(settings.ticketmaster_api_key)
            start = date.today()
            end = start + timedelta(days=365)
            queries = artists[:20] if artists else [None]
            collected: dict[str, dict[str, Any]] = {}
            for artist in queries:
                try:
                    records = client.search_events(artist=artist["name"] if artist else None,
                        city=prefs.get("city") or settings.preferred_city,
                        country_code=prefs.get("country_code") or settings.preferred_country_code,
                        start_date=start, end_date=end)
                    for record in records:
                        if record.get("event_id"):
                            collected.setdefault(record["event_id"], record)
                except ProviderError as exc:
                    warnings.append(str(exc))
                    break

            found = len(collected)
            ranked = rank_events(list(collected.values()), artists, prefs)
            for event in ranked:
                pairings = []
                for artist in artists:
                    matched, reasons = match_artist(artist, event)
                    if matched:
                        pairings.append((artist, reasons))
                        raw = event.get("raw_json", {})
                        attractions = (raw.get("_embedded", {}).get("attractions", [])
                                       if isinstance(raw, dict) else [])
                        for attraction in attractions:
                            if normalize_name(attraction.get("name")) == normalize_name(artist.get("name")) and attraction.get("id"):
                                db.set_ticketmaster_artist_id(artist["id"], attraction["id"])
                                artist["ticketmaster_id"] = attraction["id"]
                                break
                if pairings:
                    event["matched_artist"] = pairings[0][0]["name"]
                previous = db.get_event(event["event_id"])
                changed_fields = meaningful_update_fields(previous, event)
                is_new = db.upsert_event(event)
                if is_new:
                    new += 1
                for artist, reasons in pairings:
                    rank_reason = event.get("reasons", [])
                    score = event.get("score", 0)
                    db.save_match(artist["id"], event["event_id"], score, list(dict.fromkeys(reasons + rank_reason)))
                alert_enabled = db.get_setting("alerts_enabled", "false").casefold() == "true"
                update_warrants_alert = bool(changed_fields and db.notification_sent(event["event_id"]))
                should_alert = is_new or update_warrants_alert
                if pairings and should_alert and alert_enabled and settings.email_configured and (
                    update_warrants_alert or not db.notification_sent(event["event_id"])):
                    artist_name = pairings[0][0]["name"]
                    explanation = notification_explanation(settings.gemini_api_key, settings.gemini_model,
                                                           event, artist_name)
                    ok, _message = send_event_email(db, settings, event, explanation=explanation,
                        force=update_warrants_alert, update=update_warrants_alert)
                    sent += int(ok)

            if warnings and not collected:
                status = "partial_failure"
                message = "; ".join(dict.fromkeys(warnings))
            elif warnings:
                status = "partial_success"
                message = f"{len(warnings)} provider search(es) failed; returned records were saved."
            else:
                status = "success"
                message = (f"{len(warnings)} provider search(es) failed. " if warnings else "") + (
                    "No events were returned for this location and artist search." if not collected else "Scan completed.")
            db.finish_scan(scan_id, status=status, events_found=found, events_new=new,
                           notifications_sent=sent, message=message)
            return {"status": status, "message": message, "events_found": found,
                    "events_new": new, "notifications_sent": sent}
        except Exception as exc:
            safe_detail = type(exc).__name__
            db.finish_scan(scan_id, status="failed", events_found=found, events_new=new,
                           notifications_sent=sent, message=f"Scan stopped unexpectedly ({safe_detail}). Check configuration and activity details.")
            return {"status": "failed", "message": f"Scan stopped unexpectedly ({safe_detail}). Check configuration and activity details.",
                    "events_found": found, "events_new": new, "notifications_sent": sent}
    finally:
        _release_lock(lock_fd)
