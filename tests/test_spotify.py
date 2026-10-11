import json
import time
from unittest.mock import Mock

import requests

from spotify_client import SpotifyClient, SpotifyError


def test_missing_token_is_reported(tmp_path):
    client = SpotifyClient("client", "http://127.0.0.1:8888/callback", tmp_path / "token.json")
    try:
        client.top_artists()
    except SpotifyError as exc:
        assert "Connect Spotify" in str(exc)
    else:
        raise AssertionError("expected a missing-token error")


def test_spotify_403_eligibility_is_clear(tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text(json.dumps({"access_token": "private-token", "expires_at": int(time.time()) + 300}), encoding="utf-8")
    session = Mock()
    session.get.return_value.status_code = 403
    client = SpotifyClient("client", "http://127.0.0.1:8888/callback", token_path, session=session)
    try:
        client.top_artists()
    except SpotifyError as exc:
        assert "Premium" in str(exc)
        assert "private-token" not in str(exc)
    else:
        raise AssertionError("expected Spotify authorization error")


def test_top_artists_uses_authorized_token_and_returns_artist_fields(tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text(json.dumps({
        "access_token": "private-token",
        "refresh_token": "private-refresh-token",
        "expires_at": int(time.time()) + 300,
    }), encoding="utf-8")
    session = Mock()
    response = Mock(status_code=200)
    response.json.return_value = {"items": [{"name": "Example Artist", "id": "artist-123", "genres": ["indie"]}]}
    session.get.return_value = response
    client = SpotifyClient("client", "http://127.0.0.1:8888/callback", token_path, session=session)

    artists = client.top_artists(limit=1)

    assert artists == [{"name": "Example Artist", "spotify_id": "artist-123", "genres": ["indie"]}]
    session.get.assert_called_once()
    assert "Bearer private-token" == session.get.call_args.kwargs["headers"]["Authorization"]


def test_expired_spotify_token_refreshes_and_is_persisted(tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text(json.dumps({
        "access_token": "expired-access-token",
        "refresh_token": "private-refresh-token",
        "expires_at": 1,
    }), encoding="utf-8")
    session = Mock()
    refreshed = Mock(status_code=200)
    refreshed.json.return_value = {"access_token": "new-access-token", "expires_in": 3600}
    session.post.return_value = refreshed
    response = Mock(status_code=200)
    response.json.return_value = {"items": []}
    session.get.return_value = response
    client = SpotifyClient("client", "http://127.0.0.1:8888/callback", token_path, session=session)

    assert client.top_artists(limit=1) == []

    saved_token = json.loads(token_path.read_text(encoding="utf-8"))
    assert saved_token["access_token"] == "new-access-token"
    assert saved_token["refresh_token"] == "private-refresh-token"
    session.post.assert_called_once()


def test_expired_authorization_refresh_failure_is_reported(tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text(json.dumps({"access_token": "expired", "refresh_token": "refresh", "expires_at": 1}), encoding="utf-8")
    session = Mock()
    session.post.return_value.status_code = 400
    client = SpotifyClient("client", "http://127.0.0.1:8888/callback", token_path, session=session)
    try:
        client.top_artists()
    except SpotifyError as exc:
        assert "expired" in str(exc).casefold() or "revoked" in str(exc).casefold()
    else:
        raise AssertionError("expected refresh error")
