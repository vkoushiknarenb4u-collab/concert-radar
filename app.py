"""Concert Radar local Streamlit dashboard."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import streamlit as st

from config import Settings, load_settings
from database import Database
from gemini_client import GeminiClient, GeminiError, rule_based_answer
from matcher import match_artist, normalize_name
from notifications import send_event_email
from recommender import rank_events
from scanner import run_scan
from spotify_client import SpotifyClient, SpotifyError
from ticketmaster_client import ProviderError, TicketmasterClient


st.set_page_config(page_title="Concert Radar", page_icon="🎟️", layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
:root { --bg:#0b0d13; --panel:#131722; --muted:#9aa4b5; --mint:#54e6be; --purple:#a790ff; --line:#242b39; }
.stApp { background: radial-gradient(ellipse at 83% 0%, #1a1740 0, transparent 34%), var(--bg); color:#f3f4f8; }
html, body, [class*="css"] { font-family:'DM Sans',sans-serif; }
h1,h2,h3,.brand {font-family:'Space Grotesk',sans-serif; letter-spacing:-.035em;}
.block-container {max-width:1400px; padding-top:2rem; padding-bottom:4rem;}
[data-testid="stSidebar"] {background:#10131c; border-right:1px solid var(--line);}
[data-testid="stMetric"] {background:linear-gradient(145deg,#171c29,#121620); border:1px solid var(--line); border-radius:16px; padding:18px 20px;}
[data-testid="stMetricLabel"] {color:var(--muted);}
[data-testid="stMetricValue"] {color:#fff; font-family:'Space Grotesk',sans-serif;}
.eyebrow {color:var(--mint); text-transform:uppercase; letter-spacing:.14em; font-size:.72rem; font-weight:700;}
.subtle {color:var(--muted);}
.eventbox {background:linear-gradient(145deg,#171c29,#121620); border:1px solid #293143; border-radius:16px; padding:18px 20px; margin:10px 0;}
.eventbox.demo {border-color:#7259c7;}
.pill {display:inline-block; border-radius:999px; padding:3px 9px; font-size:.72rem; font-weight:700; background:#193c37; color:#7df2d0;}
button[kind="primary"] {background:linear-gradient(100deg,#54e6be,#a790ff); color:#09110f; border:0;}
div[data-testid="stExpander"] {border:1px solid var(--line); border-radius:12px;}
</style>
""", unsafe_allow_html=True)

SETTINGS = load_settings()
DB = Database(SETTINGS.database_path)
if not DB.get_preferences():
    DB.save_preferences(city=SETTINGS.preferred_city, country_code=SETTINGS.preferred_country_code,
        max_budget_inr=SETTINGS.max_ticket_budget_inr, max_travel_km=SETTINGS.max_travel_distance_km)

if "demo_mode" not in st.session_state:
    st.session_state.demo_mode = DB.get_setting("demo_mode", str(SETTINGS.demo_mode).lower()) == "true"


def _safe_event_list(*, demo: bool | None = None) -> list[dict[str, Any]]:
    current_demo = st.session_state.demo_mode if demo is None else demo
    events = DB.get_events(include_demo=True)
    return [e for e in events if bool(e.get("is_demo")) == current_demo]


def _preferences() -> dict[str, Any]:
    prefs = DB.get_preferences()
    return prefs or {"city": SETTINGS.preferred_city, "country_code": SETTINGS.preferred_country_code,
        "max_budget_inr": SETTINGS.max_ticket_budget_inr, "max_travel_km": SETTINGS.max_travel_distance_km, "genres": []}


def _artists() -> list[dict[str, Any]]:
    return DB.list_artists(include_demo=st.session_state.demo_mode)


def _status(configured: bool, name: str) -> str:
    return f"🟢 {name} configured" if configured else f"⚪ {name} not configured"


