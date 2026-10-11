import sys
import types

import pytest

from gemini_client import GeminiClient, GeminiError


def install_fake_genai(monkeypatch, client_factory):
    google = types.ModuleType("google")
    google.__path__ = []
    genai = types.ModuleType("google.genai")
    genai.Client = client_factory
    genai_types = types.ModuleType("google.genai.types")
    genai_types.HttpOptions = lambda **kwargs: kwargs
    google.genai = genai
    genai.types = genai_types
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", genai_types)


def test_answer_sends_only_relevant_records_and_grounding_rules(monkeypatch):
    captured = {}

    class FakeModels:
        def generate_content(self, *, model, contents):
            captured["model"] = model
            captured["prompt"] = contents
            return types.SimpleNamespace(text="The stored event is in Chennai.")

    class FakeClient:
        def __init__(self, **_kwargs):
            self.models = FakeModels()

    install_fake_genai(monkeypatch, FakeClient)
    answer = GeminiClient("key", "test-model").answer("Which event?", [
        {"event_id": "known-1", "name": "Real provider record", "city": "Chennai", "ticket_url": "https://ticket.example"}
    ], {"city": "Chennai"}, [{"name": "Artist", "source": "manual"}])
    assert answer == "The stored event is in Chennai."
    assert captured["model"] == "test-model"
    assert "known-1" in captured["prompt"]
    assert "Do not invent or infer event names" in captured["prompt"]
    assert "Spotify" not in captured["prompt"]


def test_gemini_quota_failure_is_friendly(monkeypatch):
    class FakeModels:
        def generate_content(self, **_kwargs):
            raise type("ResourceExhausted", (Exception,), {})()

    class FakeClient:
        def __init__(self, **_kwargs):
            self.models = FakeModels()

    install_fake_genai(monkeypatch, FakeClient)
    with pytest.raises(GeminiError, match="quota"):
        GeminiClient("key").answer("Question", [])


def test_gemini_auth_failure_is_friendly(monkeypatch):
    class FakeModels:
        def generate_content(self, **_kwargs):
            raise type("PermissionDenied", (Exception,), {})()

    class FakeClient:
        def __init__(self, **_kwargs):
            self.models = FakeModels()

    install_fake_genai(monkeypatch, FakeClient)
    with pytest.raises(GeminiError, match="rejected"):
        GeminiClient("key").answer("Question", [])
