"""Step 4 Verification Suite — Backend Ingest, Privacy, and Preference Learning.

Tests:
1. User-only ingest; assistant turns skipped immediately.
2. Old / extension-injected memory blocks skipped before saving or learning.
3. Secret credential screening (defense-in-depth across input, extracted facts, and search).
4. Idempotent duplicate events (same message or event_id deduplicated without inflation).
5. Safe retry state machine:
   - Failed write marks event 'failed' and allows retry.
   - Successful write then failed learning reuses 'memory_saved' without duplicating writes.
6. Classification fail-closed:
   - Missing/invalid classification, vector space mismatch, or mismatched ownership excluded.
   - Allergies and uncertainty forced to sensitive (requiring user approval).
7. Two-account isolation:
   - Complete partition between users across memories, search, and preferences.
8. Narrow learning promotion gate:
   - 1 observation: not promoted.
   - 3 observations in the SAME chat: not promoted (requires >= 2 chats).
   - 3 distinct observations across >= 2 chats: promoted!
   - User correction locks wording and takes precedence over automatic learning.
9. Provider layer integration:
   - FactExtractor and ValidatorPass integrated with MemoryManager and authenticated routes.
"""

from __future__ import annotations

import copy
import hashlib
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

os.environ["APP_ENV"] = "test"
os.environ["ALLOW_TEST_AUTH"] = "true"
os.environ["ALLOW_OFFLINE_EMBEDDINGS"] = "true"

import pytest
from starlette.testclient import TestClient

from auth import AuthenticatedUser
from memory_manager import MemoryManager
from preference_engine import PreferenceEngine
from providers import (
    ExtractionResult,
    FactExtractor,
    MockEmbedder,
    MockExtractor,
    MockValidator,
    ProviderOutageError,
    ValidationResult,
    create_extractor,
    create_embedder,
    create_validator,
)
from security import classify_text, contains_secret
from server import app, get_current_user, get_mem_manager, get_pref_engine


