"""SQLite persistence for preferences, events, matches, scans and alerts."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    try:
        raw = json.loads(result.get("raw_json") or "{}")
    except ValueError:
        raw = {}
    result["raw_json"] = raw
    embedded = raw.get("_embedded", {}) if isinstance(raw, dict) else {}
    attractions = embedded.get("attractions", []) if isinstance(embedded, dict) else []
    if attractions:
        result["attractions"] = [a.get("name", "") for a in attractions if a.get("name")]
        result["attraction_ids"] = [a.get("id") for a in attractions if a.get("id")]
    elif isinstance(raw, dict):
        result["attractions"] = raw.get("attractions", [])
        result["attraction_ids"] = raw.get("attraction_ids", [])
    classifications = raw.get("classifications", []) if isinstance(raw, dict) else []
    result["genres"] = [(c.get("genre") or {}).get("name") for c in classifications
                         if (c.get("genre") or {}).get("name")]
    return result


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as db:
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            if version > 1:
                raise RuntimeError("This database was created by a newer Concert Radar version.")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS artists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    spotify_id TEXT,
                    ticketmaster_id TEXT,
                    genre TEXT,
                    source TEXT NOT NULL DEFAULT 'manual',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS preferences (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    city TEXT NOT NULL DEFAULT 'Chennai',
                    country_code TEXT NOT NULL DEFAULT 'IN',
                    max_budget_inr REAL,
                    max_travel_km REAL,
                    genres_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    source TEXT NOT NULL,
                    is_demo INTEGER NOT NULL DEFAULT 0,
                    name TEXT NOT NULL,
                    matched_artist TEXT,
                    venue TEXT,
                    city TEXT,
                    country TEXT,
                    country_code TEXT,
                    event_date TEXT,
                    event_time TEXT,
                    ticket_url TEXT,
                    price_min REAL,
                    price_max REAL,
                    currency TEXT,
                    presale TEXT,
                    first_discovered_at TEXT NOT NULL,
                    last_checked_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date);
                CREATE INDEX IF NOT EXISTS idx_events_city ON events(city COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS idx_events_demo ON events(is_demo);
                CREATE TABLE IF NOT EXISTS artist_event_matches (
                    artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
                    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
                    score INTEGER NOT NULL,
                    reasons_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (artist_id, event_id)
                );
                CREATE TABLE IF NOT EXISTS scan_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    events_found INTEGER NOT NULL DEFAULT 0,
                    events_new INTEGER NOT NULL DEFAULT 0,
                    notifications_sent INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS notification_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
                    recipient TEXT NOT NULL,
                    attempted_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    UNIQUE(event_id, attempted_at)
                );
                CREATE INDEX IF NOT EXISTS idx_notifications_event_status
                  ON notification_history(event_id, status);
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            if version < 1:
                db.execute("PRAGMA user_version = 1")

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def get_preferences(self) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM preferences WHERE id=1").fetchone()
        if not row:
            return {}
        result = dict(row)
        result["genres"] = json.loads(result.pop("genres_json") or "[]")
        return result

    def save_preferences(self, *, city: str, country_code: str, max_budget_inr: float,
                         max_travel_km: float, genres: list[str] | None = None) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO preferences(id, city, country_code, max_budget_inr, max_travel_km, genres_json, updated_at)
                   VALUES(1, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET city=excluded.city, country_code=excluded.country_code,
                     max_budget_inr=excluded.max_budget_inr, max_travel_km=excluded.max_travel_km,
                     genres_json=excluded.genres_json, updated_at=excluded.updated_at""",
                (city.strip() or "Chennai", country_code.strip().upper() or "IN", max_budget_inr,
                 max_travel_km, self._json(genres or []), utc_now()),
            )

    def add_artist(self, name: str, *, spotify_id: str | None = None, ticketmaster_id: str | None = None,
                   genre: str | None = None, source: str = "manual") -> bool:
        name = " ".join(name.split())[:120]
        if not name:
            return False
        with self.connect() as db:
            cur = db.execute(
                """INSERT INTO artists(name, spotify_id, ticketmaster_id, genre, source, created_at)
                   VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET
                   spotify_id=COALESCE(excluded.spotify_id, artists.spotify_id),
                   ticketmaster_id=COALESCE(excluded.ticketmaster_id, artists.ticketmaster_id),
                   genre=COALESCE(excluded.genre, artists.genre),
                   source=CASE WHEN artists.source='demo' AND excluded.source!='demo'
                               THEN excluded.source ELSE artists.source END""",
                (name, spotify_id, ticketmaster_id, genre, source, utc_now()),
            )
            return cur.rowcount > 0

    def remove_artist(self, artist_id: int) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM artists WHERE id=?", (artist_id,))

    def update_artist(self, artist_id: int, *, name: str, genre: str | None = None) -> None:
        normalized = " ".join(name.split())[:120]
        if not normalized:
            raise ValueError("Artist name cannot be empty.")
        with self.connect() as db:
            db.execute("UPDATE artists SET name=?, genre=? WHERE id=?", (normalized, genre or None, artist_id))

    def set_ticketmaster_artist_id(self, artist_id: int, ticketmaster_id: str) -> None:
        if ticketmaster_id:
            with self.connect() as db:
                db.execute("UPDATE artists SET ticketmaster_id=? WHERE id=? AND ticketmaster_id IS NULL",
                           (ticketmaster_id, artist_id))

    def list_artists(self, *, include_demo: bool = True) -> list[dict[str, Any]]:
        clause = "" if include_demo else "WHERE source != 'demo'"
        with self.connect() as db:
            rows = db.execute(f"SELECT * FROM artists {clause} ORDER BY name COLLATE NOCASE").fetchall()
        return [dict(r) for r in rows]

    def upsert_event(self, event: dict[str, Any]) -> bool:
        """Persist an event. Return True only when its event ID was first discovered."""
        now = utc_now()
        raw = self._json(event.get("raw_json", {}))
        with self.connect() as db:
            existed = db.execute("SELECT 1 FROM events WHERE event_id=?", (event["event_id"],)).fetchone()
            db.execute(
                """INSERT INTO events(event_id, source, is_demo, name, matched_artist, venue, city, country,
                   country_code, event_date, event_time, ticket_url, price_min, price_max, currency, presale,
                   first_discovered_at, last_checked_at, raw_json)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(event_id) DO UPDATE SET source=excluded.source, is_demo=excluded.is_demo,
                     name=excluded.name, matched_artist=COALESCE(excluded.matched_artist, events.matched_artist),
                     venue=excluded.venue, city=excluded.city, country=excluded.country,
                     country_code=excluded.country_code, event_date=excluded.event_date, event_time=excluded.event_time,
                     ticket_url=excluded.ticket_url, price_min=excluded.price_min, price_max=excluded.price_max,
                     currency=excluded.currency, presale=excluded.presale, last_checked_at=excluded.last_checked_at,
                     raw_json=excluded.raw_json""",
                (event["event_id"], event.get("source", "ticketmaster"), int(event.get("is_demo", False)),
                 event["name"], event.get("matched_artist"), event.get("venue"), event.get("city"),
                 event.get("country"), event.get("country_code"), event.get("event_date"),
                 event.get("event_time"), event.get("ticket_url"), event.get("price_min"),
                 event.get("price_max"), event.get("currency"), event.get("presale"), now, now, raw),
            )
        return existed is None

    def get_events(self, *, include_demo: bool = True, limit: int = 500) -> list[dict[str, Any]]:
        clause = "" if include_demo else "WHERE is_demo=0"
        with self.connect() as db:
            rows = db.execute(f"SELECT * FROM events {clause} ORDER BY COALESCE(event_date, '9999') LIMIT ?", (limit,)).fetchall()
        return [_event_from_row(row) for row in rows]

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        if not row:
            return None
        return _event_from_row(row)

    def save_match(self, artist_id: int, event_id: str, score: int, reasons: list[str]) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO artist_event_matches(artist_id,event_id,score,reasons_json,created_at)
                   VALUES(?,?,?,?,?) ON CONFLICT(artist_id,event_id) DO UPDATE SET
                   score=excluded.score,reasons_json=excluded.reasons_json,created_at=excluded.created_at""",
                (artist_id, event_id, score, self._json(reasons), utc_now()),
            )

    def get_matches(self, *, include_demo: bool = True) -> list[dict[str, Any]]:
        demo_clause = "" if include_demo else "AND e.is_demo=0 AND a.source != 'demo'"
        with self.connect() as db:
            rows = db.execute(
                """SELECT a.name AS artist, m.score, m.reasons_json, e.*
                   FROM artist_event_matches m JOIN artists a ON a.id=m.artist_id
                   JOIN events e ON e.event_id=m.event_id WHERE 1=1 """ + demo_clause +
                   " ORDER BY m.score DESC, e.event_date",
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["reasons"] = json.loads(item.pop("reasons_json"))
            output.append(item)
        return output

    def begin_scan(self, source: str) -> int:
        with self.connect() as db:
            cursor = db.execute("INSERT INTO scan_history(started_at,status,source) VALUES(?, 'running', ?)", (utc_now(), source))
            return int(cursor.lastrowid)

    def finish_scan(self, scan_id: int, *, status: str, events_found: int = 0, events_new: int = 0,
                    notifications_sent: int = 0, message: str = "") -> None:
        with self.connect() as db:
            db.execute("""UPDATE scan_history SET finished_at=?,status=?,events_found=?,events_new=?,
                          notifications_sent=?,message=? WHERE id=?""",
                       (utc_now(), status, events_found, events_new, notifications_sent, message[:500], scan_id))

    def list_scans(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM scan_history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def notification_sent(self, event_id: str) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT 1 FROM notification_history WHERE event_id=? AND status='sent' LIMIT 1", (event_id,)).fetchone()
        return row is not None

    def record_notification(self, event_id: str, recipient: str, status: str, message: str = "") -> None:
        with self.connect() as db:
            db.execute("INSERT INTO notification_history(event_id,recipient,attempted_at,status,message) VALUES(?,?,?,?,?)",
                       (event_id, recipient, utc_now(), status, message[:500]))

    def list_notifications(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("""SELECT n.*,e.name,e.venue,e.city,e.event_date,e.ticket_url
                                FROM notification_history n JOIN events e USING(event_id)
                                ORDER BY n.id DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_setting(self, key: str, default: str = "") -> str:
        with self.connect() as db:
            row = db.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as db:
            db.execute("""INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?)
                          ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                       (key, value, utc_now()))
