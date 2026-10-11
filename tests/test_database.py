from database import Database


def sample_event(event_id="tm-1"):
    return {"event_id": event_id, "source": "ticketmaster", "name": "A show",
            "city": "Chennai", "raw_json": {"_embedded": {"attractions": [{"id": "artist-1", "name": "An Artist"}]},
                                                  "classifications": [{"genre": {"name": "Rock"}}]}}


def test_event_ids_prevent_duplicates_and_keep_first_discovery(db):
    assert db.upsert_event(sample_event()) is True
    first = db.get_event("tm-1")["first_discovered_at"]
    assert db.upsert_event({**sample_event(), "name": "Updated show"}) is False
    stored = db.get_event("tm-1")
    assert stored["name"] == "Updated show"
    assert stored["first_discovered_at"] == first


def test_sqlite_persists_preferences_and_artist_across_instances(tmp_path):
    path = tmp_path / "radar.db"
    db = Database(path)
    db.save_preferences(city="Chennai", country_code="IN", max_budget_inr=1500, max_travel_km=50, genres=["rock"])
    db.add_artist("An Artist")
    reopened = Database(path)
    assert reopened.get_preferences()["genres"] == ["rock"]
    assert reopened.list_artists()[0]["name"] == "An Artist"


def test_event_hydration_restores_attractions_and_genre(db):
    db.upsert_event(sample_event())
    item = db.get_events()[0]
    assert item["attractions"] == ["An Artist"]
    assert item["attraction_ids"] == ["artist-1"]
    assert item["genres"] == ["Rock"]


def test_notification_duplicate_check_only_counts_success(db):
    db.upsert_event(sample_event())
    db.record_notification("tm-1", "listener@example.com", "failed", "provider error")
    assert not db.notification_sent("tm-1")
    db.record_notification("tm-1", "listener@example.com", "sent", "ok")
    assert db.notification_sent("tm-1")
