"""Test suite for Step 3 — Embeddings and safe migration.

Verifies:
1. 1536-dimensional vectors for text-embedding-3-small
2. Model identity tagging on all vector documents
3. Exact migration count and metadata preservation
4. Duplicate-safe idempotent reruns
5. Complete rollback capability restoring original state
6. Ownership filter enforced inside $vectorSearch
7. Rejection of mixed vector spaces (Gemini vs OpenAI vectors never mixed)
8. Recent write catchup filters strictly by active embedding model
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
import pytest
from pymongo import MongoClient

os.environ["APP_ENV"] = "test"
os.environ["ALLOW_TEST_AUTH"] = "true"
os.environ["ALLOW_OFFLINE_EMBEDDINGS"] = "true"

from migration import VectorMigrationManager, MigrationError
from memory_manager import MemoryManager, generate_deterministic_embedding
from providers import MockEmbedder, OpenRouterEmbedder
from settings import Settings, load_settings
from scoped_mongodb import ScopedMongoDB


@pytest.fixture(scope="module")
def mongo_client():
    settings = load_settings(require_gemini=False)
    client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=10000)
    yield client
    client.close()


@pytest.fixture
def test_db_and_col(mongo_client):
    settings = load_settings(require_gemini=False)
    db_name = settings.mongodb_db_name
    col_name = f"test_migration_{uuid.uuid4().hex[:8]}"
    db = mongo_client[db_name]
    col = db[col_name]
    yield db_name, col_name, col
    # Teardown: drop test collection and any backup collections
    col.drop()
    for name in db.list_collection_names():
        if name.startswith(f"{col_name}_backup_"):
            db[name].drop()


# ---------------------------------------------------------------------------
# 1. Dimensions & Model Identity Tests
# ---------------------------------------------------------------------------
def test_embedding_dimensions():
    embedder = MockEmbedder(dimensions=1536)
    vec = embedder.embed("Test embedding dimensions")
    assert len(vec) == 1536
    assert isinstance(vec[0], float)


def test_memory_add_tags_embedding_model(mongo_client):
    settings = load_settings(require_gemini=False)
    manager = MemoryManager(settings)
    user_id = f"test-tag-{uuid.uuid4().hex[:6]}"

    try:
        res = manager.add("User enjoys functional programming in Elixir.", user_id=user_id)
        assert res.get("results")
        mem_id = res["results"][0]["id"]

        doc = manager.collection.find_one({"_id": mem_id})
        assert doc is not None
        payload = doc.get("payload", {})
        assert payload.get("embedding_model") == settings.embedding_model
        assert payload.get("user_id") == user_id
        assert len(doc.get("embedding", [])) == 1536
    finally:
        manager.delete_all(user_id)


def test_memory_update_updates_embedding_model(mongo_client):
    settings = load_settings(require_gemini=False)
    manager = MemoryManager(settings)
    user_id = f"test-update-{uuid.uuid4().hex[:6]}"

    try:
        mem_id = str(uuid.uuid4())
        vec = manager.embed_text("Initial memory text for update test.")
        manager.collection.insert_one({
            "_id": mem_id,
            "text": "Initial memory text for update test.",
            "embedding": vec,
            "payload": {
                "user_id": user_id,
                "data": "Initial memory text for update test.",
                "classification": "general",
                "status": "active",
                "embedding_model": "models/gemini-embedding-001",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        })

        # Update memory text
        updated = manager.update(mem_id, user_id=user_id, text="Updated memory text with new wording.")
        assert updated["id"] == mem_id

        doc = manager.collection.find_one({"_id": mem_id})
        expected_model = manager.active_embedding_model or settings.embedding_model
        assert doc["payload"]["embedding_model"] == expected_model
    finally:
        manager.delete_all(user_id)


# ---------------------------------------------------------------------------
# 2. Migration, Rerun & Rollback Tests
# ---------------------------------------------------------------------------
def test_migration_exact_count_and_data_preservation(mongo_client, test_db_and_col):
    db_name, col_name, col = test_db_and_col
    embedder = MockEmbedder(dimensions=1536)

    # Insert 5 synthetic legacy documents
    legacy_docs = []
    for i in range(5):
        legacy_docs.append({
            "_id": f"legacy-doc-{i}",
            "text": f"User preference fact number {i}",
            "embedding": [0.01 * (i + 1)] * 1536,
            "payload": {
                "user_id": "alice",
                "data": f"User preference fact number {i}",
                "classification": "general",
                "status": "active",
                "embedding_model": "models/gemini-embedding-001",
                "created_at": "2026-09-01T00:00:00Z",
            },
        })
    col.insert_many(legacy_docs)

    migrator = VectorMigrationManager(mongo_client, db_name, col_name)

    # Pre-migration distribution
    dist_before = migrator.get_model_distribution()
    assert dist_before.get("models/gemini-embedding-001") == 5

    # Run migration
    res = migrator.migrate_collection(
        embedder=embedder,
        target_model="openai/text-embedding-3-small",
        expected_dims=1536,
    )

    assert res["status"] == "success"
    assert res["migrated_count"] == 5
    assert res["backup_collection"] is not None

    # Post-migration checks
    dist_after = migrator.get_model_distribution()
    assert dist_after.get("openai/text-embedding-3-small") == 5
    assert dist_after.get("models/gemini-embedding-001", 0) == 0

    # Verify every document preserved original data and received new embedding
    for i in range(5):
        doc = col.find_one({"_id": f"legacy-doc-{i}"})
        assert doc is not None
        assert doc["payload"]["user_id"] == "alice"
        assert doc["payload"]["classification"] == "general"
        assert doc["payload"]["prior_embedding_model"] == "models/gemini-embedding-001"
        assert doc["payload"]["embedding_model"] == "openai/text-embedding-3-small"
        assert len(doc["embedding"]) == 1536
        # Must not be the old dummy vector
        assert doc["embedding"] != [0.01 * (i + 1)] * 1536


def test_migration_duplicate_safe_reruns(mongo_client, test_db_and_col):
    db_name, col_name, col = test_db_and_col
    embedder = MockEmbedder(dimensions=1536)

    col.insert_one({
        "_id": "doc-rerun-1",
        "text": "User likes Python",
        "embedding": [0.05] * 1536,
        "payload": {
            "user_id": "bob",
            "data": "User likes Python",
            "embedding_model": "models/gemini-embedding-001",
        },
    })

    migrator = VectorMigrationManager(mongo_client, db_name, col_name)

    # First run
    res1 = migrator.migrate_collection(embedder=embedder, target_model="openai/text-embedding-3-small")
    assert res1["migrated_count"] == 1
    assert res1["status"] == "success"

    # Second run (idempotent rerun)
    res2 = migrator.migrate_collection(embedder=embedder, target_model="openai/text-embedding-3-small")
    assert res2["migrated_count"] == 0
    assert res2["status"] == "already_up_to_date"
    assert col.count_documents({}) == 1


def test_migration_rollback_restores_original_state(mongo_client, test_db_and_col):
    db_name, col_name, col = test_db_and_col
    embedder = MockEmbedder(dimensions=1536)
    original_vector = [0.42] * 1536

    col.insert_one({
        "_id": "doc-rollback-1",
        "text": "Rollback test fact",
        "embedding": list(original_vector),
        "payload": {
            "user_id": "carol",
            "data": "Rollback test fact",
            "embedding_model": "models/gemini-embedding-001",
        },
    })

    migrator = VectorMigrationManager(mongo_client, db_name, col_name)

    # Migrate
    res = migrator.migrate_collection(embedder=embedder, target_model="openai/text-embedding-3-small")
    backup_name = res["backup_collection"]

    # Verify migrated
    migrated_doc = col.find_one({"_id": "doc-rollback-1"})
    assert migrated_doc["payload"]["embedding_model"] == "openai/text-embedding-3-small"
    assert migrated_doc["embedding"] != original_vector

    # Rollback
    rollback_res = migrator.rollback(backup_name)
    assert rollback_res["status"] == "restored"
    assert rollback_res["restored_count"] == 1

    # Verify restored document
    restored_doc = col.find_one({"_id": "doc-rollback-1"})
    assert restored_doc["payload"]["embedding_model"] == "models/gemini-embedding-001"
    assert restored_doc["embedding"] == original_vector


# ---------------------------------------------------------------------------
# 3. Vector Space Rejection & Mixed Index Isolation
# ---------------------------------------------------------------------------
def test_rejection_of_mixed_vector_spaces(mongo_client):
    """
    Never score or expose memories from a different embedding model.
    A collection holding both Gemini and OpenAI vectors must filter by model.
    """
    settings = load_settings(require_gemini=False)
    user_id = f"test-mixed-{uuid.uuid4().hex[:6]}"

    # Set up manager with OpenAI model
    openai_settings = Settings(
        llm_provider=settings.llm_provider,
        openrouter_api_key=settings.openrouter_api_key,
        openrouter_base_url=settings.openrouter_base_url,
        extraction_primary_model=settings.extraction_primary_model,
        extraction_fallback_model=settings.extraction_fallback_model,
        embedding_provider="openrouter",
        embedding_model="openai/text-embedding-3-small",
        embedding_dims=1536,
        enable_jev_validation=settings.enable_jev_validation,
        openrouter_spending_cap=settings.openrouter_spending_cap,
        provider_timeout_seconds=settings.provider_timeout_seconds,
        provider_max_retries=settings.provider_max_retries,
        gemini_api_key=settings.gemini_api_key,
        gemini_extraction_model=settings.gemini_extraction_model,
        gemini_embedding_model=settings.gemini_embedding_model,
        gemini_embedding_dims=1536,
        mongodb_uri=settings.mongodb_uri,
        mongodb_db_name=settings.mongodb_db_name,
        mongodb_collection_name=settings.mongodb_collection_name,
        mongodb_vector_index_name=settings.mongodb_vector_index_name,
        history_db_path=settings.history_db_path,
    )
    manager = MemoryManager(openai_settings)

    try:
        # Insert one memory with OpenAI model
        vec_openai = generate_deterministic_embedding("User loves Rust and WebAssembly")
        manager.collection.insert_one({
            "_id": f"openai-mem-{user_id}",
            "text": "User loves Rust and WebAssembly",
            "embedding": vec_openai,
            "payload": {
                "user_id": user_id,
                "data": "User loves Rust and WebAssembly",
                "classification": "general",
                "status": "active",
                "embedding_model": "openai/text-embedding-3-small",
                "created_at": "2026-09-01T00:00:00Z",
            },
        })

        # Insert one memory with Gemini model (incompatible vector space)
        vec_gemini = generate_deterministic_embedding("User loves Python and Django")
        manager.collection.insert_one({
            "_id": f"gemini-mem-{user_id}",
            "text": "User loves Python and Django",
            "embedding": vec_gemini,
            "payload": {
                "user_id": user_id,
                "data": "User loves Python and Django",
                "classification": "general",
                "status": "active",
                "embedding_model": "models/gemini-embedding-001",
                "created_at": "2026-09-01T00:00:00Z",
            },
        })

        # Search using OpenAI model configuration
        search_res = manager.search("What language and framework does user like?", user_id=user_id)
        results = search_res["general_memories"] + search_res["sensitive_memories"]
        returned_ids = [r["id"] for r in results]

        # Must NOT include the Gemini memory!
        assert f"gemini-mem-{user_id}" not in returned_ids
    finally:
        manager.delete_all(user_id)


def test_mixed_space_atlas_top_k_cannot_hide_older_compatible_memory(mongo_client, monkeypatch):
    settings = load_settings(require_gemini=False)
    user_id = f"test-mixed-topk-{uuid.uuid4().hex[:6]}"
    manager = MemoryManager(settings)
    compatible_id = f"openai-{user_id}"
    incompatible_id = f"gemini-{user_id}"
    manager.collection.insert_one({
        "_id": compatible_id,
        "embedding": generate_deterministic_embedding("User loves Rust and WebAssembly"),
        "payload": {"user_id": user_id, "data": "User loves Rust and WebAssembly", "classification": "general", "status": "active", "embedding_model": settings.embedding_model, "created_at": "2026-09-01T00:00:00Z"},
    })
    manager.collection.insert_one({
        "_id": incompatible_id,
        "embedding": generate_deterministic_embedding("User loves Python and Django"),
        "payload": {"user_id": user_id, "data": "User loves Python and Django", "classification": "general", "status": "active", "embedding_model": "models/gemini-embedding-001", "created_at": "2026-09-01T00:00:00Z"},
    })
    incompatible = manager.collection.find_one({"_id": incompatible_id})
    incompatible["score"] = 0.99
    monkeypatch.setattr(manager.collection, "aggregate", lambda _pipeline: [incompatible])

    result = manager.search("User loves Rust and WebAssembly", user_id=user_id, top_k=1)
    assert [item["id"] for item in result["general_memories"]] == [compatible_id]


def test_recent_write_catchup_respects_embedding_model(mongo_client):
    """Recent writes must only include documents with matching embedding_model."""
    settings = load_settings(require_gemini=False)
    user_id = f"test-recent-{uuid.uuid4().hex[:6]}"
    manager = MemoryManager(settings)

    try:
        # Add fresh memory with matching model
        res = manager.add("Fresh recent fact with active model", user_id=user_id)
        matching_id = res["results"][0]["id"]

        # Insert fresh memory with DIFFERENT model
        different_id = f"diff-model-{user_id}"
        manager.collection.insert_one({
            "_id": different_id,
            "text": "Fresh recent fact with incompatible model",
            "embedding": generate_deterministic_embedding("Fresh recent fact with incompatible model"),
            "payload": {
                "user_id": user_id,
                "data": "Fresh recent fact with incompatible model",
                "classification": "general",
                "status": "active",
                "embedding_model": "incompatible/other-model-space",
                "created_at": "2026-09-26T00:00:00Z",
            },
        })

        search_res = manager.search("Fresh recent fact", user_id=user_id)
        recalled_ids = [r["id"] for r in search_res["results"]]

        assert different_id not in recalled_ids
    finally:
        manager.delete_all(user_id)


# ---------------------------------------------------------------------------
# 4. Scoped MongoDB Tenant Filter in $vectorSearch
# ---------------------------------------------------------------------------
def test_scoped_mongodb_vector_search_filter():
    store = object.__new__(ScopedMongoDB)
    store.index_name = "test_index"
    store.embedding_model_dims = 1536

    class FakeAggCollection:
        def __init__(self):
            self.pipeline = None

        def list_search_indexes(self, name):
            return [{"name": name}]

        def aggregate(self, pipeline):
            self.pipeline = pipeline
            return []

    store.collection = FakeAggCollection()
    store.search("query", [0.1] * 1536, top_k=5, filters={"user_id": "tenant-alice"})

    vector_stage = store.collection.pipeline[0]["$vectorSearch"]
    assert "filter" in vector_stage
    assert vector_stage["filter"] == {"payload.user_id": {"$eq": "tenant-alice"}}
