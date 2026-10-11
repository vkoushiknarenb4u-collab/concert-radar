"""Local Spotify Authorization Code with PKCE integration."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import requests


class SpotifyError(RuntimeError):
    pass


class SpotifyClient:
    AUTH_URL = "https://accounts.spotify.com/authorize"
    TOKEN_URL = "https://accounts.spotify.com/api/token"
    API_URL = "https://api.spotify.com/v1"

    def __init__(self, client_id: str, redirect_uri: str, token_cache_path: str | Path,
                 *, timeout: float = 12.0, session: requests.Session | None = None):
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.token_cache_path = Path(token_cache_path)
        self.timeout = timeout
        self.session = session or requests.Session()

    def _redirect_parts(self) -> tuple[str, int, str]:
        parsed = urllib.parse.urlparse(self.redirect_uri)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.port:
            raise SpotifyError("Spotify redirect URI must use local HTTP and include a port, such as http://127.0.0.1:8888/callback.")
        return parsed.hostname, parsed.port, parsed.path or "/callback"

    def start_authorization(self, *, open_browser: bool = True) -> str:
        if not self.client_id:
            raise SpotifyError("Spotify Client ID is not configured.")
        host, port, callback_path = self._redirect_parts()
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": "user-top-read",
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
        auth_url = f"{self.AUTH_URL}?{urllib.parse.urlencode(params)}"
        completed = threading.Event()
        result: dict[str, str] = {}
        session = self.session

        class CallbackHandler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                if parsed.path != callback_path:
                    self.send_error(404)
                    return
                if query.get("state", [""])[0] != state:
                    result["error"] = "Spotify authorization state did not match. Start again from Concert Radar."
                    body = "Authorization could not be verified. You can close this window."
                elif query.get("error"):
                    result["error"] = "Spotify authorization was declined."
                    body = "Authorization was declined. You can close this window."
                elif not query.get("code"):
                    result["error"] = "Spotify did not return an authorization code."
                    body = "Spotify did not return an authorization code. You can close this window."
                else:
                    try:
                        response = session.post(
                            SpotifyClient.TOKEN_URL,
                            data={"client_id": self.server.client_id, "grant_type": "authorization_code",
                                  "code": query["code"][0], "redirect_uri": self.server.redirect_uri,
                                  "code_verifier": self.server.verifier},
                            timeout=self.server.timeout,
                        )
                        if response.status_code >= 400:
                            raise SpotifyError("Spotify could not complete authorization. Check the app's exact redirect URI and try again.")
                        token = response.json()
                        if not token.get("access_token") or not token.get("refresh_token"):
                            raise SpotifyError("Spotify did not return the access and refresh tokens needed for this app.")
                        token["expires_at"] = int(time.time()) + int(token.get("expires_in", 3600))
                        path: Path = self.server.token_cache_path
                        path.parent.mkdir(parents=True, exist_ok=True)
                        temp_path = path.with_suffix(path.suffix + ".tmp")
                        temp_path.write_text(json.dumps(token), encoding="utf-8")
                        temp_path.replace(path)
                        result["success"] = "Spotify connected. Return to Concert Radar and import your top artists."
                        body = "Spotify connected to Concert Radar. You can close this window and return to the app."
                    except (requests.RequestException, ValueError, SpotifyError):
                        result["error"] = "Spotify authorization failed. Check your connection and Spotify app settings."
                        body = "Authorization failed. Return to Concert Radar to try again."
                encoded = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                completed.set()

        class LocalServer(ThreadingHTTPServer):
            daemon_threads = True

        try:
            server = LocalServer((host, port), CallbackHandler)
        except OSError as exc:
            raise SpotifyError("Could not start Spotify's local callback. Check whether port 8888 is already in use.") from exc
        server.client_id = self.client_id
        server.redirect_uri = self.redirect_uri
        server.verifier = verifier
        server.timeout = self.timeout
        server.token_cache_path = self.token_cache_path

        def serve() -> None:
            server.timeout = 180
            server.handle_request()
            server.server_close()

        threading.Thread(target=serve, daemon=True).start()
        if open_browser:
            webbrowser.open(auth_url)
        return auth_url

    def _read_token(self) -> dict[str, Any]:
        try:
            return json.loads(self.token_cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SpotifyError("Connect Spotify first. No local Spotify token is available.") from exc

    def _write_token(self, token: dict[str, Any]) -> None:
        self.token_cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.token_cache_path.with_suffix(self.token_cache_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(token), encoding="utf-8")
        temp_path.replace(self.token_cache_path)

    def _access_token(self) -> str:
        token = self._read_token()
        if int(token.get("expires_at", 0)) > int(time.time()) + 30:
            return token["access_token"]
        refresh_token = token.get("refresh_token")
        if not refresh_token:
            raise SpotifyError("Spotify authorization expired. Connect Spotify again to continue.")
        try:
            response = self.session.post(self.TOKEN_URL, data={"client_id": self.client_id,
                "grant_type": "refresh_token", "refresh_token": refresh_token}, timeout=self.timeout)
        except requests.RequestException as exc:
            raise SpotifyError("Spotify token refresh failed because the service could not be reached.") from exc
        if response.status_code >= 400:
            raise SpotifyError("Spotify authorization expired or was revoked. Connect Spotify again.")
        try:
            updated = response.json()
        except ValueError as exc:
            raise SpotifyError("Spotify returned an unreadable refresh response.") from exc
        token.update(updated)
        token["refresh_token"] = updated.get("refresh_token", refresh_token)
        token["expires_at"] = int(time.time()) + int(updated.get("expires_in", 3600))
        self._write_token(token)
        return token["access_token"]

    def top_artists(self, limit: int = 50) -> list[dict[str, Any]]:
        access_token = self._access_token()
        response = None
        for attempt in range(3):
            try:
                response = self.session.get(f"{self.API_URL}/me/top/artists",
                    params={"limit": max(1, min(limit, 50)), "time_range": "medium_term"},
                    headers={"Authorization": f"Bearer {access_token}"}, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt == 2:
                    raise SpotifyError("Spotify could not be reached. Try again when online.") from exc
                time.sleep(0.3 * (attempt + 1))
                continue
            if response.status_code == 429 and attempt < 2:
                try:
                    delay = min(float(response.headers.get("Retry-After", "1")), 5.0)
                except (TypeError, ValueError):
                    delay = 1.0
                time.sleep(max(0.1, delay))
                continue
            break
        if response is None:
            raise SpotifyError("Spotify did not return a response.")
        if response.status_code == 401:
            raise SpotifyError("Spotify authorization expired. Connect Spotify again.")
        if response.status_code == 403:
            raise SpotifyError("Spotify denied top-artist access. Check Premium status, the app allowlist and Development Mode access.")
        if response.status_code == 429:
            raise SpotifyError("Spotify rate limit reached. Wait before importing again.")
        if response.status_code >= 400:
            raise SpotifyError(f"Spotify artist request failed (HTTP {response.status_code}).")
        try:
            items = response.json().get("items", [])
        except ValueError as exc:
            raise SpotifyError("Spotify returned an unreadable artist response.") from exc
        return [{"name": item.get("name"), "spotify_id": item.get("id"),
                 "genres": item.get("genres") or []} for item in items if item.get("name")]

    def is_connected(self) -> bool:
        try:
            return bool(self._read_token().get("refresh_token"))
        except SpotifyError:
            return False