def _event_card(event: dict[str, Any], *, demo: bool | None = None) -> None:
    is_demo = bool(event.get("is_demo")) if demo is None else demo
    title = event.get("name") or "Event name not provided"
    st.markdown(f"#### {title} {'`DEMO · FICTIONAL`' if is_demo else ''}")
    fields = []
    place = ", ".join(filter(None, (event.get("venue"), event.get("city"), event.get("country"))))
    if place:
        fields.append(f"**Location:** {place}")
    when = " ".join(filter(None, (event.get("event_date"), event.get("event_time"))))
    if when:
        fields.append(f"**Date and time:** {when}")
    elif not event.get("event_date"):
        fields.append("**Date:** Not provided by the event provider")
    if event.get("price_min") is not None or event.get("price_max") is not None:
        low, high = event.get("price_min"), event.get("price_max")
        currency = event.get("currency") or "currency not provided"
        if low is not None and high is not None:
            price_text = f"{low:g}–{high:g} {currency}"
        elif low is not None:
            price_text = f"from {low:g} {currency}"
        else:
            price_text = f"up to {high:g} {currency}"
        fields.append(f"**Listed price:** {price_text}{' · sample price' if is_demo else ''}")
    else:
        fields.append("**Ticket price:** Not provided")
    if event.get("matched_artist"):
        fields.append(f"**Matched artist:** {event['matched_artist']}")
    if event.get("presale"):
        fields.append(f"**Presale:** {event['presale']}")
    if event.get("reasons"):
        fields.append("**Why it matches:** " + "; ".join(event["reasons"]))
    st.markdown("  \n".join(fields))
    if event.get("ticket_url"):
        st.link_button("Official ticket page", event["ticket_url"], key=f"ticket-{event.get('event_id')}")
    elif is_demo:
        st.caption("Demo events have no ticket page. No real concert or ticket availability is implied.")


def _render_ranked(events: list[dict[str, Any]], limit: int = 10) -> None:
    if not events:
        if st.session_state.demo_mode:
            st.info("No matching fictional demo records are available yet. Refresh demo data from Settings.")
        else:
            st.info("No matching saved event records were found. Run a new search to look for concerts.")
        return
    for event in events[:limit]:
        with st.container(border=True):
            _event_card(event)


def _relevant_events(question: str) -> list[dict[str, Any]]:
    events = _safe_event_list()
    ranked = rank_events(events, _artists(), _preferences())
    if not ranked:
        return []
    words = {w.casefold() for w in question.split() if len(w) > 3}
    if words:
        selected = [e for e in ranked if any(w in (e.get("name") or "").casefold() for w in words)
                    or any(w in (e.get("city") or "").casefold() for w in words)]
        if selected:
            return selected[:20]
    favorite_matches = [e for e in ranked if e.get("score", 0) > 0]
    return (favorite_matches or ranked)[:20]


with st.sidebar:
    st.markdown("<div class='eyebrow'>Local discovery</div><h2 class='brand'>🎟️ Concert Radar</h2>", unsafe_allow_html=True)
    st.caption("Your music taste, closer to the stage.")
    mode = st.toggle("Demo mode · fictional listings", value=st.session_state.demo_mode,
                     help="Demo events are clearly labeled and kept separate from real provider results.")
    if mode != st.session_state.demo_mode:
        st.session_state.demo_mode = mode
        DB.set_setting("demo_mode", str(mode).lower())
        if mode:
            run_scan(SETTINGS, db=DB, demo_mode=True)
    if st.session_state.demo_mode:
        st.info("Demo data is fictional. No APIs or email are used for demo scans.")
    page = st.radio("Navigate", ["Dashboard", "Discover Concerts", "My Artists", "AI Concert Assistant",
                                  "Alerts", "Settings", "Activity"], label_visibility="collapsed")
    st.markdown("---")
    st.caption(_status(SETTINGS.spotify_configured, "Spotify"))
    st.caption(_status(SETTINGS.ticketmaster_configured, "Ticketmaster"))
    st.caption(_status(SETTINGS.gemini_configured, "Gemini"))


