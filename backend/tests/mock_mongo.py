"""In-memory mock MongoDB implementation for fully isolated offline testing.

Provides drop-in replacements for pymongo's MongoClient, Database, and Collection
without opening network sockets or connecting to production MongoDB Atlas.
"""

from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Union


def _get_dotted_value(doc: Dict[str, Any], path: str) -> Any:
    parts = path.split(".")
    curr = doc
    for part in parts:
        if not isinstance(curr, dict):
            return None
        curr = curr.get(part)
    return curr


def _set_dotted_value(doc: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    curr = doc
    for part in parts[:-1]:
        if part not in curr or not isinstance(curr[part], dict):
            curr[part] = {}
        curr = curr[part]
    curr[parts[-1]] = value


class MockMongoCollection:
    """In-memory collection supporting the complete query and update pattern of Context Passport."""

    def __init__(self, name: str = "mock_col", db: Optional[Any] = None):
        self.name = name
        self.db = db
        self.docs: Dict[str, Dict[str, Any]] = {}
        self.indexes: List[Any] = []
        self.search_indexes: List[str] = [
            f"{name}_vector_index_scoped",
            "memories_vector_index_scoped",
        ]
        self._initialized = False

    @staticmethod
    def matches(doc: Dict[str, Any], query: Optional[Dict[str, Any]]) -> bool:
        if not query:
            return True

        for key, expected in query.items():
            actual = _get_dotted_value(doc, key)

            if isinstance(expected, dict):
                # Operator checks
                if "$ne" in expected:
                    if actual == expected["$ne"]:
                        return False
                if "$eq" in expected:
                    if actual != expected["$eq"]:
                        return False
                if "$gte" in expected:
                    if actual is None:
                        return False
                    exp_val = expected["$gte"]
                    if str(actual) < str(exp_val):
                        return False
                if "$in" in expected:
                    if actual not in expected["$in"]:
                        return False
            elif actual != expected:
                return False

        return True

    def find_one(self, query: Optional[Dict[str, Any]] = None, *args, **kwargs) -> Optional[Dict[str, Any]]:
        for doc in self.docs.values():
            if self.matches(doc, query):
                return copy.deepcopy(doc)
        return None

    def find(self, query: Optional[Dict[str, Any]] = None, *args, **kwargs) -> List[Dict[str, Any]]:
        matches = [copy.deepcopy(doc) for doc in self.docs.values() if self.matches(doc, query)]
        # Support sort keyword if provided
        sort_arg = kwargs.get("sort") or (args[0] if len(args) > 0 and isinstance(args[0], list) else None)
        if sort_arg and isinstance(sort_arg, list):
            for field_name, direction in reversed(sort_arg):
                reverse = direction < 0
                matches.sort(
                    key=lambda d: str(_get_dotted_value(d, field_name) or ""),
                    reverse=reverse,
                )
        return matches

    def count_documents(self, query: Optional[Dict[str, Any]] = None) -> int:
        if not query:
            return len(self.docs)
        return sum(1 for doc in self.docs.values() if self.matches(doc, query))

    def insert_one(self, doc: Dict[str, Any]) -> SimpleNamespace:
        d = copy.deepcopy(doc)
        d.setdefault("_id", str(uuid.uuid4()))
        self.docs[d["_id"]] = d
        self._initialized = True
        return SimpleNamespace(inserted_id=d["_id"])

    def insert_many(self, docs: List[Dict[str, Any]]) -> SimpleNamespace:
        inserted_ids = []
        for doc in docs:
            res = self.insert_one(doc)
            inserted_ids.append(res.inserted_id)
        return SimpleNamespace(inserted_ids=inserted_ids)

    def update_one(
        self,
        query: Dict[str, Any],
        update: Dict[str, Any],
        upsert: bool = False,
    ) -> SimpleNamespace:
        doc = None
        for item in self.docs.values():
            if self.matches(item, query):
                doc = item
                break

        if doc is None and upsert:
            doc = {}
            for k, v in query.items():
                if not isinstance(v, dict) and not k.startswith("$"):
                    _set_dotted_value(doc, k, v)
            doc.setdefault("_id", str(uuid.uuid4()))
            self.docs[doc["_id"]] = doc
            self._initialized = True

            # Apply $setOnInsert
            for k, v in update.get("$setOnInsert", {}).items():
                _set_dotted_value(doc, k, copy.deepcopy(v))

            # Apply $set
            for k, v in update.get("$set", {}).items():
                _set_dotted_value(doc, k, copy.deepcopy(v))

            return SimpleNamespace(matched_count=0, modified_count=1, upserted_id=doc["_id"])

        if doc is None:
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)

        # Apply $set
        for k, v in update.get("$set", {}).items():
            _set_dotted_value(doc, k, copy.deepcopy(v))

        # Apply $pull
        for k, v in update.get("$pull", {}).items():
            curr_list = _get_dotted_value(doc, k)
            if isinstance(curr_list, list):
                _set_dotted_value(doc, k, [item for item in curr_list if item != v])

        return SimpleNamespace(matched_count=1, modified_count=1, upserted_id=None)

    def update_many(self, query: Dict[str, Any], update: Dict[str, Any]) -> SimpleNamespace:
        count = 0
        for doc in self.docs.values():
            if self.matches(doc, query):
                for k, v in update.get("$set", {}).items():
                    _set_dotted_value(doc, k, copy.deepcopy(v))
                count += 1
        return SimpleNamespace(matched_count=count, modified_count=count)

    def delete_one(self, query: Dict[str, Any]) -> SimpleNamespace:
        target_id = None
        for doc_id, doc in self.docs.items():
            if self.matches(doc, query):
                target_id = doc_id
                break
        if target_id is not None:
            del self.docs[target_id]
            return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    def delete_many(self, query: Dict[str, Any]) -> SimpleNamespace:
        to_del = [doc_id for doc_id, doc in self.docs.items() if self.matches(doc, query)]
        for doc_id in to_del:
            del self.docs[doc_id]
        return SimpleNamespace(deleted_count=len(to_del))

    def aggregate(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results = list(self.docs.values())
        for stage in pipeline:
            if "$match" in stage:
                results = [d for d in results if self.matches(d, stage["$match"])]
            elif "$group" in stage:
                group_spec = stage["$group"]
                id_expr = group_spec.get("_id")
                groups: Dict[Any, List[Dict[str, Any]]] = {}
                for d in results:
                    val = _get_dotted_value(d, id_expr.lstrip("$")) if isinstance(id_expr, str) and id_expr.startswith("$") else None
                    groups.setdefault(val, []).append(d)
                grouped: List[Dict[str, Any]] = []
                for val, items in groups.items():
                    entry = {"_id": val}
                    for k, v in group_spec.items():
                        if k == "_id":
                            continue
                        if isinstance(v, dict) and "$sum" in v:
                            entry[k] = len(items)
                    grouped.append(entry)
                results = grouped
            elif "$vectorSearch" in stage:
                filter_spec = stage["$vectorSearch"].get("filter", {})
                results = [d for d in results if self.matches(d, filter_spec)]
            elif "$limit" in stage:
                results = results[: stage["$limit"]]
        return [copy.deepcopy(d) for d in results]

    def create_index(self, *args, **kwargs) -> str:
        self.indexes.append((args, kwargs))
        return "idx_" + str(len(self.indexes))

    def create_search_index(self, model: Any) -> str:
        name = getattr(model, "name", "mock_search_index")
        if name not in self.search_indexes:
            self.search_indexes.append(name)
        return name

    def list_search_indexes(self) -> List[Dict[str, str]]:
        return [{"name": name} for name in self.search_indexes]

    def drop(self) -> None:
        self.docs.clear()
        self._initialized = False


import pymongo.database

_ORIGINAL_DATABASE_COMMAND = pymongo.database.Database.command


class MockDatabase:
    """In-memory mock database for isolated tests."""

    def __init__(self, name: str, client: MockMongoClient):
        self.name = name
        self.client = client
        self.collections: Dict[str, MockMongoCollection] = {}

    def __getitem__(self, col_name: str) -> MockMongoCollection:
        if col_name not in self.collections:
            self.collections[col_name] = MockMongoCollection(name=col_name, db=self)
        return self.collections[col_name]

    def list_collection_names(self, authorizedCollections: bool = True) -> List[str]:
        return [
            name
            for name, col in self.collections.items()
            if col.docs or getattr(col, "_initialized", False)
        ]

    def command(self, cmd: str, *args, **kwargs) -> Dict[str, Any]:
        if pymongo.database.Database.command != _ORIGINAL_DATABASE_COMMAND:
            return pymongo.database.Database.command(self, cmd, *args, **kwargs)
        if cmd == "ping":
            return {"ok": 1.0}
        return {"ok": 1.0}


class MockMongoClient:
    """In-memory mock client that intercepts MongoClient calls during tests."""

    def __init__(self, *args, **kwargs):
        self.databases: Dict[str, MockDatabase] = {}
        self.admin = MockDatabase("admin", self)
        self.serverSelectionTimeoutMS = 10000

    def __getitem__(self, db_name: str) -> MockDatabase:
        if db_name not in self.databases:
            self.databases[db_name] = MockDatabase(db_name, self)
        return self.databases[db_name]

    def close(self) -> None:
        pass


# Global singleton mock client so multiple components within a test share state
_SHARED_MOCK_CLIENT = MockMongoClient()


def get_shared_mock_client() -> MockMongoClient:
    return _SHARED_MOCK_CLIENT


def reset_mock_database() -> None:
    """Clears all collections across all databases in the shared mock client."""
    for db in _SHARED_MOCK_CLIENT.databases.values():
        for col in db.collections.values():
            col.drop()
