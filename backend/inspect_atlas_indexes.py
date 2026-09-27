"""Read-only Atlas Search index inventory; never creates or drops an index."""

from __future__ import annotations

import json

from pymongo import MongoClient

from settings import load_settings


def inspect() -> dict[str, object]:
    settings = load_settings(require_gemini=False)
    client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
    try:
        collection = client[settings.mongodb_db_name][settings.mongodb_collection_name]
        indexes = []
        for index in collection.list_search_indexes():
            definition = index.get("latestDefinition") or index.get("definition") or {}
            fields = definition.get("fields") or []
            indexes.append({
                "name": index.get("name"),
                "status": index.get("status"),
                "queryable": index.get("queryable"),
                "fields": [field.get("path") for field in fields if field.get("path")],
                "dimensions": next((field.get("numDimensions") for field in fields if field.get("path") == "embedding"), None),
                "similarity": next((field.get("similarity") for field in fields if field.get("path") == "embedding"), None),
            })
        target = next((item for item in indexes if item["name"] == settings.mongodb_vector_index_name), None)
        required_fields = {"embedding", "payload.user_id", "payload.agent_id", "payload.run_id"}
        valid = bool(
            target
            and target["status"] == "READY"
            and target["queryable"] is not False
            and target["dimensions"] == settings.embedding_dims
            and target["similarity"] == "cosine"
            and required_fields.issubset(set(target["fields"]))
        )
        return {"target_name": settings.mongodb_vector_index_name, "target_valid": valid, "index_count": len(indexes), "indexes": indexes}
    finally:
        client.close()


if __name__ == "__main__":
    try:
        report = inspect()
        print(json.dumps(report, indent=2))
        raise SystemExit(0 if report["target_valid"] else 1)
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__, "target_valid": False}))
        raise SystemExit(2)
