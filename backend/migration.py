"""Safe vector migration and rollback tool for Context Passport.

Enforces:
- Mathematical vector space isolation: never mix Gemini and OpenAI vectors
- Explicit model identity: every vector document records payload.embedding_model
- Safe backup and verification before modification
- Idempotent and duplicate-safe re-runs
- Complete rollback capability restoring original embeddings and metadata
- Zero memory loss: preserves all text, classifications, and lifecycle state
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pymongo import MongoClient
from pymongo.collection import Collection

from providers import Embedder

logger = logging.getLogger(__name__)


class MigrationError(Exception):
    """Raised when migration fails or pre-flight verification fails."""
    pass


class VectorMigrationManager:
    """Manages model-aware vector migration and rollback across MongoDB collections."""

    def __init__(
        self,
        mongo_client: MongoClient,
        db_name: str,
        collection_name: str = "memories",
    ):
        self.client = mongo_client
        self.db = self.client[db_name]
        self.collection: Collection = self.db[collection_name]

    def get_model_distribution(self) -> Dict[str, int]:
        """Returns count of documents grouped by payload.embedding_model."""
        pipeline = [
            {"$group": {"_id": "$payload.embedding_model", "count": {"$sum": 1}}},
        ]
        distribution: Dict[str, int] = {}
        for item in self.collection.aggregate(pipeline):
            model_key = item["_id"] or "unspecified_legacy"
            distribution[model_key] = item["count"]
        return distribution

    def migrate_collection(
        self,
        embedder: Embedder,
        target_model: str = "openai/text-embedding-3-small",
        expected_dims: int = 1536,
        backup_collection_name: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Migrates vector embeddings to target_model safely:
        1. Identifies documents requiring migration (missing model tag or different model)
        2. Creates full rollback backup before modifying any documents
        3. Generates new embeddings and updates documents atomically
        4. Verifies post-migration counts and dimensions
        """
        # Find candidates needing migration
        query: Dict[str, Any] = {"payload.embedding_model": {"$ne": target_model}}
        if user_id:
            query["payload.user_id"] = user_id

        candidates = list(self.collection.find(query))
        total_candidates = len(candidates)

        if total_candidates == 0:
            logger.info("No documents require migration to %s", target_model)
            return {
                "migrated_count": 0,
                "target_model": target_model,
                "backup_collection": None,
                "status": "already_up_to_date",
            }

        # 1. Create backup for rollback
        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        backup_name = backup_collection_name or f"{self.collection.name}_backup_{timestamp_str}"
        backup_col: Collection = self.db[backup_name]

        # Backup candidates
        backup_docs = [dict(doc) for doc in candidates]
        backup_col.insert_many(backup_docs)
        backup_col.create_index([("payload.user_id", 1)])

        logger.info(
            "Backed up %d documents to %s prior to migration",
            total_candidates,
            backup_name,
        )

        migrated_count = 0
        now_iso = now.isoformat()

        # 2. Re-embed and update each document
        try:
            for doc in candidates:
                doc_id = doc["_id"]
                payload = doc.get("payload", {})
                text_to_embed = payload.get("data") or doc.get("text", "")

                if not text_to_embed:
                    logger.warning("Document %s has empty text; keeping existing embedding", doc_id)
                    continue

                new_vector = embedder.embed(text_to_embed)
                if len(new_vector) != expected_dims:
                    raise MigrationError(
                        f"Embedder returned {len(new_vector)} dims, expected {expected_dims}"
                    )

                original_model = payload.get("embedding_model", "models/gemini-embedding-001")

                self.collection.update_one(
                    {"_id": doc_id},
                    {
                        "$set": {
                            "embedding": new_vector,
                            "payload.embedding_model": target_model,
                            "payload.migrated_at": now_iso,
                            "payload.prior_embedding_model": original_model,
                        }
                    },
                )
                migrated_count += 1
        except Exception as exc:
            logger.error("Migration encountered error (%s); rolling back automatically", exc)
            self.rollback(backup_name)
            raise MigrationError(f"Migration aborted and rolled back: {exc}") from exc

        # 3. Verify final state
        remaining_unmigrated = self.collection.count_documents(query)
        if remaining_unmigrated != 0:
            raise MigrationError(f"Verification failed: {remaining_unmigrated} documents unmigrated")

        return {
            "migrated_count": migrated_count,
            "target_model": target_model,
            "backup_collection": backup_name,
            "status": "success",
        }

    def rollback(self, backup_collection_name: str) -> Dict[str, Any]:
        """Restores original embeddings and metadata from backup collection."""
        backup_col: Collection = self.db[backup_collection_name]
        backup_docs = list(backup_col.find())
        restored_count = 0

        for doc in backup_docs:
            doc_id = doc["_id"]
            self.collection.update_one(
                {"_id": doc_id},
                {
                    "$set": {
                        "embedding": doc.get("embedding"),
                        "payload": doc.get("payload"),
                        "text": doc.get("text"),
                    }
                },
                upsert=True,
            )
            restored_count += 1

        logger.info("Restored %d documents from backup %s", restored_count, backup_collection_name)
        return {
            "restored_count": restored_count,
            "backup_collection": backup_collection_name,
            "status": "restored",
        }
