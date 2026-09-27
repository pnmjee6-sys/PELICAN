from __future__ import annotations

from scoped_mongodb import IDENTITY_FIELDS, ScopedMongoDB


class FakeCollection:
    def __init__(self):
        self.pipeline = None

    def list_search_indexes(self, name):
        return [{"name": name}]

    def aggregate(self, pipeline):
        self.pipeline = pipeline
        return [{"_id": "m1", "score": 0.9, "payload": {"user_id": "alice", "data": "fact"}}]


def test_vector_filter_is_inside_vector_search_stage():
    store = object.__new__(ScopedMongoDB)
    store.collection = FakeCollection()
    store.index_name = "scoped"

    results = store.search("query", [0.1, 0.2], top_k=3, filters={"user_id": "alice"})

    vector_search = store.collection.pipeline[0]["$vectorSearch"]
    assert vector_search["filter"] == {"payload.user_id": {"$eq": "alice"}}
    assert not any("$match" in stage for stage in store.collection.pipeline)
    assert results[0].payload["user_id"] == "alice"


def test_index_definition_contains_all_identity_filter_fields():
    store = object.__new__(ScopedMongoDB)
    store.embedding_model_dims = 1536
    fields = store.vector_definition["fields"]
    filter_paths = {field["path"] for field in fields if field["type"] == "filter"}
    assert filter_paths == {f"payload.{field}" for field in IDENTITY_FIELDS}
    assert fields[0]["numDimensions"] == 1536


def test_rejects_mongodb_operator_injection():
    try:
        ScopedMongoDB._payload_filter({"user_id": {"$ne": "alice"}})
    except ValueError:
        pass
    else:
        raise AssertionError("operator-bearing dict should be rejected")