# ---------------------------------------------------------------------------
# In-Memory Collection Fixture for Deterministic, Fast Lifecycle Testing
# ---------------------------------------------------------------------------
class MockMongoCollection:
    def __init__(self, documents=()):
        self.docs: Dict[str, Dict[str, Any]] = {
            doc["_id"]: copy.deepcopy(doc) for doc in documents
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
        self.docs[d["_id"]] = d
        return SimpleNamespace(inserted_id=d["_id"])

    def update_one(self, query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False) -> SimpleNamespace:
        doc = next((item for item in self.docs.values() if self.matches(item, query)), None)
        if doc is None and upsert:
            doc = {k: v for k, v in query.items() if not isinstance(v, dict)}
            doc.setdefault("_id", f"auto-{len(self.docs) + 1}")
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
            del self.docs[doc["_id"]]
        return SimpleNamespace(deleted_count=1 if doc else 0)

    def delete_many(self, query: Dict[str, Any]) -> SimpleNamespace:
        to_delete = [doc["_id"] for doc in self.docs.values() if self.matches(doc, query)]
        for doc_id in to_delete:
            del self.docs[doc_id]
        return SimpleNamespace(deleted_count=len(to_delete))

    def aggregate(self, _pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [copy.deepcopy(doc) for doc in self.docs.values() if "score" in doc]

    def create_index(self, *_args, **_kwargs):
        pass


def build_test_environment(user_id: str = "alice"):
    """Creates isolated MemoryManager and PreferenceEngine with mock collections."""
    mem_mgr = object.__new__(MemoryManager)
    mem_mgr.collection = MockMongoCollection()
    mem_mgr.forgotten_sources = MockMongoCollection()
    mem_mgr.memory = None
    mem_mgr._allow_offline = True
    mem_mgr.settings = SimpleNamespace(
        mongodb_vector_index_name="test_index",
        embedding_model="openai/text-embedding-3-small",
        embedding_dims=1536,
        gemini_api_key=None,
    )
    mem_mgr.embedder = MockEmbedder(dimensions=1536)
    mem_mgr.extractor = None
    mem_mgr.validator = MockValidator(enabled=False)

    pref_engine = object.__new__(PreferenceEngine)
    pref_engine.observations_col = MockMongoCollection()
    pref_engine.preferences_col = MockMongoCollection()
    pref_engine.dedup_col = MockMongoCollection()
    pref_engine.forgotten_preferences_col = MockMongoCollection()

    return mem_mgr, pref_engine


@contextmanager
def client_fixture(mem_mgr: MemoryManager, pref_engine: PreferenceEngine, user_id: str = "alice"):
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id=user_id)
    app.dependency_overrides[get_mem_manager] = lambda: mem_mgr
    app.dependency_overrides[get_pref_engine] = lambda: pref_engine
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# TEST 1: User-Only Ingest; Assistant Replies Skipped
# ---------------------------------------------------------------------------
def test_user_only_ingest_and_assistant_skipped():
    mem_mgr, pref_engine = build_test_environment("alice")
    with client_fixture(mem_mgr, pref_engine, "alice") as client:
        # Assistant reply is skipped immediately
        resp_asst = client.post(
            "/api/v1/messages/ingest",
            json={
                "conversation_id": "c1",
                "text": "Certainly! Here is how you can use Python.",
                "role": "assistant",
            },
        )
        assert resp_asst.status_code == 200
        data_asst = resp_asst.json()
        assert data_asst["status"] == "skipped"
        assert data_asst["reason"] == "assistant_reply_ignored"
        assert len(mem_mgr.collection.docs) == 0
        assert len(pref_engine.observations_col.docs) == 0

        # User message is processed
        resp_user = client.post(
            "/api/v1/messages/ingest",
            json={
                "conversation_id": "c1",
                "text": "I work with FastAPI and PostgreSQL.",
                "role": "user",
            },
        )
        assert resp_user.status_code == 200
        assert resp_user.json()["status"] == "processed"
        assert len(mem_mgr.collection.docs) == 1


# ---------------------------------------------------------------------------
# TEST 2: Old / Injected Context Skipped Before Storage or Learning
# ---------------------------------------------------------------------------
def test_old_and_injected_context_skipped():
    mem_mgr, pref_engine = build_test_environment("alice")
    with client_fixture(mem_mgr, pref_engine, "alice") as client:
        # Explicit flag
        resp_flag = client.post(
            "/api/v1/messages/ingest",
            json={
                "conversation_id": "c1",
                "text": "Likes dark mode [Context Passport Memory]",
                "role": "user",
                "is_extension_context": True,
            },
        )
        assert resp_flag.status_code == 200
        assert resp_flag.json()["status"] == "skipped"
        assert resp_flag.json()["reason"] == "extension_context_ignored"

        # Injected markers in text
        resp_marker = client.post(
            "/api/v1/messages/ingest",
            json={
                "conversation_id": "c1",
                "text": "<!-- context-passport memory: Prefers TypeScript --> Help me with syntax.",
                "role": "user",
                "is_extension_context": False,
            },
        )
        assert resp_marker.status_code == 200
        assert resp_marker.json()["status"] == "skipped"
        assert resp_marker.json()["reason"] == "extension_context_ignored"

        # Neither memories nor observations created
        assert len(mem_mgr.collection.docs) == 0
        assert len(pref_engine.observations_col.docs) == 0


# ---------------------------------------------------------------------------
# TEST 3: Secrets Skipped Defense-in-Depth
# ---------------------------------------------------------------------------
def test_secrets_skipped_defense_in_depth():
    mem_mgr, pref_engine = build_test_environment("alice")
    with client_fixture(mem_mgr, pref_engine, "alice") as client:
        # OpenAI API Key
        resp_key = client.post(
            "/api/v1/messages/ingest",
            json={
                "conversation_id": "c1",
                "text": "My key is sk-abcdef1234567890abcdef1234567890 for testing.",
                "role": "user",
            },
        )
        assert resp_key.status_code == 200
        assert resp_key.json()["status"] == "skipped"
        assert resp_key.json()["reason"] == "secret_credential_screened"

        # Bearer token
        resp_bearer = client.post(
            "/api/v1/messages/ingest",
            json={
                "conversation_id": "c1",
                "text": "Authorization: Bearer mysecrettoken1234567890abcdef",
                "role": "user",
            },
        )
        assert resp_bearer.json()["reason"] == "secret_credential_screened"

        # Zero memories stored
        assert len(mem_mgr.collection.docs) == 0


# ---------------------------------------------------------------------------
# TEST 4: Idempotent Duplicate Events
# ---------------------------------------------------------------------------
def test_idempotent_duplicate_events():
    mem_mgr, pref_engine = build_test_environment("alice")
    with client_fixture(mem_mgr, pref_engine, "alice") as client:
        payload = {
            "conversation_id": "c1",
            "text": "I am a backend engineer building in Go.",
            "role": "user",
            "event_id": "evt-idempotent-01",
        }

        # First request succeeds
        resp1 = client.post("/api/v1/messages/ingest", json=payload)
        assert resp1.status_code == 200
        assert resp1.json()["status"] == "processed"
        assert len(mem_mgr.collection.docs) == 1

        # Second request with identical payload / event_id is skipped as duplicate
        resp2 = client.post("/api/v1/messages/ingest", json=payload)
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "duplicate_skipped"
        assert resp2.json()["event_id"] == "evt-idempotent-01"

        # Collection count remains exactly 1
        assert len(mem_mgr.collection.docs) == 1


# ---------------------------------------------------------------------------
# TEST 5: Failed Write Then Retry State Machine
# ---------------------------------------------------------------------------
def test_failed_write_then_retry():
    mem_mgr, pref_engine = build_test_environment("alice")

    # 1. Simulate write failure on first attempt
    failing_extractor = MockExtractor(fail_mode="outage")
    mem_mgr.extractor = failing_extractor

    with client_fixture(mem_mgr, pref_engine, "alice") as client:
        payload = {
            "conversation_id": "c-retry",
            "text": "I am building a web crawler in Python.",
            "role": "user",
            "event_id": "evt-retry-01",
        }

        # First attempt fails with 500
        resp_fail = client.post("/api/v1/messages/ingest", json=payload)
        assert resp_fail.status_code == 500

        # Event in dedup table must be marked 'failed'
        message_hash = hashlib.sha256(payload["text"].encode("utf-8")).hexdigest()
        event_key = f"alice:{payload['event_id']}"
        event_doc = pref_engine.dedup_col.find_one({"_id": event_key})
        assert event_doc is not None
        assert event_doc["status"] == "failed"

        # 2. Fix extractor (provider recovers) and retry same event
        mem_mgr.extractor = MockExtractor(default_facts=["Builds web crawlers in Python"])
        resp_success = client.post("/api/v1/messages/ingest", json=payload)
        assert resp_success.status_code == 200
        assert resp_success.json()["status"] == "processed"

        # Dedup table is now completed
        event_completed = pref_engine.dedup_col.find_one({"_id": event_key})
        assert event_completed["status"] == "completed"
        assert len(mem_mgr.collection.docs) == 1


def test_memory_saved_then_complete_failed_retry_reuses_saved():
    mem_mgr, pref_engine = build_test_environment("alice")
    mem_mgr.extractor = MockExtractor(default_facts=["User writes Go"])

    with client_fixture(mem_mgr, pref_engine, "alice") as client:
        payload = {
            "conversation_id": "c-reuse",
            "text": "I write Go microservices.",
            "role": "user",
            "event_id": "evt-reuse-01",
        }

        # First attempt: let memory save succeed, but simulate crash before complete_message
        orig_complete = pref_engine.complete_message
        pref_engine.complete_message = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Simulated DB blip"))

        resp_first = client.post("/api/v1/messages/ingest", json=payload)
        assert resp_first.status_code == 500

        event_key = f"alice:{payload['event_id']}"
        event_doc = pref_engine.dedup_col.find_one({"_id": event_key})
        assert event_doc["status"] == "memory_saved"
        assert len(mem_mgr.collection.docs) == 1

        # Track call count on extractor
        initial_call_count = mem_mgr.extractor.call_count

        # Restore complete_message and retry
        pref_engine.complete_message = orig_complete
        resp_retry = client.post("/api/v1/messages/ingest", json=payload)
        assert resp_retry.status_code == 200
        assert resp_retry.json()["status"] == "processed"

        # Extractor was NOT called a second time!
        assert mem_mgr.extractor.call_count == initial_call_count
        # Memory docs not duplicated
        assert len(mem_mgr.collection.docs) == 1


# ---------------------------------------------------------------------------
# TEST 6: Classification Fail-Closed
# ---------------------------------------------------------------------------
def test_classification_fail_closed():
    mem_mgr, pref_engine = build_test_environment("alice")

    # Document 1: Missing classification -> must be excluded
    mem_mgr.collection.docs["m-unlabelled"] = {
        "_id": "m-unlabelled",
        "score": 0.95,
        "payload": {"user_id": "alice", "status": "active", "data": "Uses Mac"},
    }

    # Document 2: Invalid classification -> must be excluded
    mem_mgr.collection.docs["m-invalid"] = {
        "_id": "m-invalid",
        "score": 0.95,
        "payload": {"user_id": "alice", "status": "active", "classification": "invalid_label", "data": "Uses Linux"},
    }

    # Document 3: Blocked status -> must be excluded
    mem_mgr.collection.docs["m-blocked"] = {
        "_id": "m-blocked",
        "score": 0.95,
        "payload": {"user_id": "alice", "status": "blocked", "classification": "general", "data": "Uses Windows"},
    }

    # Document 4: Vector space mismatch (wrong embedding model) -> must be excluded
    mem_mgr.collection.docs["m-model-mismatch"] = {
        "_id": "m-model-mismatch",
        "score": 0.95,
        "payload": {
            "user_id": "alice",
            "status": "active",
            "classification": "general",
            "data": "Uses C++",
            "embedding_model": "legacy-gemini-model",
        },
    }

    # Document 5: Valid general memory -> must be included
    mem_mgr.collection.docs["m-valid"] = {
        "_id": "m-valid",
        "score": 0.90,
        "payload": {
            "user_id": "alice",
            "status": "active",
            "classification": "general",
            "data": "Uses Python",
            "embedding_model": "openai/text-embedding-3-small",
        },
    }

    # Document 6: Labelled 'general' but contains allergy keywords -> forced to 'sensitive'
    mem_mgr.collection.docs["m-allergy"] = {
        "_id": "m-allergy",
        "score": 0.92,
        "payload": {
            "user_id": "alice",
            "status": "active",
            "classification": "general",  # mislabelled!
            "data": "Severe peanut allergy and carries EpiPen",
            "embedding_model": "openai/text-embedding-3-small",
        },
    }

    with client_fixture(mem_mgr, pref_engine, "alice") as client:
        query_resp = client.post("/api/v1/memories/query", json={"query": "languages and allergies"})
        assert query_resp.status_code == 200
        data = query_resp.json()

        general_texts = [m["text"] for m in data["general_memories"]]
        sensitive_texts = [m["text"] for m in data["sensitive_memories"]]

        # Only m-valid is in general
        assert "Uses Python" in general_texts
        assert len(general_texts) == 1

        # Unlabelled, invalid, blocked, model-mismatch NEVER returned
        all_returned = general_texts + sensitive_texts
        assert "Uses Mac" not in all_returned
        assert "Uses Linux" not in all_returned
        assert "Uses Windows" not in all_returned
        assert "Uses C++" not in all_returned

        # Allergy memory forced to sensitive
        assert any("peanut allergy" in t for t in sensitive_texts)


# ---------------------------------------------------------------------------
# TEST 7: Two-Account Isolation
# ---------------------------------------------------------------------------
def test_two_account_isolation():
    mem_mgr, pref_engine = build_test_environment("alice")

    # Add Alice's private memory
    mem_mgr.collection.docs["alice-m1"] = {
        "_id": "alice-m1",
        "score": 0.95,
        "payload": {
            "user_id": "alice",
            "status": "active",
            "classification": "general",
            "data": "Confidential Project FalconOmega",
            "embedding_model": "openai/text-embedding-3-small",
        },
    }

    # Alice's preference
    pref_engine.preferences_col.docs["alice-p1"] = {
        "_id": "alice-p1",
        "user_id": "alice",
        "preference_key": "concise",
        "preference_text": "Prefers concise bullet points",
        "status": "active",
    }

    # Bob queries: must see ZERO of Alice's data
    with client_fixture(mem_mgr, pref_engine, "bob") as client:
        # Query memories
        q_bob = client.post("/api/v1/memories/query", json={"query": "FalconOmega"})
        assert q_bob.status_code == 200
        bob_memories = q_bob.json()["general_memories"] + q_bob.json()["sensitive_memories"]
        assert len(bob_memories) == 0

        # Query preferences
        p_bob = client.get("/api/v1/preferences")
        assert p_bob.status_code == 200
        assert len(p_bob.json()) == 0

        # Bob cannot update or delete Alice's memory
        put_resp = client.put("/api/v1/memories/alice-m1", json={"text": "Hijacked"})
        assert put_resp.status_code == 404

        del_resp = client.delete("/api/v1/memories/alice-m1")
        assert del_resp.status_code == 404

    # Alice queries: sees her own data
    with client_fixture(mem_mgr, pref_engine, "alice") as client:
        q_alice = client.post("/api/v1/memories/query", json={"query": "FalconOmega"})
        alice_memories = q_alice.json()["general_memories"] + q_alice.json()["sensitive_memories"]
        assert len(alice_memories) == 1
        assert "FalconOmega" in alice_memories[0]["text"]

        p_alice = client.get("/api/v1/preferences")
        assert len(p_alice.json()) == 1


# ---------------------------------------------------------------------------
# TEST 8: One Observation and Three in One Chat NOT Promoted
# ---------------------------------------------------------------------------
def test_one_obs_and_three_in_same_chat_not_promoted():
    mem_mgr, pref_engine = build_test_environment("alice")

    with client_fixture(mem_mgr, pref_engine, "alice") as client:
        # 1st observation in chat-1
        r1 = client.post(
            "/api/v1/messages/ingest",
            json={"conversation_id": "chat-1", "text": "Please explain step by step how to build a dockerfile."},
        )
        assert r1.status_code == 200
        assert r1.json()["preference_promoted"] is False
        assert r1.json()["promoted_preferences"] == []

        # 2nd observation in same chat-1
        r2 = client.post(
            "/api/v1/messages/ingest",
            json={"conversation_id": "chat-1", "text": "Can you walk me through this step-by-step with layers?"},
        )
        assert r2.status_code == 200
        assert r2.json()["preference_promoted"] is False

        # 3rd observation in the SAME chat-1 (Rule requires >= 2 distinct chats!)
        r3 = client.post(
            "/api/v1/messages/ingest",
            json={"conversation_id": "chat-1", "text": "Break this down step by step so I understand."},
        )
        assert r3.status_code == 200
        # MUST NOT promote when all observations belong to a single chat!
        assert r3.json()["preference_promoted"] is False
        assert r3.json()["promoted_preferences"] == []

        # Preferences list is empty
        prefs = client.get("/api/v1/preferences").json()
        assert len(prefs) == 0


# ---------------------------------------------------------------------------
# TEST 9: Three Distinct Observations Across Two Chats Promoted & Locked
# ---------------------------------------------------------------------------
def test_three_obs_across_two_chats_promoted_and_locked():
    mem_mgr, pref_engine = build_test_environment("alice")

    with client_fixture(mem_mgr, pref_engine, "alice") as client:
        # Obs 1 in Chat A
        client.post(
            "/api/v1/messages/ingest",
            json={"conversation_id": "chat-A", "text": "Keep it concise and skip the fluff."},
        )

        # Obs 2 in Chat A
        client.post(
            "/api/v1/messages/ingest",
            json={"conversation_id": "chat-A", "text": "Give a concise answer only."},
        )

        # Obs 3 in Chat B (2nd distinct conversation!)
        r3 = client.post(
            "/api/v1/messages/ingest",
            json={"conversation_id": "chat-B", "text": "Be concise with this summary."},
        )
        assert r3.status_code == 200
        assert r3.json()["preference_promoted"] is True
        promoted = r3.json()["promoted_preferences"]
        assert len(promoted) == 1
        pref = promoted[0]
        assert pref["preference_key"] == "concise"
        assert pref["evidence_count"] >= 3
        assert pref["conversation_count"] >= 2
        assert "chat-A" in pref["conversation_ids"]
        assert "chat-B" in pref["conversation_ids"]

        pref_id = pref["_id"]

        # User correction: updates wording and locks preference
        corr_resp = client.put(
            f"/api/v1/preferences/{pref_id}",
            json={"preference_text": "I like ultra-short 1-sentence answers"},
        )
        assert corr_resp.status_code == 200
        updated = corr_resp.json()
        assert updated["preference_text"] == "I like ultra-short 1-sentence answers"
        assert updated["locked"] is True

        # Subsequent observation in Chat C must NOT overwrite locked wording
        r4 = client.post(
            "/api/v1/messages/ingest",
            json={"conversation_id": "chat-C", "text": "Please keep it concise."},
        )
        assert r4.status_code == 200
        pref_after = pref_engine.preferences_col.find_one({"_id": pref_id})
        assert pref_after["preference_text"] == "I like ultra-short 1-sentence answers"
        assert pref_after["locked"] is True


# ---------------------------------------------------------------------------
# TEST 10: Provider Layer Extractor & Validator Integration
# ---------------------------------------------------------------------------
def test_provider_layer_extractor_and_validator_integration():
    mem_mgr, pref_engine = build_test_environment("alice")

    # Attach MockExtractor that extracts multiple discrete facts
    mem_mgr.extractor = MockExtractor(
        default_facts=["User develops backend systems in Rust", "User has a severe nut allergy"]
    )
    # Attach MockValidator
    mem_mgr.validator = MockValidator(enabled=True, classification="sensitive")

    with client_fixture(mem_mgr, pref_engine, "alice") as client:
        resp = client.post(
            "/api/v1/messages/ingest",
            json={
                "conversation_id": "c-provider",
                "text": "I develop in Rust and I also have a severe nut allergy.",
                "role": "user",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "processed"
        facts = data["facts_extracted"]
        assert len(facts) == 2

        # Nut allergy must be sensitive
        allergy_fact = next(f for f in facts if "allergy" in f["memory"])
        assert allergy_fact["classification"] == "sensitive"

        # Verify docs in collection have metadata
        rust_doc = mem_mgr.collection.find_one({"payload.data": "User develops backend systems in Rust"})
        assert rust_doc is not None
        assert rust_doc["payload"]["model_used"] == "z-ai/glm-5.3-flash"
        assert rust_doc["payload"]["user_id"] == "alice"
