from matcher import match_artist, normalize_name


def test_normalize_name_removes_accents_punctuation_and_extra_spaces():
    assert normalize_name("  Beyoncé  &  Jay-Z! ") == "beyonce jay z"
    assert normalize_name("A. R. Rahman") == normalize_name("AR Rahman")


def test_name_normalization_keeps_indian_scripts():
    tamil_name = "இசையரசன்"
    assert normalize_name(tamil_name)
    assert match_artist({"name": tamil_name}, {"name": tamil_name})[0]


def test_matches_punctuation_and_case_variations():
    artist = {"name": "A.R. Rahman"}
    event = {"name": "AR Rahman Live in Concert"}
    assert match_artist(artist, event)[0]


def test_matches_ticketmaster_artist_id():
    artist = {"name": "Some Artist", "ticketmaster_id": "K8vZfoo"}
    event = {"name": "Live show", "attraction_ids": ["K8vZfoo"]}
    matched, reasons = match_artist(artist, event)
    assert matched
    assert "Ticketmaster artist ID matches" in reasons


def test_does_not_match_unrelated_artist_substring():
    artist = {"name": "Art"}
    event = {"name": "Cartwheel Festival"}
    assert not match_artist(artist, event)[0]


def test_does_not_fuzzy_match_short_names():
    assert not match_artist({"name": "U2"}, {"name": "You Too Festival"})[0]


def test_controlled_fuzzy_match_allows_minor_variation():
    matched, _ = match_artist({"name": "The Lumineers"}, {"name": "The Lumineer Live"})
    assert matched