if page == "Dashboard":
    st.markdown("<div class='eyebrow'>Your next live moment</div>", unsafe_allow_html=True)
    st.title("Concert dashboard")
    st.write("Discover performances that line up with your artists, city and budget.")
    if st.session_state.demo_mode:
        if not any(e.get("is_demo") for e in DB.get_events()):
            run_scan(SETTINGS, db=DB, demo_mode=True)
        st.warning("Demo mode is active. Every event below is fictional sample data.")
    prefs = _preferences()
    artists = _artists()
    events = _safe_event_list()
    upcoming = [e for e in events if e.get("event_date") and str(e["event_date"])[:10] >= date.today().isoformat()]
    matched = rank_events(upcoming, artists, prefs)
    scans = DB.list_scans(1)
    scan_label = scans[0]["status"] if scans else "Not scanned yet"
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Favourite artists", len([a for a in artists if a.get("source") != "demo"]))
    col2.metric("Upcoming matches", sum(bool(e.get("matched_artist_record")) for e in matched))
    col3.metric("New discoveries", scans[0].get("events_new", 0) if scans else 0)
    col4.metric("Recent scan", scan_label.replace("_", " ").title())
    left, right = st.columns([1.5, 1])
    with left:
        st.subheader("Recommended for you")
        _render_ranked(matched, 5)
    with right:
        st.subheader("Connections")
        st.write(_status(SETTINGS.spotify_configured, "Spotify"))
        st.write(_status(SETTINGS.ticketmaster_configured, "Ticketmaster"))
        st.write(_status(SETTINGS.gemini_configured, "Gemini"))
        st.write(f"**Preferred location:** {prefs.get('city')}, {prefs.get('country_code')}")
        st.write(f"**Ticket budget:** ₹{prefs.get('max_budget_inr', 0):,.0f}")
        if st.button("Run a concert scan", type="primary", width="stretch"):
            with st.spinner("Checking event listings…"):
                result = run_scan(SETTINGS, db=DB, demo_mode=st.session_state.demo_mode)
            (st.success if result["status"] == "success" else st.warning)(result["message"])
            st.rerun()
        st.caption("Ask Concert Assistant to compare events or plan an estimated budget.")


