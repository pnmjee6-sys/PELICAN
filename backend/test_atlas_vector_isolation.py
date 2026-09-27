from __future__ import annotations

import json
import time
import uuid
from pymongo import MongoClient
from settings import load_settings


def test_atlas_vector_isolation() -> dict[str, object]:
    """
    Directly tests MongoDB Atlas $vectorSearch execution and user isolation
    using 1536-dimensional vectors with pre-filtering on payload.user_id.
    """
    settings = load_settings(require_gemini=False)
    client = MongoClient(settings.mongodb_uri)
    db = client[settings.mongodb_db_name]
    collection = db[settings.mongodb_collection_name]

    suffix = uuid.uuid4().hex[:8]
    user_a = f"test-user-a-{suffix}"
    user_b = f"test-user-b-{suffix}"

    # Create two synthetic 1536-dim orthogonal vectors
    # Vector A: [1.0, 0.0, ..., 0.0]
    # Vector B: [0.0, 1.0, ..., 0.0]
    dim = settings.gemini_embedding_dims
    vec_a = [0.0] * dim
    vec_a[0] = 1.0

    vec_b = [0.0] * dim
    vec_b[1] = 1.0

    doc_a_id = f"doc_a_{suffix}"
    doc_b_id = f"doc_b_{suffix}"

    doc_a = {
        "_id": doc_a_id,
        "embedding": vec_a,
        "text": "User A prefers TypeScript examples for frontend projects.",
        "payload": {
            "user_id": user_a,
            "data": "User A prefers TypeScript examples for frontend projects.",
        },
    }
    doc_b = {
        "_id": doc_b_id,
        "embedding": vec_b,
        "text": "User B prefers Python examples for frontend projects.",
        "payload": {
            "user_id": user_b,
            "data": "User B prefers Python examples for frontend projects.",
        },
    }

    report: dict[str, object] = {
        "user_a": user_a,
        "user_b": user_b,
        "index_name": settings.mongodb_vector_index_name,
    }

    try:
        # Insert docs
        collection.insert_many([doc_a, doc_b])

        # Define query pipelines
        pipeline_a = [
            {
                "$vectorSearch": {
                    "index": settings.mongodb_vector_index_name,
                    "path": "embedding",
                    "queryVector": vec_a,
                    "numCandidates": 10,
                    "limit": 5,
                    "filter": {"payload.user_id": {"$eq": user_a}},
                }
            },
            {"$project": {"_id": 1, "text": 1, "user_id": "$payload.user_id"}},
        ]

        # Atlas Vector Search indexes newly inserted documents asynchronously (1-5s).
        # Poll until the index has ingested the new documents.
        results_a = []
        for _ in range(15):
            results_a = list(collection.aggregate(pipeline_a))
            if any(r["_id"] == doc_a_id for r in results_a):
                break
            time.sleep(1)

        # Query as User B using vector A (even querying with A's vector, B should NEVER get A's doc)
        pipeline_b_with_vec_a = [
            {
                "$vectorSearch": {
                    "index": settings.mongodb_vector_index_name,
                    "path": "embedding",
                    "queryVector": vec_a,
                    "numCandidates": 10,
                    "limit": 5,
                    "filter": {"payload.user_id": {"$eq": user_b}},
                }
            },
            {"$project": {"_id": 1, "text": 1, "user_id": "$payload.user_id"}},
        ]
        results_b_with_vec_a = list(collection.aggregate(pipeline_b_with_vec_a))

        # Query as User B using vector B
        pipeline_b = [
            {
                "$vectorSearch": {
                    "index": settings.mongodb_vector_index_name,
                    "path": "embedding",
                    "queryVector": vec_b,
                    "numCandidates": 10,
                    "limit": 5,
                    "filter": {"payload.user_id": {"$eq": user_b}},
                }
            },
            {"$project": {"_id": 1, "text": 1, "user_id": "$payload.user_id"}},
        ]
        results_b = []
        for _ in range(15):
            results_b = list(collection.aggregate(pipeline_b))
            if any(r["_id"] == doc_b_id for r in results_b):
                break
            time.sleep(1)

        # Assertions
        assert any(r["_id"] == doc_a_id for r in results_a), "User A query failed to return doc A"
        assert not any(r["_id"] == doc_b_id for r in results_a), "User A received User B's document!"

        assert not any(r["_id"] == doc_a_id for r in results_b_with_vec_a), "User B received User A's document!"

        assert any(r["_id"] == doc_b_id for r in results_b), "User B query failed to return doc B"
        assert not any(r["_id"] == doc_a_id for r in results_b), "User B received User A's document!"

        report["query_a_returned_user_a_doc"] = True
        report["query_b_blocked_user_a_doc"] = True
        report["query_b_returned_user_b_doc"] = True
        report["isolation_verified"] = True
        report["ok"] = True
        return report

    finally:
        collection.delete_many({"_id": {"$in": [doc_a_id, doc_b_id]}})
        client.close()


if __name__ == "__main__":
    result = test_atlas_vector_isolation()
    print(json.dumps(result, indent=2))
