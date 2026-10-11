"""Deterministic, conservative artist-to-event matching."""

from __future__ import annotations

import re
import unicodedata
import json
from difflib import SequenceMatcher
from typing import Any


def normalize_name(value: str | None) -> str:
    """Normalize case, accents, punctuation and whitespace for comparisons."""
    if not value:
        return ""
    folded = unicodedata.normalize("NFKD", value).casefold()
    chars: list[str] = []
    previous_base: str | None = None
    for character in folded:
        category = unicodedata.category(character)
        if category.startswith("M"):
            if previous_base and "LATIN" in unicodedata.name(previous_base, ""):
                continue
            if chars and chars[-1] != " ":
                chars.append(character)
            continue
        if character.isalnum():
            chars.append(character)
            previous_base = character
        elif chars and chars[-1] != " ":
            chars.append(" ")
            previous_base = None
    words = "".join(chars).strip().split()
    # Keep common initial punctuation variants equivalent: “A. R. Rahman” and “AR Rahman”.
    collapsed: list[str] = []
    index = 0
    while index < len(words):
        if len(words[index]) == 1 and index + 1 < len(words):
            initials = []
            while index < len(words) and len(words[index]) == 1:
                initials.append(words[index])
                index += 1
            collapsed.append("".join(initials))
            continue
        collapsed.append(words[index])
        index += 1
    return " ".join(collapsed)


def _candidate_names(event: dict[str, Any]) -> list[str]:
    values = [event.get("name", "")]
    values.extend(event.get("attractions", []) or [])
    raw = event.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if isinstance(raw, dict):
        values.extend(raw.get("attractions", []) or [])
        embedded = raw.get("_embedded", {})
        values.extend(a.get("name", "") for a in embedded.get("attractions", []) or [])
    return [v if isinstance(v, str) else v.get("name", "") for v in values if v]


def _close_phrase_match(target: str, candidate: str) -> bool:
    target_words = target.split()
    candidate_words = candidate.split()
    if not target_words or not candidate_words:
        return False
    if len(target) < 7:
        return False
    window = len(target_words)
    threshold = 0.96 if len(target_words) == 1 else 0.93
    for start in range(max(1, len(candidate_words) - window + 1)):
        phrase = " ".join(candidate_words[start:start + window])
        if abs(len(phrase) - len(target)) > max(2, len(target) * 0.12):
            continue
        if SequenceMatcher(None, target, phrase).ratio() >= threshold:
            return True
    return False


def match_artist(artist: dict[str, Any], event: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return match and evidence. IDs are compared only within Ticketmaster."""
    reasons: list[str] = []
    tm_id = artist.get("ticketmaster_id")
    event_ids = set(event.get("attraction_ids", []) or [])
    raw = event.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if isinstance(raw, dict):
        event_ids.update(raw.get("attraction_ids", []) or [])
        event_ids.update(
            item.get("id") for item in raw.get("_embedded", {}).get("attractions", []) or [] if item.get("id")
        )
    if tm_id and tm_id in event_ids:
        return True, ["Ticketmaster artist ID matches"]

    target = normalize_name(artist.get("name"))
    if not target:
        return False, []
    candidates = [normalize_name(n) for n in _candidate_names(event)]
    candidates = [n for n in candidates if n]
    if len(target) <= 2:
        if target in candidates:
            return True, [f"Artist name matches {artist.get('name', '').strip()}"]
        return False, []
    if any(re.search(rf"(?:^| ){re.escape(target)}(?:$| )", candidate) for candidate in candidates):
        reasons.append(f"Artist name matches {artist.get('name', '').strip()}")
        return True, reasons
    if any(_close_phrase_match(target, candidate) for candidate in candidates):
        reasons.append(f"Artist name closely matches {artist.get('name', '').strip()}")
        return True, reasons
    return False, []


def match_events(artists: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one record per artist/event pairing that meets conservative rules."""
    output: list[dict[str, Any]] = []
    for event in events:
        for artist in artists:
            matched, reasons = match_artist(artist, event)
            if matched:
                output.append({"artist": artist, "event": event, "reasons": reasons})
    return output