elif page == "Discover Concerts":
    st.markdown("<div class='eyebrow'>Search verified listings</div>", unsafe_allow_html=True)
    st.title("Discover concerts")
    if st.session_state.demo_mode:
        st.info("Searching only fictional demo samples. Turn off demo mode in the sidebar to use Ticketmaster results.")
    with st.form("event-search"):
        p1, p2, p3 = st.columns([1.2, 1, 1])
        artist_query = p1.text_input("Artist or event keyword", placeholder="e.g. A. R. Rahman")
        city_query = p2.text_input("City", value=_preferences().get("city", "Chennai"))
        country_query = p3.text_input("Country code", value=_preferences().get("country_code", "IN"), max_chars=2)
        d1, d2 = st.columns(2)
        date_from = d1.date_input("From date", value=date.today())
        date_to = d2.date_input("To date", value=date.today() + timedelta(days=365))
        search_clicked = st.form_submit_button("Search events", type="primary")
    if search_clicked:
        st.session_state.discover_error = ""
        st.session_state.discover_empty = ""
        st.session_state.discover_ids = []
        if date_to < date_from:
            st.session_state.discover_error = "The end date must be on or after the start date."
        elif st.session_state.demo_mode:
            st.session_state.discover_ids = [e["event_id"] for e in _safe_event_list()
                if (not artist_query or artist_query.casefold() in (e.get("name", "") + " " + str(e.get("matched_artist", ""))).casefold())
                and (not city_query or city_query.casefold() in (e.get("city") or "").casefold())
                and (not date_from or not e.get("event_date") or e["event_date"] >= date_from.isoformat())
                and (not date_to or not e.get("event_date") or e["event_date"] <= date_to.isoformat())]
        elif not SETTINGS.ticketmaster_configured:
            st.session_state.discover_error = "Add TICKETMASTER_API_KEY to .env to search real listings. The API is optional; manual artists and saved data still work."
        else:
            try:
                client = TicketmasterClient(SETTINGS.ticketmaster_api_key)
                records = client.search_events(keyword=artist_query.strip() or None, city=city_query.strip(),
                    country_code=country_query.strip().upper(), start_date=date_from, end_date=date_to)
                ids = []
                for record in records:
                    pairings = []
                    for artist in _artists():
                        matched, reasons = match_artist(artist, record)
                        if matched:
                            pairings.append((artist, reasons))
                    if pairings:
                        record["matched_artist"] = pairings[0][0]["name"]
                    DB.upsert_event(record)
                    for artist, reasons in pairings:
                        DB.save_match(artist["id"], record["event_id"], 70, reasons)
                        for attraction in (record.get("raw_json", {}).get("_embedded", {}).get("attractions", [])
                                           if isinstance(record.get("raw_json"), dict) else []):
                            if normalize_name(artist.get("name")) == normalize_name(attraction.get("name")) and attraction.get("id"):
                                DB.set_ticketmaster_artist_id(artist["id"], attraction["id"])
                                break
                    ids.append(record["event_id"])
                st.session_state.discover_ids = ids
                if not records:
                    st.session_state.discover_empty = "Ticketmaster returned no events for this search. Coverage in India may be limited."
            except ProviderError as exc:
                st.session_state.discover_error = str(exc)
    if st.session_state.get("discover_error"):
        st.warning(st.session_state.discover_error)
    if st.session_state.get("discover_empty"):
        st.info(st.session_state.pop("discover_empty"))
    ids = st.session_state.get("discover_ids", [])
    known = {e["event_id"]: e for e in DB.get_events(include_demo=True)}
    events = [known[eid] for eid in ids if eid in known]
    filters1, filters2, filters3 = st.columns(3)
    artist_filter = filters1.selectbox("Filter by artist", ["All"] + sorted({e.get("matched_artist") or "Unmatched" for e in events}))
    city_options = sorted({e.get("city") for e in events if e.get("city")})
    city_filter = filters2.selectbox("Filter by location", ["All"] + city_options)
    price_filter = filters3.checkbox("Only events with listed prices")
    prefs = _preferences()
    ranked = rank_events(events, _artists(), prefs)
    if artist_filter != "All":
        ranked = [e for e in ranked if (e.get("matched_artist") or "Unmatched") == artist_filter]
    if city_filter != "All":
        ranked = [e for e in ranked if e.get("city") == city_filter]
    if price_filter:
        ranked = [e for e in ranked if e.get("price_min") is not None or e.get("price_max") is not None]
    if not ranked:
        if ids:
            st.info("No events match the selected filters.")
        elif not st.session_state.demo_mode:
            st.info("Search for an artist or event above. Ticketmaster is the only live listings source currently enabled.")
        else:
            st.info("No fictional demo events match this search. Try ‘Neon Banyan’ or ‘Velvet Metro’ in Chennai.")
    else:
        st.caption(f"{len(ranked)} record(s) · recommendations ranked by local, deterministic rules")
        _render_ranked(ranked, 100)


