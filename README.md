# Concert Radar

Concert Radar is a local Streamlit dashboard for matching saved favourite artists to Ticketmaster Discovery API listings. SQLite keeps preferences, events and activity on your computer. Gemini can explain recommendations using stored event records; it never supplies concert facts. The first launch defaults to a clearly labeled offline demo with fictional events, and demo mode cannot send email.

The app is intended for one local user. It does not require cloud hosting.

## Requirements

- Windows 10 or 11
- Python 3.11 or newer (64-bit; Python 3.13 is a good default)
- An internet connection only for live API calls, Spotify authorization, Gemini and email

## Install and run

Open PowerShell in the project folder:

```powershell
py -3.13 --version
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, reopen PowerShell and activate again. If you installed a different supported Python version, replace `3.13` with that version (for example `py -3.14`).

Start the dashboard:

```powershell
streamlit run app.py
```

The browser opens at `http://localhost:8501`. Streamlit is configured to bind to `127.0.0.1` only and usage statistics are disabled. Demo mode is on at first. Browse the fictional events and simulated email previews without adding any credentials. You can also run a demo scan from the dashboard or use `python main.py --demo`.

To run the scanner separately:

```powershell
python main.py
```

The scanner follows `DEMO_MODE` in `.env`. Use `python main.py --live` to explicitly run a live scan, or set `DEMO_MODE=false` before starting the dashboard to use live searches. A real search needs a Ticketmaster key. Missing optional service keys do not prevent the dashboard from starting.

Run the automated suite with mocked integrations:

```powershell
python -m pytest -q
```

The tests do not need real API keys and do not send email.

## Configure services

Edit `.env` in Notepad or another text editor. Keep the file private; it is excluded by `.gitignore`. Restart both the dashboard and scanner after changing configuration. The app displays only whether a service is configured, never credential values.

### Ticketmaster

