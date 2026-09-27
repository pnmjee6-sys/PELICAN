"""V3 lifecycle checks without external model or database calls."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from contextlib import contextmanager

from starlette.testclient import TestClient
from auth import AuthenticatedUser
from server import app, get_current_user, get_mem_manager, get_pref_engine

from memory_manager import MemoryManager
from preference_engine import PreferenceEngine


class Collection:
    def __init__(self, documents=()):
        self.docs = {doc["_id"]: deepcopy(doc) for doc in documents}

    @staticmethod
    def matches(doc, query):
        for key, expected in query.items():
            value = doc
            for part in key.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            if isinstance(expected, dict):
                if "$ne" in expected and value == expected["$ne"]:
                    return False
            elif value != expected:
                return False
        return True

    def find_one(self, query):
        return next((deepcopy(doc) for doc in self.docs.values() if self.matches(doc, query)), None)

    def find(self, query, *_args):
        return [deepcopy(doc) for doc in self.docs.values() if self.matches(doc, query)]

    def aggregate(self, _pipeline):
        return [deepcopy(doc) for doc in self.docs.values() if "score" in doc]

    def update_one(self, query, update, upsert=False):
        doc = next((item for item in self.docs.values() if self.matches(item, query)), None)
        if doc is None and upsert:
            doc = {k: v for k, v in query.items() if not isinstance(v, dict)}
            doc.setdefault("_id", f"auto-{len(self.docs)}")
            self.docs[doc["_id"]] = doc
        if doc is None:
            return SimpleNamespace(matched_count=0, modified_count=0)
        for operation in ("$setOnInsert", "$set"):
            if operation == "$setOnInsert" and not upsert:
                continue
            for key, value in update.get(operation, {}).items():
                target = doc
                parts = key.split(".")
                for part in parts[:-1]:
                    target = target.setdefault(part, {})
                target[parts[-1]] = value
        for key, value in update.get("$pull", {}).items():
            doc[key] = [item for item in doc.get(key, []) if item != value]
        return SimpleNamespace(matched_count=1, modified_count=1)

    def update_many(self, query, update):
        count = 0
        for doc in self.docs.values():
            if self.matches(doc, query):
                for key, value in update.get("$set", {}).items():
                    doc[key] = value
                count += 1
        return SimpleNamespace(modified_count=count)

    def delete_one(self, query):
        doc = self.find_one(query)
        if doc:
            del self.docs[doc["_id"]]
        return SimpleNamespace(deleted_count=bool(doc))


def memory_manager():
    manager = object.__new__(MemoryManager)
    manager.collection = Collection([{
        "_id": "memory-1", "payload": {"user_id": "alice", "status": "active",
        "classification": "general", "data": "Uses Python", "source_event_key": "alice:event-1",
        "source_site": "chatgpt.com"},
    }])
    manager.forgotten_sources = Collection()
    manager.memory = None
    manager._allow_offline = True
    manager.settings = SimpleNamespace(gemini_api_key=None)
    return manager


def test_forget_tombstones_source_and_rejects_queued_retry():
    manager = memory_manager()
    assert manager.delete("memory-1", "alice") is True
    assert manager.collection.find_one({"_id": "memory-1"}) is None
    assert manager.forgotten_sources.find_one({"_id": "alice:event-1", "user_id": "alice"})
    assert manager.add("Uses Python", "alice", {"source_event_key": "alice:event-1"})["reason"] == "forgotten_source"


def test_forget_preserves_existing_sibling_from_same_message():
    manager = memory_manager()
    manager.collection.docs["memory-2"] = {
        "_id": "memory-2", "payload": {"user_id": "alice", "status": "active", "classification": "general",
        "data": "Uses Rust", "source_event_key": "alice:event-1"},
    }
    manager.delete("memory-1", "alice")
    assert manager.forgotten_sources.find_one({"_id": "alice:event-1"})["allowed_memory_ids"] == ["memory-2"]
    manager._delete_source_memories("alice", "alice:event-1")
    assert manager.collection.find_one({"_id": "memory-2"}) is not None


def test_inflight_provider_write_is_removed_after_forget():
    manager = memory_manager()
    manager.collection.docs.clear()

    class Provider:
        def add(self, text, user_id, metadata):
            manager.collection.docs["late-memory"] = {
                "_id": "late-memory", "payload": {"user_id": user_id, "data": text, **metadata},
            }
            manager.forgotten_sources.docs["alice:event-1"] = {
                "_id": "alice:event-1", "user_id": "alice", "allowed_memory_ids": [],
            }
            return {"results": [{"id": "late-memory", "memory": text}]}

        def delete(self, memory_id):
            manager.collection.docs.pop(memory_id, None)

    manager.memory = Provider()
    manager.settings.gemini_api_key = "synthetic"
    manager._purge_history = lambda _memory_id: None
    result = manager.add("Uses Python", "alice", {"source_event_key": "alice:event-1"})
    assert result["reason"] == "forgotten_source"
    assert manager.collection.find_one({"_id": "late-memory"}) is None


def test_memory_controls_are_tenant_scoped_and_block_excludes_recall():
    manager = memory_manager()
    try:
        manager.block("memory-1", "bob")
        assert False, "cross-tenant block should fail"
    except PermissionError:
        pass
    assert manager.block("memory-1", "alice") is True
    assert manager.collection.find_one({"_id": "memory-1"})["payload"]["status"] == "blocked"
    manager.collection.docs["memory-1"]["score"] = 0.99
    manager.embed_text = lambda _text: [1.0, 0.0]
    manager.settings = SimpleNamespace(mongodb_vector_index_name="test-index")
    assert manager.search("Python", "alice")["results"] == []
    assert manager.get_all("bob") == []
    assert manager.get_all("alice")[0]["source"] == "chatgpt.com"


def test_recent_fact_is_recalled_while_atlas_index_is_catching_up():
    class PartialIndex:
        def aggregate(self, _pipeline):
            return [{"_id": "old", "score": 0.81, "payload": {
                "user_id": "alice", "status": "active", "classification": "general", "data": "Uses Python",
            }}]

        def find(self, query):
            assert query["payload.user_id"] == "alice"
            assert "$gte" in query["payload.created_at"]
            return [{"_id": "fresh", "embedding": [1.0, 0.0], "payload": {
                "user_id": "alice", "status": "active", "classification": "general", "data": "Builds audio plugins",
            }}]

    manager = object.__new__(MemoryManager)
    manager.collection = PartialIndex()
    manager.settings = SimpleNamespace(mongodb_vector_index_name="test-index")
    manager.embed_text = lambda _query: [1.0, 0.0]
    texts = [item["text"] for item in manager.search("audio plugins", "alice")["general_memories"]]
    assert "Builds audio plugins" in texts


def preference_engine():
    engine = object.__new__(PreferenceEngine)
    engine.observations_col = Collection([
        {"_id": f"obs-{i}", "user_id": "alice", "preference_key": "concise", "status": "active",
         "message_hash": f"hash-{i}", "conversation_id": f"chat-{i % 2}", "raw_evidence": "be concise",
         "default_text": "Prefers concise answers"} for i in range(3)
    ])
    engine.preferences_col = Collection([{
        "_id": "pref-1", "user_id": "alice", "preference_key": "concise", "preference_text": "Prefers concise answers",
        "status": "active", "locked": False, "evidence_count": 3,
    }])
    engine.forgotten_preferences_col = Collection()
    return engine


def test_evidence_removal_demotes_inference_and_erases_excerpt():
    engine = preference_engine()
    assert engine.remove_observation("alice", "obs-0") is True
    assert engine.observations_col.find_one({"_id": "obs-0"})["raw_evidence"] == ""
    pref = engine.preferences_col.find_one({"_id": "pref-1"})
    assert pref["status"] == "insufficient_evidence"
    assert pref["evidence_count"] == 2
    assert engine.get_user_preferences("alice", status_filter="active") == []


def test_user_correction_stays_locked_when_evidence_changes():
    engine = preference_engine()
    engine.update_preference_text("alice", "pref-1", "Use my exact phrasing")
    engine.remove_observation("alice", "obs-0")
    pref = engine.preferences_col.find_one({"_id": "pref-1"})
    assert pref["preference_text"] == "Use my exact phrasing"
    assert pref["locked"] is True
    assert pref["status"] == "active"


def test_explicit_correction_can_restore_demoted_preference():
    engine = preference_engine()
    engine.remove_observation("alice", "obs-0")
    assert engine.preferences_col.find_one({"_id": "pref-1"})["status"] == "insufficient_evidence"
    engine.update_preference_text("alice", "pref-1", "Keep answers short")
    assert engine.preferences_col.find_one({"_id": "pref-1"})["status"] == "active"


def test_block_and_forget_preference_remove_it_from_recall():
    engine = preference_engine()
    assert engine.block_preference("alice", "pref-1") is True
    engine._evaluate_and_promote("alice", "concise", "Prefers concise answers")
    assert engine.preferences_col.find_one({"_id": "pref-1"})["status"] == "blocked"
    assert engine.forget_preference("alice", "pref-1") is True
    assert engine.preferences_col.find_one({"_id": "pref-1"}) is None
    assert all(obs["status"] == "removed" and obs["raw_evidence"] == "" for obs in engine.observations_col.docs.values())


@contextmanager
def api_client(manager, engine, user_id="alice"):
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id=user_id)
    app.dependency_overrides[get_mem_manager] = lambda: manager
    app.dependency_overrides[get_pref_engine] = lambda: engine
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def test_v3_routes_correct_memory_and_preference_with_tenant_guard():
    manager = memory_manager()
    manager.embed_text = lambda _text: [1.0, 0.0]
    engine = preference_engine()
    with api_client(manager, engine) as client:
        corrected = client.put("/api/v1/memories/memory-1", json={"text": "Uses Rust"})
        assert corrected.status_code == 200
        assert manager.collection.find_one({"_id": "memory-1"})["payload"]["data"] == "Uses Rust"
        assert client.put("/api/v1/memories/memory-1", json={"text": "password: SuperSecret123"}).status_code == 400
        pref = client.put("/api/v1/preferences/pref-1", json={"preference_text": "Give me short answers"})
        assert pref.status_code == 200
        assert engine.preferences_col.find_one({"_id": "pref-1"})["locked"] is True
        removed = client.delete("/api/v1/preferences/pref-1/observations/obs-0")
        assert removed.status_code == 200 and removed.json()["ok"] is True
    with api_client(manager, engine, user_id="bob") as client:
        assert client.put("/api/v1/memories/memory-1", json={"text": "stolen"}).status_code == 404
        assert client.delete("/api/v1/preferences/pref-1").status_code == 404