elif page == "My Artists":
    st.markdown("<div class='eyebrow'>The artists you follow</div>", unsafe_allow_html=True)
    st.title("My artists")
    if st.session_state.demo_mode:
        st.info("Fictional sample artists are shown separately from your saved favourites.")
        demo_artists = [a for a in DB.list_artists() if a.get("source") == "demo"]
        if demo_artists:
            st.write("**Demo artists:** " + " · ".join(f"{a['name']} (fictional)" for a in demo_artists))
    with st.form("add-artist", clear_on_submit=True):
        name_col, genre_col, add_col = st.columns([2, 1.5, 1])
        new_name = name_col.text_input("Add an artist", placeholder="Artist name")
        new_genre = genre_col.text_input("Genre (optional)", placeholder="Indie, jazz…")
        add_submit = add_col.form_submit_button("Add artist", type="primary")
    if add_submit:
        if new_name.strip():
            DB.add_artist(new_name, genre=new_genre.strip() or None)
            st.success(f"Added {new_name.strip()} to your favourites.")
            st.rerun()
        st.warning("Enter an artist name first.")

    spotify = SpotifyClient(SETTINGS.spotify_client_id, SETTINGS.spotify_redirect_uri, SETTINGS.token_cache_path)
    with st.expander("Import top artists from Spotify"):
        if not SETTINGS.spotify_configured:
            st.caption("Optional. Add SPOTIFY_CLIENT_ID to .env and register the local redirect URI first.")
        else:
            s1, s2 = st.columns(2)
            if s1.button("Connect Spotify", disabled=not SETTINGS.spotify_configured):
                try:
                    url = spotify.start_authorization(open_browser=True)
                    st.session_state.spotify_auth_url = url
                    st.info("Your browser opened Spotify. Approve the request, then return and import your top artists.")
                except SpotifyError as exc:
                    st.error(str(exc))
            if st.session_state.get("spotify_auth_url"):
                st.link_button("Open Spotify authorization", st.session_state.spotify_auth_url)
            if s2.button("Import top artists"):
                try:
                    imports = spotify.top_artists()
                    count = 0
                    for item in imports:
                        DB.add_artist(item["name"], spotify_id=item.get("spotify_id"),
                            genre=", ".join(item.get("genres", [])[:4]) or None, source="spotify")
                        count += 1
                    st.success(f"Imported or refreshed {count} Spotify artist(s).")
                    st.rerun()
                except SpotifyError as exc:
                    st.warning(str(exc))
            st.caption("Spotify Development Mode requires the app owner to have Premium and each authorized user to be allowlisted (up to five users). Only the user-top-read scope is requested.")

    saved = [a for a in DB.list_artists() if a.get("source") != "demo"]
    st.subheader(f"Your saved favourites · {len(saved)}")
    if not saved:
        st.caption("Add artists manually or import them from Spotify. Manual artist lists work without API access.")
    for artist in saved:
        col_name, col_source, col_action = st.columns([3, 1.3, 1])
        col_name.write(f"**{artist['name']}**" + (f" · {artist['genre']}" if artist.get("genre") else ""))
        col_source.caption(artist.get("source", "manual").title())
        if col_action.button("Remove", key=f"remove-{artist['id']}"):
            DB.remove_artist(artist["id"])
            st.rerun()
    if saved:
        with st.expander("Edit an artist"):
            selected_id = st.selectbox("Choose artist", [a["id"] for a in saved],
                format_func=lambda item: next(a["name"] for a in saved if a["id"] == item))
            selected = next(a for a in saved if a["id"] == selected_id)
            edit_name = st.text_input("Name", value=selected["name"], key="artist-edit-name")
            edit_genre = st.text_input("Genre", value=selected.get("genre") or "", key="artist-edit-genre")
            if st.button("Save artist changes"):
                try:
                    DB.update_artist(selected_id, name=edit_name, genre=edit_genre)
                    st.success("Artist updated.")
                    st.rerun()
                except Exception:
                    st.error("Could not update this artist. Check that its name is unique.")


