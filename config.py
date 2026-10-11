"""Application configuration loaded from environment variables and .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def _boolean(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    spotify_client_id: str = ""
    spotify_client_secret: str = ""  # Kept for compatibility; PKCE does not send it.
    spotify_redirect_uri: str = "http://127.0.0.1:8888/callback"
    ticketmaster_api_key: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    alert_email: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    preferred_city: str = "Chennai"
    preferred_country_code: str = "IN"
    max_ticket_budget_inr: int = 2000
    max_travel_distance_km: int = 100
    search_interval_hours: int = 6
    demo_mode: bool = True
    database_path: Path = ROOT_DIR / "data" / "concert_radar.sqlite3"
    token_cache_path: Path = ROOT_DIR / ".cache" / "spotify_token.json"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            spotify_client_id=os.getenv("SPOTIFY_CLIENT_ID", "").strip(),
            spotify_client_secret=os.getenv("SPOTIFY_CLIENT_SECRET", "").strip(),
            spotify_redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback").strip(),
            ticketmaster_api_key=os.getenv("TICKETMASTER_API_KEY", "").strip(),
            gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip() or "gemini-3.8-flash",
            alert_email=os.getenv("ALERT_EMAIL", "").strip(),
            smtp_host=os.getenv("SMTP_HOST", "").strip(),
            smtp_port=_integer("SMTP_PORT", 587, 1, 65535),
            smtp_username=os.getenv("SMTP_USERNAME", "").strip(),
            smtp_password=os.getenv("SMTP_PASSWORD", "").strip(),
            preferred_city=os.getenv("PREFERRED_CITY", "Chennai").strip() or "Chennai",
            preferred_country_code=os.getenv("PREFERRED_COUNTRY_CODE", "IN").strip().upper() or "IN",
            max_ticket_budget_inr=_integer("MAX_TICKET_BUDGET_INR", 2000, 0, 10_000_000),
            max_travel_distance_km=_integer("MAX_TRAVEL_DISTANCE_KM", 100, 0, 50_000),
            search_interval_hours=_integer("SEARCH_INTERVAL_HOURS", 6, 1, 168),
            demo_mode=_boolean("DEMO_MODE", True),
        )

    @property
    def spotify_configured(self) -> bool:
        return bool(self.spotify_client_id)

    @property
    def ticketmaster_configured(self) -> bool:
        return bool(self.ticketmaster_api_key)

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def email_configured(self) -> bool:
        return bool(self.alert_email and self.smtp_host and self.smtp_username and self.smtp_password)


def load_settings() -> Settings:
    return Settings.from_env()
