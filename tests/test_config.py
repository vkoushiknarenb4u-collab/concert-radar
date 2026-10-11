from config import Settings


def test_missing_variables_are_optional(monkeypatch):
    for name in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REDIRECT_URI",
                 "TICKETMASTER_API_KEY", "GEMINI_API_KEY", "GEMINI_MODEL", "ALERT_EMAIL",
                 "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "PREFERRED_CITY",
                 "PREFERRED_COUNTRY_CODE", "MAX_TICKET_BUDGET_INR", "MAX_TRAVEL_DISTANCE_KM",
                 "SEARCH_INTERVAL_HOURS", "DEMO_MODE"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings.from_env()
    assert not settings.spotify_configured
    assert not settings.ticketmaster_configured
    assert not settings.gemini_configured
    assert not settings.email_configured
    assert settings.preferred_city == "Chennai"
    assert settings.demo_mode is True