elif page == "AI Concert Assistant":
    st.markdown("<div class='eyebrow'>Ask about your saved records</div>", unsafe_allow_html=True)
    st.title("Concert assistant")
    st.caption("Recommendations are ranked by the app. Gemini can explain them, but it does not supply concert listings.")
    prompt_cols = st.columns(3)
    suggestions = ["Which concerts fit my budget?", "Compare the best matching events", "Help me plan a concert budget"]
    for i, prompt in enumerate(suggestions):
        if prompt_cols[i].button(prompt, key=f"suggest-{i}"):
            st.session_state.suggested_prompt = prompt
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    for entry in st.session_state.chat_history:
        with st.chat_message(entry["role"]):
            st.markdown(entry["content"])
    with st.form("chat-form", clear_on_submit=True):
        question = st.text_input("Ask about concerts, comparisons or a budget", value=st.session_state.pop("suggested_prompt", ""),
                                 placeholder="Which concerts should I attend this month?")
        ask = st.form_submit_button("Ask assistant", type="primary")
    actions = st.columns([1, 5])
    if actions[0].button("Clear chat"):
        st.session_state.chat_history = []
        st.session_state.last_context_events = []
        st.session_state.last_answer_label = ""
        st.rerun()
    if ask and question.strip():
        context_events = _relevant_events(question.strip())
        history = st.session_state.chat_history[-6:]
        if SETTINGS.gemini_configured:
            try:
                answer = GeminiClient(SETTINGS.gemini_api_key, SETTINGS.gemini_model).answer(
                    question.strip(), context_events, _preferences(), _artists(), history)
                label = "Gemini · grounded in saved records"
            except GeminiError as exc:
                answer = rule_based_answer(question, context_events, demo=st.session_state.demo_mode)
                label = "Rule-based response · Gemini unavailable"
                st.session_state.gemini_error = str(exc)
        else:
            answer = rule_based_answer(question, context_events, demo=st.session_state.demo_mode)
            label = "Rule-based response · Gemini is not configured"
        if st.session_state.demo_mode:
            answer = "**Fictional demo data:** " + answer
        st.session_state.chat_history.extend([{"role": "user", "content": question.strip()},
                                              {"role": "assistant", "content": answer}])
        st.session_state.last_answer_label = label
        st.session_state.last_context_events = context_events
        st.rerun()
    if st.session_state.get("last_answer_label"):
        st.caption(st.session_state.last_answer_label)
    gemini_error = st.session_state.pop("gemini_error", None)
    if gemini_error:
        st.warning(gemini_error)
    underlying = st.session_state.get("last_context_events", [])
    if underlying:
        st.subheader("Event records used for the latest answer")
        _render_ranked(underlying, 20)
    elif st.session_state.chat_history:
        st.info("No saved events were supplied to the latest answer. Run a new search to find current listings.")
    st.markdown("**Try:** compare two events · find shows near Chennai · plan a budget with your own travel estimate · what details are not confirmed?")
    with st.expander("Build a budget using your own estimates"):
        estimate_events = _safe_event_list()
        if not estimate_events:
            st.caption("Save or discover an event first. Entered travel and meal values are your estimates, not live quotes.")
        else:
            selected_event_id = st.selectbox("Choose an event", [e["event_id"] for e in estimate_events],
                format_func=lambda key: next(e["name"] for e in estimate_events if e["event_id"] == key))
            chosen_event = next(e for e in estimate_events if e["event_id"] == selected_event_id)
            ec1, ec2, ec3 = st.columns(3)
            travel_input = ec1.text_input("Your travel estimate (INR)", placeholder="Optional", key="budget-travel")
            food_input = ec2.text_input("Your food estimate (INR)", placeholder="Optional", key="budget-food")
            hotel_input = ec3.text_input("Your stay estimate (INR)", placeholder="Optional", key="budget-hotel")
            user_estimates = []
            estimate_error = False
            for field, label in ((travel_input, "travel"), (food_input, "food"), (hotel_input, "stay")):
                if field.strip():
                    try:
                        amount = float(field.replace(",", "").strip())
                        if amount < 0:
                            raise ValueError
                        user_estimates.append((label, amount))
                    except ValueError:
                        estimate_error = True
            if estimate_error:
                st.warning("Enter non-negative numbers in INR, or leave an estimate blank.")
            else:
                supplied = sum(value for _, value in user_estimates)
                ticket_known = (chosen_event.get("currency") or "").upper() == "INR" and (
                    chosen_event.get("price_min") is not None or chosen_event.get("price_max") is not None)
                if ticket_known:
                    low = chosen_event.get("price_min") if chosen_event.get("price_min") is not None else chosen_event.get("price_max")
                    high = chosen_event.get("price_max") if chosen_event.get("price_max") is not None else low
                    st.write(f"**Estimated total range:** ₹{low + supplied:,.0f}–₹{high + supplied:,.0f}")
                    st.caption("Ticket figures are listed by the provider. Travel, food and stay are your estimates: " +
                               (", ".join(f"{label} ₹{amount:,.0f}" for label, amount in user_estimates) or "none entered") +
                               ". This total excludes any unentered costs and is not a live travel or hotel quote.")
                elif user_estimates:
                    st.write(f"**User estimate subtotal:** ₹{supplied:,.0f} · ticket price not provided in INR")
                    st.caption("This subtotal contains only the estimates you entered; it is not a complete trip budget.")
                else:
                    st.caption("This event has no provider-supplied INR ticket price. Enter your own estimates to see a partial subtotal.")


