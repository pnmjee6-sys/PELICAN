"""MongoDB adapter for Mem0 2.2.0 with Atlas pre-filtered vector search.

The installed Mem0 adapter inserts ``$match`` after ``$vectorSearch``. That can
drop the requested user's matches after Atlas has already selected global top-k
candidates. This adapter places identity filters inside ``$vectorSearch`` and
creates the matching Atlas filter fields.
"""

from __future__ import annotations

import logging
import os
from importlib.metadata import version
from typing import Any, Dict, List, Optional

from mem0.vector_stores.mongodb import MongoDB, OutputData
from pymongo.driver_info import DriverInfo
from pymongo.errors import PyMongoError
from pymongo.operations import SearchIndexModel


logger = logging.getLogger(__name__)
IDENTITY_FIELDS = ("user_id", "agent_id", "run_id")
DRIVER_METADATA = DriverInfo(name="ContextPassport-Mem0", version=version("mem0ai"))


class ScopedMongoDB(MongoDB):
    """Drop-in Mem0 MongoDB store whose tenant filters run before ANN ranking."""

    def __init__(self, db_name: str, collection_name: str, embedding_model_dims: int, mongo_uri: str):
        self.configured_index_name = os.getenv(
            "MONGODB_VECTOR_INDEX_NAME", f"{collection_name}_vector_index_scoped"
        )
        super().__init__(db_name, collection_name, embedding_model_dims, mongo_uri)

    @property
    def vector_definition(self) -> dict[str, Any]:
        return {
            "fields": [
                {
                    "type": "vector",
                    "path": "embedding",
                    "numDimensions": self.embedding_model_dims,
                    "similarity": "cosine",
                },
                *({"type": "filter", "path": f"payload.{field}"} for field in IDENTITY_FIELDS),
            ]
        }

    @property
    def text_definition(self) -> dict[str, Any]:
        return {
            "mappings": {
                "dynamic": False,
                "fields": {
                    "payload": {
                        "type": "document",
                        "fields": {
                            "data": {"type": "string"},
                            "text_lemmatized": {"type": "string"},
                            **{field: {"type": "token"} for field in IDENTITY_FIELDS},
                        },
                    }
                },
            }
        }

    def create_col(self):
        try:
            database = self.client[self.db_name]
            collection = database[self.collection_name]
            if self.collection_name not in database.list_collection_names(authorizedCollections=True):
                collection.insert_one({"_id": 0, "placeholder": True})
                collection.delete_one({"_id": 0})

            self.index_name = self.configured_index_name
            try:
                existing_names = [idx.get("name") for idx in collection.list_search_indexes()]
                if self.index_name not in existing_names:
                    collection.create_search_index(
                        SearchIndexModel(
                            name=self.index_name,
                            type="vectorSearch",
                            definition=self.vector_definition,
                        )
                    )
            except Exception as exc:
                logger.warning("Scoped vector index notice: %s", exc)

            self.text_index_name = f"{self.collection_name}_text_search_index_scoped"
            # Hybrid text search is optional. Atlas Free has a small index
            # quota, so only create this extra index when explicitly enabled.
            if os.getenv("ENABLE_ATLAS_TEXT_INDEX", "false").lower() == "true":
                try:
                    existing_names = [idx.get("name") for idx in collection.list_search_indexes()]
                    if self.text_index_name not in existing_names:
                        collection.create_search_index(
                            SearchIndexModel(name=self.text_index_name, definition=self.text_definition)
                        )
                except Exception as exc:
                    logger.warning("Could not create optional Atlas text index: %s", exc)
            return collection
        except PyMongoError:
            logger.exception("Could not initialize MongoDB collection or search indexes")
            raise

    @staticmethod
    def _payload_filter(filters: Optional[Dict[str, Any]]) -> dict[str, Any]:
        if not filters:
            return {}
        query: dict[str, Any] = {}
        for key, value in filters.items():
            if not key or key.startswith("$") or "." in key:
                raise ValueError(f"Unsafe MongoDB filter key: {key!r}")
            MongoDB._validate_filter_value(key, value)
            query[f"payload.{key}"] = {"$in": value} if isinstance(value, list) else {"$eq": value}
        return query

    def search(
        self,
        query: str,
        vectors: List[float],
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[OutputData]:
        if not list(self.collection.list_search_indexes(name=self.index_name)):
            return []
        vector_stage: dict[str, Any] = {
            "index": self.index_name,
            "limit": top_k,
            "numCandidates": min(max(top_k * 20, 100), 10000),
            "queryVector": vectors,
            "path": "embedding",
        }
        pre_filter = self._payload_filter(filters)
        if pre_filter:
            vector_stage["filter"] = pre_filter
        pipeline = [
            {"$vectorSearch": vector_stage},
            {"$set": {"score": {"$meta": "vectorSearchScore"}}},
            {"$project": {"embedding": 0}},
        ]
        try:
            documents = list(self.collection.aggregate(pipeline))
        except Exception:
            logger.exception("Scoped vector search failed")
            raise
        return [
            OutputData(id=str(doc["_id"]), score=doc.get("score"), payload=doc.get("payload"))
            for doc in documents
        ]

    def keyword_search(self, query: str, top_k: int = 5, filters: Optional[Dict[str, Any]] = None):
        clauses = [
            {"equals": {"path": path, "value": expression["$eq"]}}
            for path, expression in self._payload_filter(filters).items()
            if "$eq" in expression
        ]
        search: dict[str, Any] = {
            "index": self.text_index_name,
            "compound": {
                "must": [
                    {
                        "text": {
                            "query": query,
                            "path": ["payload.data", "payload.text_lemmatized"],
                        }
                    }
                ],
                "filter": clauses,
            },
        }
        pipeline = [
            {"$search": search},
            {"$limit": top_k},
            {"$set": {"score": {"$meta": "searchScore"}}},
            {"$project": {"embedding": 0}},
        ]
        try:
            documents = list(self.collection.aggregate(pipeline))
        except Exception as exc:
            logger.warning("Scoped keyword search unavailable: %s", exc)
            return None
        return [
            OutputData(id=str(doc["_id"]), score=doc.get("score"), payload=doc.get("payload"))
            for doc in documents
        ]
