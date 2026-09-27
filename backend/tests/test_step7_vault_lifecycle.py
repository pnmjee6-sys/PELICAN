"""Step 7: Vault Controls, Corrections, Block, and Forget Lifecycle Verification Suite.

Validates all Test 7 requirements:
1. Correct memory and search new wording (compatible new embedding).
2. Reject a correction containing a secret.
3. Correct and lock preference (user-corrected preference stays locked).
4. Remove evidence demotes unsupported inferred preferences while keeping locked preferences active.
5. Block memory and preference and immediately query (no blocked item leaks into recall).
6. Forget purges Mem0 plaintext history and uses source tombstones (rejects queued retry/restart).
7. Sibling memories from the same message are preserved when one sibling is forgotten.
8. Cross-account security: All controls are strictly tenant-scoped (404 on mismatched user_id).
"""

from __future__ import annotations

import copy
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from starlette.testclient import TestClient

from auth import AuthenticatedUser
from memory_manager import MemoryManager, generate_deterministic_embedding
from preference_engine import PreferenceEngine
from security import redacted_evidence_excerpt
from server import app, get_current_user, get_mem_manager, get_pref_engine


# ---------------------------------------------------------------------------
# In-Memory Collection Fixture for Deterministic, Fast Lifecycle Testing
# ---------------------------------------------------------------------------
class MockMongoCollection:
    def __init__(self, documents=()):
        self.docs: Dict[str, Dict[str, Any]] = {
            str(doc["_id"]): copy.deepcopy(doc) for doc in documents
        }

    @staticmethod
    def matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
        for key, expected in query.items():
            value = doc
            for part in key.split("."):
                value = value.get(part) if isinstance(value, dict) else None

            if isinstance(expected, dict):
                if "$ne" in expected and value == expected["$ne"]:
                    return False
                if "$eq" in expected and value != expected["$eq"]:
                    return False
                if "$gte" in expected and (value is None or str(value) < str(expected["$gte"])):
                    return False
            elif value != expected:
                return False
        return True

    def find_one(self, query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return next((copy.deepcopy(doc) for doc in self.docs.values() if self.matches(doc, query)), None)

    def find(self, query: Dict[str, Any], *_args, **_kwargs) -> List[Dict[str, Any]]:
        return [copy.deepcopy(doc) for doc in self.docs.values() if self.matches(doc, query)]

    def insert_one(self, doc: Dict[str, Any]) -> SimpleNamespace:
        d = copy.deepcopy(doc)
        d.setdefault("_id", f"auto-{len(self.docs) + 1}")
        self.docs[str(d["_id"])] = d
        return SimpleNamespace(inserted_id=d["_id"])

    def update_one(self, query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False) -> SimpleNamespace:
        doc = next((item for item in self.docs.values() if self.matches(item, query)), None)
        if doc is None and upsert:
            doc = {k: v for k, v in query.items() if not isinstance(v, dict)}
            doc.setdefault("_id", f"auto-{len(self.docs) + 1}")
            self.docs[str(doc["_id"])] = doc

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

    def update_many(self, query: Dict[str, Any], update: Dict[str, Any]) -> SimpleNamespace:
        count = 0
        for doc in self.docs.values():
            if self.matches(doc, query):
                for key, value in update.get("$set", {}).items():
                    doc[key] = value
                count += 1
        return SimpleNamespace(modified_count=count)

    def delete_one(self, query: Dict[str, Any]) -> SimpleNamespace:
        doc = self.find_one(query)
        if doc:
            del self.docs[str(doc["_id"])]
        return SimpleNamespace(deleted_count=1 if doc else 0)

    def delete_many(self, query: Dict[str, Any]) -> SimpleNamespace:
        to_delete = [str(doc["_id"]) for doc in self.docs.values() if self.matches(doc, query)]
        for doc_id in to_delete:
            del self.docs[doc_id]
        return SimpleNamespace(deleted_count=len(to_delete))

    def aggregate(self, _pipeline):
        return [copy.deepcopy(doc) for doc in self.docs.values() if "score" in doc]


# ---------------------------------------------------------------------------
# Test SQLite History Store for Mem0 Purge Verification
# ---------------------------------------------------------------------------
class MockMem0HistoryDb:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self._lock = threading.Lock()
        with self.connection:
            self.connection.execute(
                "CREATE TABLE history (id INTEGER PRIMARY KEY, memory_id TEXT, action TEXT, old_value TEXT, new_value TEXT)"
            )

    def seed(self, memory_id: str, action: str, new_value: str):
        with self._lock:
            self.connection.execute(
                "INSERT INTO history (memory_id, action, new_value) VALUES (?, ?, ?)",
                (memory_id, action, new_value),
            )
            self.connection.commit()

    def count(self, memory_id: str) -> int:
        with self._lock:
            cur = self.connection.execute("SELECT COUNT(*) FROM history WHERE memory_id = ?", (memory_id,))
            return cur.fetchone()[0]


class MockMem0Instance:
    def __init__(self, history_db: MockMem0HistoryDb):
        self.db = history_db
        self.deleted_ids = []
        self.updated = {}

    def delete(self, memory_id: str):
        self.deleted_ids.append(memory_id)

    def update(self, memory_id: str, text: str):
        self.updated[memory_id] = text


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def make_test_memory_manager(history_db: Optional[MockMem0HistoryDb] = None) -> MemoryManager:
    manager = object.__new__(MemoryManager)
    manager.settings = SimpleNamespace(
        mongodb_vector_index_name="test-index",
        embedding_model="openai/text-embedding-3-small",
        embedding_dims=1536,
    )
    manager.collection = MockMongoCollection()
    manager.forgotten_sources = MockMongoCollection()
    manager._allow_offline = True
    manager.embed_text = lambda text: generate_deterministic_embedding(text, 1536)

    if history_db:
        manager.memory = MockMem0Instance(history_db)
    else:
        manager.memory = None

    return manager


def make_test_preference_engine() -> PreferenceEngine:
    engine = object.__new__(PreferenceEngine)
    engine.observations_col = MockMongoCollection()
    engine.preferences_col = MockMongoCollection()
    engine.dedup_col = MockMongoCollection()
    engine.forgotten_preferences_col = MockMongoCollection()
    return engine


@contextmanager
def make_test_client(manager: MemoryManager, engine: PreferenceEngine, user_id: str = "alice"):
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id=user_id)
    app.dependency_overrides[get_mem_manager] = lambda: manager
    app.dependency_overrides[get_pref_engine] = lambda: engine
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Step 7 Tests
# ---------------------------------------------------------------------------