elif page == "Alerts":
    st.markdown("<div class='eyebrow'>Only new matches, once each</div>", unsafe_allow_html=True)
    st.title("Alerts")
    enabled = DB.get_setting("alerts_enabled", "false").casefold() == "true"
    new_enabled = st.toggle("Enable email alerts", value=enabled, disabled=st.session_state.demo_mode)
    if new_enabled != enabled:
        if new_enabled and not SETTINGS.email_configured:
            st.warning("Configure ALERT_EMAIL and all SMTP settings in .env before enabling alerts.")
            new_enabled = False
        DB.set_setting("alerts_enabled", str(new_enabled).lower())
    if st.session_state.demo_mode:
        st.info("Email delivery is disabled in demo mode. Preview messages below are simulated and will never send.")
    elif not SETTINGS.email_configured:
        st.caption("Email is optional. Add ALERT_EMAIL, SMTP_HOST, SMTP_PORT, SMTP_USERNAME and SMTP_PASSWORD to .env.")
    else:
        saved_matches = {m["event_id"] for m in DB.get_matches(include_demo=False)}
        actionable = [e for e in event_list if e.get("event_id") in saved_matches or e.get("matched_artist")]
        if actionable:
            st.subheader("Matching events")
            st.caption("Sending again is an explicit request and may deliver a duplicate email.")
            for event in actionable[:30]:
                col_event, col_send = st.columns([3, 1])
                col_event.write(f"**{event['name']}** · {event.get('city') or 'location not listed'} · " +
                                ("Already sent" if DB.notification_sent(event["event_id"]) else "No successful alert yet"))
                if col_send.button("Send alert again" if DB.notification_sent(event["event_id"]) else "Send alert",
                                   key=f"alert-{event['event_id']}"):
                    ok, message = send_event_email(DB, SETTINGS, event,
                        explanation=None, force=DB.notification_sent(event["event_id"]))
                    (st.success if ok else st.warning)(message)
    event_list = _safe_event_list()
    st.subheader("Notification preview")
    if st.session_state.demo_mode:
        for event in event_list[:3]:
            st.markdown(f"**Simulated preview · {event['name']}**")
            st.code(f"Event: {event['name']}\nVenue: {event.get('venue')}\nDate: {event.get('event_date')}\nSample price: {event.get('price_min')} {event.get('currency')}\nNo message sent.", language=None)
    else:
        st.caption("New matched events generate an email only after the next scan. A successful SMTP delivery is recorded to prevent duplicate alerts.")
    st.subheader("Notification history")
    history_rows = DB.list_notifications()
    if history_rows:
        st.dataframe([{key: row.get(key) for key in ("attempted_at", "name", "city", "event_date", "status", "message")}
                      for row in history_rows], width="stretch", hide_index=True)
    else:
        st.caption("No notification attempts have been recorded.")


