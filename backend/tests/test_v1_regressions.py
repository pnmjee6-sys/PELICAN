"""Regression tests for V1 privacy, evidence, and recall failures."""

from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from mem0.memory.main import SQLiteManager
from starlette.testclient import TestClient

from auth import AuthenticatedUser, verify_supabase_token
from memory_manager import MemoryManager
from preference_engine import PreferenceEngine, extract_explanation_evidence
from security import classify_text
from server import app, get_current_user, get_mem_manager, get_pref_engine
from settings import load_settings


@pytest.mark.parametrize("app_env,allow", [("production", "true"), ("test", "false"), ("production", "false")])
def test_test_tokens_need_both_test_flags(monkeypatch, app_env, allow):
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("ALLOW_TEST_AUTH", allow)
    with pytest.raises(HTTPException) as error:
        verify_supabase_token("test-bearer-attacker")
    assert error.value.status_code == 401


def test_test_token_allowed_only_when_both_flags_set(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ALLOW_TEST_AUTH", "true")
    assert verify_supabase_token("test-bearer-audit").user_id == "audit"


@pytest.mark.parametrize("text", [
    "My home address is 123 Test Street.",
    "My phone number is +1 555 123 4567.",
    "My religion is Buddhism.",
    "Reach me at user@example.com.",
])
def test_personal_details_require_approval(text):
    assert classify_text(text) == "sensitive"


def test_ordinary_project_fact_remains_general():
    assert classify_text("My HackNext project uses MongoDB Atlas.") == "general"


@pytest.mark.parametrize("text,key", [
    ("Explain this in very simple language.", "simple_eli5"),
    ("Please make every technical term easy to understand.", "simple_eli5"),
    ("I prefer examples and detailed explanations.", "detailed_deep_dive"),
    ("Can you break it into small steps?", "step_by_step"),
])
def test_natural_explanation_requests_are_learning_evidence(text, key):
    assert key in [item[0] for item in extract_explanation_evidence(text)]


@pytest.mark.parametrize("score,expected", [(0.01, 0), (0.60, 0), (0.72, 1), (0.81, 1)])
def test_recall_discards_weak_vector_matches(score, expected):
    class Collection:
        def aggregate(self, pipeline):
            assert pipeline[0]["$vectorSearch"]["filter"] == {"payload.user_id": {"$eq": "audit-user"}}
            return [{
                "_id": "memory-1", "score": score,
                "payload": {"user_id": "audit-user", "status": "active", "classification": "general", "data": "Uses Tailwind CSS"},
            }]

        def find(self, _query):
            return []

    manager = object.__new__(MemoryManager)
    manager.collection = Collection()
    manager.settings = SimpleNamespace(mongodb_vector_index_name="scoped")
    manager.embed_text = lambda _text: [1.0, 0.0]
    assert len(manager.search("Tell me a joke", "audit-user")["general_memories"]) == expected


def test_missing_privacy_classification_returns_no_content():
    class Collection:
        def aggregate(self, _pipeline):
            return [{"_id": "memory-1", "score": 0.99, "payload": {
                "user_id": "audit-user", "status": "active", "data": "A private fact",
            }}]

        def find(self, _query):
            return []

    manager = object.__new__(MemoryManager)
    manager.collection = Collection()
    manager.settings = SimpleNamespace(mongodb_vector_index_name="scoped")
    manager.embed_text = lambda _text: [1.0, 0.0]
    assert manager.search("private", "audit-user")["results"] == []


def test_index_catchup_fallback_uses_atlas_cosine_score_scale():
    class Collection:
        def aggregate(self, _pipeline):
            return []

        def find(self, query):
            assert query["payload.user_id"] == "audit-user"
            return [{
                "_id": "memory-1", "embedding": [0.5, 0.0],
                "payload": {"user_id": "audit-user", "status": "active", "classification": "general", "data": "Uses Tailwind CSS"},
            }]

    manager = object.__new__(MemoryManager)
    manager.collection = Collection()
    manager.settings = SimpleNamespace(mongodb_vector_index_name="scoped")
    manager.embed_text = lambda _text: [0.25, 0.0]
    result = manager.search("Tailwind", "audit-user")
    assert len(result["general_memories"]) == 1
    assert result["general_memories"][0]["score"] == pytest.approx(1.0)


def test_retry_after_provider_write_reuses_existing_event_memory():
    class Collection:
        def find(self, query):
            assert query == {"payload.user_id": "audit-user", "payload.source_event_key": "audit-user:event-1"}
            return [{
                "_id": "memory-1",
                "payload": {"user_id": "audit-user", "data": "Uses Tailwind CSS", "classification": "general"},
            }]

    class Provider:
        def add(self, *_args, **_kwargs):
            raise AssertionError("Provider must not be called again for the same event")

    manager = object.__new__(MemoryManager)
    manager.collection = Collection()
    manager.memory = Provider()
    manager.settings = SimpleNamespace(gemini_api_key="synthetic-key")
    result = manager.add("I use Tailwind CSS", "audit-user", {"source_event_key": "audit-user:event-1"})
    assert result["results"][0]["id"] == "memory-1"


def test_failed_save_and_retry_count_as_one_observation():
    load_settings(require_gemini=False)
    engine = PreferenceEngine()
    user_id = f"retry-regression-{uuid.uuid4().hex[:12]}"
    event_id = f"event-{uuid.uuid4().hex[:12]}"
    text = "Please explain step by step how caching works."
    message_hash = hashlib.sha256(text.encode()).hexdigest()
    try:
        first = engine.process_message(user_id, "chat-a", text, event_id=event_id)
        assert first["status"] == "pending"
        assert engine.observations_col.count_documents({"user_id": user_id}) == 0
        engine.mark_event_failed(user_id, message_hash, "SyntheticFailure", event_id=event_id)
        retry = engine.process_message(user_id, "chat-a", text, event_id=event_id)
        assert retry["status"] == "pending"
        engine.mark_memory_saved(user_id, message_hash, [], event_id=event_id)
        result = engine.complete_message(user_id, "chat-a", text, message_hash, event_id=event_id)
        assert result["preference_promoted"] is False
        assert engine.observations_col.count_documents({"user_id": user_id}) == 1
        assert engine.process_message(user_id, "chat-a", text, event_id=event_id)["status"] == "duplicate_skipped"
    finally:
        engine.clear_user_data(user_id)


def test_partial_evidence_commit_retries_without_duplication(monkeypatch):
    load_settings(require_gemini=False)
    engine = PreferenceEngine()
    user_id = f"commit-regression-{uuid.uuid4().hex[:12]}"
    event_id = f"event-{uuid.uuid4().hex[:12]}"
    text = "Explain this in very simple language."
    message_hash = hashlib.sha256(text.encode()).hexdigest()
    try:
        engine.process_message(user_id, "chat-a", text, event_id=event_id)
        engine.mark_memory_saved(user_id, message_hash, [], event_id=event_id)
        original = engine.mark_event_completed
        monkeypatch.setattr(engine, "mark_event_completed", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic failure")))
        with pytest.raises(RuntimeError):
            engine.complete_message(user_id, "chat-a", text, message_hash, event_id=event_id)
        monkeypatch.setattr(engine, "mark_event_completed", original)
        assert engine.process_message(user_id, "chat-a", text, event_id=event_id)["status"] == "memory_saved"
        engine.complete_message(user_id, "chat-a", text, message_hash, event_id=event_id)
        assert engine.observations_col.count_documents({"user_id": user_id}) == 1
    finally:
        engine.clear_user_data(user_id)


def test_mem0_plaintext_history_is_purged_after_forget():
    with tempfile.TemporaryDirectory() as tmp:
        db = SQLiteManager(str(Path(tmp) / "history.db"))
        db.add_history("memory-1", None, "A private fact", "ADD", created_at="2026-09-25T10:00:00Z", is_deleted=0)
        manager = object.__new__(MemoryManager)
        manager.memory = SimpleNamespace(db=db)
        manager._purge_history("memory-1")
        assert db.get_history("memory-1") == []
        db.close()


def test_production_capture_requires_paid_tier_confirmation(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "mock-gemini-key")
    monkeypatch.delenv("GEMINI_PAID_TIER_CONFIRMED", raising=False)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="audit-user")
    app.dependency_overrides[get_pref_engine] = lambda: object()
    app.dependency_overrides[get_mem_manager] = lambda: object()
    try:
        with TestClient(app) as client:
            readiness = client.get("/ready")
            response = client.post("/api/v1/messages/ingest", json={"conversation_id": "c", "text": "I like TypeScript."})
            query = client.post("/api/v1/memories/query", json={"query": "What did I like?"})
        assert readiness.status_code == 503
        assert "GEMINI_PAID_TIER_CONFIRMED" in readiness.json()["missing"]
        assert response.status_code == 503
        assert query.status_code == 503
    finally:
        app.dependency_overrides.clear()