def test_correct_memory_and_search_new_wording():
    """1. Corrected memory receives a compatible new embedding and is searchable under the new text."""
    manager = make_test_memory_manager()
    engine = make_test_preference_engine()

    # Initial memory created with "Uses Python"
    initial_emb = manager.embed_text("Uses Python")
    manager.collection.insert_one({
        "_id": "mem-1",
        "text": "Uses Python",
        "embedding": initial_emb,
        "payload": {
            "user_id": "alice",
            "data": "Uses Python",
            "classification": "general",
            "status": "active",
            "embedding_model": "openai/text-embedding-3-small",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    })

    with make_test_client(manager, engine, user_id="alice") as client:
        # Correct memory wording to "Uses Rust"
        res = client.put("/api/v1/memories/mem-1", json={"text": "Uses Rust"})
        assert res.status_code == 200
        data = res.json()
        assert data["text"] == "Uses Rust"
        assert data["classification"] == "general"

        # Verify collection has updated embedding matching "Uses Rust"
        updated_doc = manager.collection.find_one({"_id": "mem-1"})
        assert updated_doc["payload"]["data"] == "Uses Rust"
        assert updated_doc["text"] == "Uses Rust"
        rust_emb = manager.embed_text("Uses Rust")
        assert updated_doc["embedding"] == rust_emb
        assert updated_doc["payload"]["embedding_model"] == "openai/text-embedding-3-small"

        # Search with "Rust" query -> memory is recalled
        query_res = client.post("/api/v1/memories/query", json={"query": "Rust"})
        assert query_res.status_code == 200
        found = [m["text"] for m in query_res.json()["general_memories"]]
        assert "Uses Rust" in found


def test_reject_correction_containing_secret():
    """2. Updating memory or preference with credentials/secrets is rejected (HTTP 400) without saving."""
    manager = make_test_memory_manager()
    engine = make_test_preference_engine()

    manager.collection.insert_one({
        "_id": "mem-1",
        "text": "Uses Python",
        "payload": {"user_id": "alice", "data": "Uses Python", "status": "active", "classification": "general"},
    })
    engine.preferences_col.insert_one({
        "_id": "pref-1",
        "user_id": "alice",
        "preference_key": "concise",
        "preference_text": "Prefers concise answers",
        "status": "active",
        "locked": False,
    })

    with make_test_client(manager, engine, user_id="alice") as client:
        # Secret in memory correction
        res_mem_pw = client.put("/api/v1/memories/mem-1", json={"text": "my password: SuperSecret123!"})
        assert res_mem_pw.status_code == 400
        assert "secret" in res_mem_pw.json()["detail"].lower()

        res_mem_key = client.put("/api/v1/memories/mem-1", json={"text": "api key is sk-proj-12345678901234567890"})
        assert res_mem_key.status_code == 400

        # Memory was NOT modified
        assert manager.collection.find_one({"_id": "mem-1"})["payload"]["data"] == "Uses Python"

        # Secret in preference correction
        res_pref_sec = client.put("/api/v1/preferences/pref-1", json={"preference_text": "bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.do_not_use_this"})
        assert res_pref_sec.status_code == 400
        assert "credential" in res_pref_sec.json()["detail"].lower() or "wording" in res_pref_sec.json()["detail"].lower()

        # Preference was NOT modified
        assert engine.preferences_col.find_one({"_id": "pref-1"})["preference_text"] == "Prefers concise answers"


def test_correct_and_lock_preference():
    """3. User correction locks preference wording; subsequent observation processing never overwrites it."""
    manager = make_test_memory_manager()
    engine = make_test_preference_engine()

    engine.preferences_col.insert_one({
        "_id": "pref-1",
        "user_id": "alice",
        "preference_key": "concise",
        "preference_text": "Prefers concise answers",
        "evidence_count": 3,
        "conversation_count": 2,
        "status": "active",
        "locked": False,
    })

    with make_test_client(manager, engine, user_id="alice") as client:
        # User explicitly updates preference text
        res = client.put("/api/v1/preferences/pref-1", json={"preference_text": "Give me short step-by-step points only"})
        assert res.status_code == 200
        data = res.json()
        assert data["preference_text"] == "Give me short step-by-step points only"
        assert data["locked"] is True

        # Verify DB doc has locked: True
        pref_doc = engine.preferences_col.find_one({"_id": "pref-1"})
        assert pref_doc["locked"] is True
        assert pref_doc["preference_text"] == "Give me short step-by-step points only"

        # Now simulate new automated evidence evaluation that suggests the default wording
        engine._evaluate_and_promote("alice", "concise", "Prefers concise answers")

        # Preference text MUST remain the user's locked wording
        pref_doc_after = engine.preferences_col.find_one({"_id": "pref-1"})
        assert pref_doc_after["preference_text"] == "Give me short step-by-step points only"
        assert pref_doc_after["locked"] is True


def test_remove_evidence_demotes_unsupported_inferred_preferences():
    """4. Removing evidence drops observation count and demotes inferred preferences to insufficient_evidence, while locked preferences stay active."""
    manager = make_test_memory_manager()
    engine = make_test_preference_engine()

    # Seed 3 observations across 2 conversations
    for i in range(3):
        engine.observations_col.insert_one({
            "_id": f"obs-{i}",
            "user_id": "alice",
            "preference_key": "concise",
            "message_hash": f"hash-{i}",
            "conversation_id": f"chat-{i % 2}",
            "raw_evidence": f"be concise {i}",
            "status": "active",
        })

    # Inferred preference (locked: False)
    engine.preferences_col.insert_one({
        "_id": "pref-inferred",
        "user_id": "alice",
        "preference_key": "concise",
        "preference_text": "Prefers concise answers",
        "evidence_count": 3,
        "conversation_count": 2,
        "status": "active",
        "locked": False,
    })

    with make_test_client(manager, engine, user_id="alice") as client:
        # Remove one observation
        del_res = client.delete("/api/v1/preferences/pref-inferred/observations/obs-0")
        assert del_res.status_code == 200
        assert del_res.json()["ok"] is True

        # Evidence excerpt is wiped and status is removed
        obs_doc = engine.observations_col.find_one({"_id": "obs-0"})
        assert obs_doc["status"] == "removed"
        assert obs_doc["raw_evidence"] == ""

        # Inferred preference must be DEMOTED to insufficient_evidence
        pref = engine.preferences_col.find_one({"_id": "pref-inferred"})
        assert pref["status"] == "insufficient_evidence"
        assert pref["evidence_count"] == 2

        # Demoted preference is NOT returned in active query recall
        query_res = client.post("/api/v1/memories/query", json={"query": "concise style"})
        assert query_res.status_code == 200
        pref_ids = [p["id"] for p in query_res.json()["preferences"]]
        assert "pref-inferred" not in pref_ids

        # Now test that a locked preference stays active even if evidence is removed
        engine.preferences_col.update_one(
            {"_id": "pref-inferred"},
            {"$set": {"status": "active", "locked": True}}
        )
        del_res2 = client.delete("/api/v1/preferences/pref-inferred/observations/obs-1")
        assert del_res2.status_code == 200
        pref_locked = engine.preferences_col.find_one({"_id": "pref-inferred"})
        assert pref_locked["locked"] is True
        assert pref_locked["status"] == "active"


def test_block_and_immediately_query():
    """5. Blocking memory or preference immediately excludes it from recall query."""
    manager = make_test_memory_manager()
    engine = make_test_preference_engine()

    # Active memory
    manager.collection.insert_one({
        "_id": "mem-1",
        "text": "Uses Rust",
        "embedding": manager.embed_text("Uses Rust"),
        "payload": {
            "user_id": "alice",
            "data": "Uses Rust",
            "classification": "general",
            "status": "active",
            "embedding_model": "openai/text-embedding-3-small",
        },
    })

    # Active preference
    engine.preferences_col.insert_one({
        "_id": "pref-1",
        "user_id": "alice",
        "preference_key": "concise",
        "preference_text": "Prefers concise answers",
        "status": "active",
        "locked": False,
        "evidence_count": 3,
        "conversation_count": 2,
    })

    with make_test_client(manager, engine, user_id="alice") as client:
        # Pre-check: memory and preference are recallable
        pre_res = client.post("/api/v1/memories/query", json={"query": "Rust"})
        assert any(m["id"] == "mem-1" for m in pre_res.json()["general_memories"])
        assert any(p["id"] == "pref-1" for p in pre_res.json()["preferences"])

        # Block memory
        block_mem_res = client.post("/api/v1/memories/mem-1/block")
        assert block_mem_res.status_code == 200
        assert block_mem_res.json()["ok"] is True

        # Block preference
        block_pref_res = client.post("/api/v1/preferences/pref-1/block")
        assert block_pref_res.status_code == 200
        assert block_pref_res.json()["ok"] is True

        # Immediately query again: neither should appear in recall
        post_res = client.post("/api/v1/memories/query", json={"query": "Rust"})
        assert not any(m["id"] == "mem-1" for m in post_res.json()["general_memories"])
        assert not any(m["id"] == "mem-1" for m in post_res.json()["sensitive_memories"])
        assert not any(p["id"] == "pref-1" for p in post_res.json()["preferences"])


def test_forget_purges_mem0_plaintext_history_and_uses_source_tombstones():
    """6. Deleting/forgetting a memory purges Mem0 plaintext history and tombstones source event."""
    history_db = MockMem0HistoryDb()
    # Seed history for mem-1 and another memory mem-other
    history_db.seed("mem-1", "ADD", "Uses Python")
    history_db.seed("mem-1", "UPDATE", "Uses Rust")
    history_db.seed("mem-other", "ADD", "Uses Go")
    assert history_db.count("mem-1") == 2
    assert history_db.count("mem-other") == 1

    manager = make_test_memory_manager(history_db=history_db)
    engine = make_test_preference_engine()

    manager.collection.insert_one({
        "_id": "mem-1",
        "text": "Uses Rust",
        "payload": {
            "user_id": "alice",
            "data": "Uses Rust",
            "status": "active",
            "classification": "general",
            "source_event_key": "alice:event-123",
        },
    })

    with make_test_client(manager, engine, user_id="alice") as client:
        # Delete / forget memory
        del_res = client.delete("/api/v1/memories/mem-1")
        assert del_res.status_code == 200
        assert del_res.json()["ok"] is True

        # Plaintext history in Mem0 SQLite database is completely purged
        assert history_db.count("mem-1") == 0
        # Other memories' history is preserved
        assert history_db.count("mem-other") == 1

        # Collection document is deleted
        assert manager.collection.find_one({"_id": "mem-1"}) is None

        # Source event is tombstoned in forgotten_memory_sources
        tombstone = manager.forgotten_sources.find_one({"_id": "alice:event-123", "user_id": "alice"})
        assert tombstone is not None

        # Refresh: GET /api/v1/memories returns empty
        get_res = client.get("/api/v1/memories")
        assert get_res.status_code == 200
        assert not any(m["id"] == "mem-1" for m in get_res.json())

        # Retry: An ingest retry with source_event_key="alice:event-123" is rejected as forgotten_source
        add_result = manager.add("Uses Rust", "alice", metadata={"source_event_key": "alice:event-123"})
        assert add_result["status"] == "skipped"
        assert add_result["reason"] == "forgotten_source"
        assert manager.collection.find_one({"_id": "mem-1"}) is None


def test_verify_sibling_memories_preserved_on_forget():
    """7. Forgetting one memory from a multi-fact event preserves its sibling memories."""
    manager = make_test_memory_manager()
    engine = make_test_preference_engine()

    source_key = "alice:multi-event-1"
    manager.collection.insert_one({
        "_id": "mem-a",
        "text": "Prefers TypeScript",
        "payload": {
            "user_id": "alice",
            "data": "Prefers TypeScript",
            "status": "active",
            "classification": "general",
            "source_event_key": source_key,
        },
    })
    manager.collection.insert_one({
        "_id": "mem-b",
        "text": "Works in finance",
        "payload": {
            "user_id": "alice",
            "data": "Works in finance",
            "status": "active",
            "classification": "sensitive",
            "source_event_key": source_key,
        },
    })

    with make_test_client(manager, engine, user_id="alice") as client:
        # Delete mem-a only
        del_res = client.delete("/api/v1/memories/mem-a")
        assert del_res.status_code == 200

        # mem-a is deleted
        assert manager.collection.find_one({"_id": "mem-a"}) is None

        # Sibling mem-b is PRESERVED
        mem_b = manager.collection.find_one({"_id": "mem-b"})
        assert mem_b is not None
        assert mem_b["payload"]["data"] == "Works in finance"

        # Tombstone reflects allowed_memory_ids containing mem-b
        tombstone = manager.forgotten_sources.find_one({"_id": source_key})
        assert tombstone is not None
        assert "mem-b" in tombstone.get("allowed_memory_ids", [])
        assert "mem-a" not in tombstone.get("allowed_memory_ids", [])

        # Running _delete_source_memories retains mem-b
        manager._delete_source_memories("alice", source_key)
        assert manager.collection.find_one({"_id": "mem-b"}) is not None


def test_attempt_each_action_with_another_account_id():
    """8. Cross-account security: All memory and preference endpoints fail closed (404) for mismatched user."""
    manager = make_test_memory_manager()
    engine = make_test_preference_engine()

    # Alice's data
    manager.collection.insert_one({
        "_id": "alice-mem",
        "text": "Alice secret recipe",
        "payload": {
            "user_id": "alice",
            "data": "Alice secret recipe",
            "status": "active",
            "classification": "general",
        },
    })
    engine.preferences_col.insert_one({
        "_id": "alice-pref",
        "user_id": "alice",
        "preference_key": "concise",
        "preference_text": "Alice likes brevity",
        "status": "active",
        "locked": False,
        "evidence_count": 3,
        "conversation_count": 2,
    })
    engine.observations_col.insert_one({
        "_id": "alice-obs",
        "user_id": "alice",
        "preference_key": "concise",
        "message_hash": "hash-alice",
        "conversation_id": "chat-alice",
        "raw_evidence": "be brief",
        "status": "active",
    })

    # Bob attempts all actions against Alice's resources
    with make_test_client(manager, engine, user_id="bob") as bob_client:
        # Memory endpoints
        assert bob_client.get("/api/v1/memories").json() == []
        assert bob_client.put("/api/v1/memories/alice-mem", json={"text": "Hijacked"}).status_code == 404
        assert bob_client.post("/api/v1/memories/alice-mem/block").status_code == 404
        assert bob_client.delete("/api/v1/memories/alice-mem").status_code == 404

        # Preference endpoints
        assert bob_client.get("/api/v1/preferences").json() == []
        assert bob_client.put("/api/v1/preferences/alice-pref", json={"preference_text": "Bob text"}).status_code == 404
        assert bob_client.post("/api/v1/preferences/alice-pref/block").status_code == 404
        assert bob_client.delete("/api/v1/preferences/alice-pref/observations/alice-obs").status_code == 404
        assert bob_client.delete("/api/v1/preferences/alice-pref").status_code == 404

    # Verify Alice's resources remain completely unchanged
    alice_mem = manager.collection.find_one({"_id": "alice-mem"})
    assert alice_mem["payload"]["data"] == "Alice secret recipe"
    assert alice_mem["payload"]["status"] == "active"

    alice_pref = engine.preferences_col.find_one({"_id": "alice-pref"})
    assert alice_pref["preference_text"] == "Alice likes brevity"
    assert alice_pref["status"] == "active"

    alice_obs = engine.observations_col.find_one({"_id": "alice-obs"})
    assert alice_obs["status"] == "active"