elif page == "Settings":
    st.markdown("<div class='eyebrow'>Your local setup</div>", unsafe_allow_html=True)
    st.title("Settings")
    prefs = _preferences()
    with st.form("preferences"):
        c1, c2 = st.columns(2)
        city = c1.text_input("Preferred city", value=prefs.get("city", "Chennai"))
        country = c2.text_input("Country code", value=prefs.get("country_code", "IN"), max_chars=2)
        c3, c4 = st.columns(2)
        budget = c3.number_input("Maximum ticket budget (INR)", min_value=0.0, value=float(prefs.get("max_budget_inr") or 2000), step=100.0)
        distance = c4.number_input("Travel radius preference (km)", min_value=0.0, value=float(prefs.get("max_travel_km") or 100), step=10.0)
        genre_text = st.text_input("Preferred genres · comma separated", value=", ".join(prefs.get("genres", [])))
        saved = st.form_submit_button("Save preferences", type="primary")
    if saved:
        DB.save_preferences(city=city, country_code=country, max_budget_inr=budget,
            max_travel_km=distance, genres=[g.strip() for g in genre_text.split(",") if g.strip()])
        st.success("Preferences saved.")
        st.rerun()
    st.caption("Travel distance is a preference only. This version does not retrieve venue coordinates or live distance data; searches use your selected city and country.")

    st.subheader("Service setup")
    for label, configured, hint in [
        ("Spotify", SETTINGS.spotify_configured, "SPOTIFY_CLIENT_ID and the exact local callback URI"),
        ("Ticketmaster", SETTINGS.ticketmaster_configured, "TICKETMASTER_API_KEY"),
        ("Gemini", SETTINGS.gemini_configured, "GEMINI_API_KEY and GEMINI_MODEL"),
        ("Email", SETTINGS.email_configured, "ALERT_EMAIL and SMTP host, port, username and password"),
    ]:
        st.write(("✅ " if configured else "○ ") + f"**{label}:** " + ("Configured (not tested here)" if configured else f"Missing {hint}"))
    with st.expander("Check Ticketmaster connection"):
        if st.button("Search a small Chennai sample", disabled=not SETTINGS.ticketmaster_configured):
            try:
                found = TicketmasterClient(SETTINGS.ticketmaster_api_key, max_pages=1).search_events(
                    city=prefs.get("city", "Chennai"), country_code=prefs.get("country_code", "IN"),
                    start_date=date.today(), end_date=date.today() + timedelta(days=365))
                st.success(f"Ticketmaster responded. {len(found)} event(s) returned; nothing was saved by this check.")
            except ProviderError as exc:
                st.warning(str(exc))
    with st.expander("Check Gemini connection"):
        if st.button("Ask Gemini to check", disabled=not SETTINGS.gemini_configured):
            try:
                response = GeminiClient(SETTINGS.gemini_api_key, SETTINGS.gemini_model).answer(
                    "Reply with a short confirmation that this connection check worked.", [], prefs, [])
                st.success("Gemini responded: " + response)
            except GeminiError as exc:
                st.warning(str(exc))
    st.write(f"**Gemini model:** `{SETTINGS.gemini_model}` (change GEMINI_MODEL in .env)")
    st.write(f"**Scheduled scan interval:** {SETTINGS.search_interval_hours} hours. Configure it in Windows Task Scheduler.")
    database_path = Path(SETTINGS.database_path).resolve()
    app_root = Path(__file__).resolve().parent
    try:
        database_display = database_path.relative_to(app_root)
    except ValueError:
        database_display = database_path
    st.write(f"**Local database:** `{database_display}`")
    if st.button("Run a scan now", type="primary"):
        result = run_scan(SETTINGS, db=DB, demo_mode=st.session_state.demo_mode)
        (st.success if result["status"] == "success" else st.warning)(result["message"])
        st.rerun()
    st.subheader("Secrets and privacy")
    st.caption("Credentials are read from .env and are never shown here. Spotify tokens stay in a local ignored cache. Gemini receives only relevant saved event records and preferences, never Spotify access tokens.")


elif page == "Activity":
    st.markdown("<div class='eyebrow'>What Concert Radar checked</div>", unsafe_allow_html=True)
    st.title("Activity")
    rows = DB.list_scans(100)
    if not rows:
        st.info("No scans recorded yet.")
    else:
        st.dataframe([{key: row.get(key) for key in ("started_at", "finished_at", "source", "status", "events_found", "events_new", "notifications_sent", "message")}
                      for row in rows], width="stretch", hide_index=True)
    st.caption("Activity contains sanitized status messages. API keys and authorization tokens are not recorded.")