1. Create a developer account in the [Ticketmaster Developer Portal](https://developer.ticketmaster.com/).
2. Create an app and copy its consumer key into `TICKETMASTER_API_KEY`.
3. Set `DEMO_MODE=false` to search real listings.
4. In the dashboard, use **Settings → Check Ticketmaster connection** or run a scan.

Concert Radar uses the official Discovery API v2 events endpoint and official event URLs from its response. Ticketmaster coverage in India can be limited. An empty result means the provider returned no events for the selected query; the app does not invent listings or scrape other ticket sites.

### Spotify (optional)

1. Create a Web API app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Add this exact redirect URI in the app settings: `http://127.0.0.1:8888/callback`.
3. Put the app's Client ID in `SPOTIFY_CLIENT_ID`.
4. In **My Artists**, choose **Connect Spotify**, approve access in your browser, then choose **Import top artists**.

The app uses Authorization Code with PKCE and requests only `user-top-read`. `SPOTIFY_CLIENT_SECRET` is included as a configuration placeholder for compatibility but is not used or sent by this PKCE flow. The access and refresh token cache is local at `.cache/spotify_token.json` and is ignored by Git.

As of October 2026, Spotify Development Mode requires the app owner to have an active Premium account; each app supports up to five allowlisted Spotify users. `GET /me/top/artists` requires the `user-top-read` scope and Spotify may still deny the request based on the app's access. If it does, add artists manually; the rest of Concert Radar works without Spotify.

### Gemini (optional)

1. Create a Gemini API key in [Google AI Studio](https://aistudio.google.com/apikey).
2. Set `GEMINI_API_KEY` in `.env`.
3. The default `GEMINI_MODEL=gemini-3.8-flash` is a currently documented stable model. Change it to another supported model ID if needed.
4. Use **Settings → Check Gemini connection** to make a live test request.

Gemini receives only the question, a short recent chat context, relevant artist preferences and compact saved event fields. It never receives Spotify tokens. If Gemini is missing, unavailable, out of quota, or returns an error, rule-based explanations and event search remain available. Verify current model access and usage limits in Google AI Studio; API availability and billing rules can depend on the account and region.

### Email alerts (optional)

Set `ALERT_EMAIL`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME` and `SMTP_PASSWORD`. For port 587 the app uses STARTTLS; for port 465 it uses SMTP over SSL. For Gmail, use an app password when required by Google account security settings. Enable alerts in the **Alerts** section only after entering valid SMTP details. A failed SMTP response is recorded as failed, not delivered. Successful notifications are deduplicated by event ID. Demo mode always blocks real email delivery.

## Windows Task Scheduler: every six hours

The laptop must be powered on, signed in as the Windows account that owns this project, and connected to the internet for a scheduled scan. The dashboard does not need to stay open.

1. In **Task Scheduler**, choose **Create Basic Task** and name it `Concert Radar scan`.
2. Choose **Daily** and set a start time.
3. Select **Start a program**.
4. Program/script: the full path to this project's `.venv\Scripts\python.exe`.
5. Add arguments: `main.py --live`.
6. Start in: the full project folder, for example `C:\Users\admin\Desktop\concert rader`.
7. Open the task's **Properties → Triggers → Edit** and enable **Repeat task every 6 hours**, with duration **1 day**. Ensure the task is enabled.
8. In **Conditions**, select the power/network conditions that fit your laptop. In **Settings**, allow the task to start on demand and stop/retry according to your preference.

Before scheduling live scans, add `TICKETMASTER_API_KEY` to `.env`, set `DEMO_MODE=false`, and run `python main.py --live` once by hand. To schedule demo refreshes instead, use `main.py --demo`.

The scanner uses a Windows process lock to prevent concurrent runs, writes a scan-history result even after a handled failure, and stores all scan state in SQLite.

## What is and is not verified

- Ticketmaster event dates, venues, ticket prices, presales and URLs are shown only when returned by Ticketmaster. Missing fields are left blank or labeled as not provided.
- Recommendation scores use deterministic artist-name/ID matching, preferred city/country, optional genre and actual listed price. Venue distance is not currently retrieved: the travel radius is saved as a preference but is not used to claim a distance.
- Budget answers can use listed ticket prices. Travel, food and lodging figures must be supplied by you and are estimates, not live quotes.
- Demo artists, dates, events, venues and prices are fictional examples and are separated from live records.
- The only live event source in this version is Ticketmaster Discovery API. The provider client is modular so a legitimate provider can be added later.

## Local data, revocation and troubleshooting

- SQLite database: `data/concert_radar.sqlite3`.
- Spotify token cache: `.cache/spotify_token.json`.
- To revoke Spotify access, remove Concert Radar in your Spotify account's **Apps** page; to clear local app data, stop the app and delete the `data` and `.cache` folders. This removes your saved artist list, events, preferences, notification history and local tokens.
- Never share `.env`, `.cache`, or the database. Do not commit them.
- **Python fails to launch:** install a standard 64-bit Python from [python.org](https://www.python.org/downloads/windows/) and confirm `py -0p` lists its executable.
- **Spotify callback fails:** ensure port 8888 is free and the registered redirect URI exactly matches the value in `.env`; use `127.0.0.1`, not `localhost`.
- **Spotify returns 403:** check Premium status, allowlist membership, Development Mode state and whether Spotify permits `user-top-read` for your app. Manual artists are the fallback.
- **Ticketmaster returns 401/403:** check the consumer key and app status. A valid key can still have limited availability.
- **No Indian events appear:** Ticketmaster's India coverage may be limited. Verify location and date filters, then try the artist search again later.
- **Gemini fails:** verify the key, supported model ID, account access, quotas and usage limits. Rule-based recommendations continue to work.
- **Email fails:** check the SMTP host/port, STARTTLS or SSL support, username, app password and recipient address. Inspect **Alerts → Notification history** for the sanitized result.
- **Scheduled task does not run:** verify the executable path, **Start in** folder, account permissions and `.env`; use Task Scheduler's **Last Run Result** and run the action manually once.

## Official API references checked

- [Spotify Authorization](https://developer.spotify.com/documentation/web-api/concepts/authorization), [PKCE flow](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow), [top artists endpoint](https://developer.spotify.com/documentation/web-api/reference/get-users-top-artists-and-tracks), and [February 2026 Development Mode guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide).
- [Ticketmaster Discovery API v2](https://developer.ticketmaster.com/products-and-docs/apis/discovery-manual/v2/).
- [Google Gen AI SDK](https://ai.google.dev/gemini-api/docs/libraries), [models](https://ai.google.dev/gemini-api/docs/models), and [API key setup](https://ai.google.dev/gemini-api/docs/api-key).
# concert-radar
