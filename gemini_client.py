"""Small grounded Gemini adapter with an honest rule-based fallback."""

from __future__ import annotations

import json
from datetime import date
from typing import Any


class GeminiError(RuntimeError):
    pass


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    fields = ("event_id", "source", "is_demo", "name", "matched_artist", "venue", "city", "country", "event_date",
              "event_time", "ticket_url", "price_min", "price_max", "currency", "presale", "genres", "score", "reasons")
    return {key: event[key] for key in fields if event.get(key) is not None}


def rule_based_answer(question: str, events: list[dict[str, Any]], *, demo: bool = False) -> str:
    """Provide useful local guidance without implying a model was called."""
    if not events:
        prefix = "In the demo data, " if demo else "In your saved records, "
        return prefix + "I couldn't find a relevant event. Run a new search to look for concerts."
    question_l = question.casefold()
    if any(word in question_l for word in ("compare", "which", "recommend", "attend", "best", "month", "budget")):
        chosen = events[:3]
        lines = ["Based on the saved event records and your deterministic preference scores:"]
        for item in chosen:
            location = ", ".join(filter(None, (item.get("venue"), item.get("city")))) or "location not listed"
            when = item.get("event_date") or "date not listed"
            price = ""
            if item.get("price_min") is not None:
                currency = item.get("currency") or "listed currency"
                price = f" Listed prices start at {item['price_min']:g} {currency}."
            elif item.get("price_max") is not None:
                currency = item.get("currency") or "listed currency"
                price = f" Listed price is up to {item['price_max']:g} {currency}."
            lines.append(f"• {item.get('name')} — {location}, {when}.{price}")
        if demo:
            lines.append("These are fictional demo records, not actual concert listings.")
        return "\n".join(lines)
    prefix = "The demo records show " if demo else "The saved records show "
    return prefix + f"{len(events)} relevant event(s). Concert details below come from the stored event records."


class GeminiClient:
    def __init__(self, api_key: str, model: str = "gemini-3.8-flash", *, timeout_ms: int = 15000):
        self.api_key = api_key
        self.model = model
        self.timeout_ms = timeout_ms

    def answer(self, question: str, events: list[dict[str, Any]], preferences: dict[str, Any] | None = None,
               artists: list[dict[str, Any]] | None = None, history: list[dict[str, str]] | None = None) -> str:
        if not self.api_key:
            raise GeminiError("Gemini API key is not configured.")
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise GeminiError("Install project dependencies to enable Gemini.") from exc
        context = {
            "as_of_date": date.today().isoformat(),
            "preferences": {
                key: (preferences or {}).get(key)
                for key in ("city", "country_code", "max_budget_inr", "max_travel_km", "genres")
                if (preferences or {}).get(key) is not None
            },
            "favorite_artists": [a.get("name") for a in (artists or []) if a.get("source") != "demo"][:30],
            "events": [_compact_event(e) for e in events[:30]],
        }
        recent = [{"role": m.get("role"), "content": str(m.get("content", ""))[:1000]}
                  for m in (history or [])[-6:] if m.get("role") in {"user", "assistant"}]
        prompt = (
            "You are Concert Radar's concert planning assistant. Treat the JSON records below as the complete factual "
            "source of concert information. Do not invent or infer event names, dates, times, locations, ticket prices, "
            "availability, links, presales, travel distance, or event existence. If a field is absent, say it was not "
            "provided. If no supplied event is relevant, say no matching saved records were found and suggest running "
            "a search. If is_demo is true, clearly call the event fictional sample data. Explain preference scores/reasons as application rankings, not model decisions. Distinguish "
            "confirmed event facts from clearly labeled estimates. Never claim transport or hotel prices are live. "
            "Do not mention information absent from the records. Keep the answer concise.\n\n"
            f"Verified application context (JSON):\n{json.dumps(context, ensure_ascii=False)}\n\n"
        )
        if recent:
            prompt += f"Recent chat context (JSON):\n{json.dumps(recent, ensure_ascii=False)}\n\n"
        prompt += f"User question: {question[:1200]}"
        try:
            client = genai.Client(api_key=self.api_key, http_options=types.HttpOptions(timeout=self.timeout_ms))
            response = client.models.generate_content(model=self.model, contents=prompt)
            text = getattr(response, "text", None)
            if not text or not text.strip():
                raise GeminiError("Gemini returned an empty answer.")
            return text.strip()
        except GeminiError:
            raise
        except Exception as exc:
            # Do not surface SDK exception text: some errors can include request details.
            name = type(exc).__name__.casefold()
            status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            if "timeout" in name:
                detail = "Gemini took too long to respond. Try again in a moment."
            elif status_code == 429 or "resourceexhausted" in name or "quota" in name:
                detail = "Gemini quota is currently unavailable. You can still use the saved event recommendations."
            elif status_code in {401, 403, 404} or "permission" in name or "auth" in name or "api" in name:
                detail = "Gemini rejected the configured API key or model. Check the Gemini settings."
            else:
                detail = "Gemini is temporarily unavailable. You can still use the saved event recommendations."
            raise GeminiError(detail) from exc


def notification_explanation(api_key: str, model: str, event: dict[str, Any], artist_name: str) -> str | None:
    if not api_key:
        return None
    try:
        return GeminiClient(api_key, model).answer(
            f"In one short sentence, why may this event match favorite artist {artist_name}?",
            [event], artists=[{"name": artist_name, "source": "manual"}],
        )
    except GeminiError:
        return None
