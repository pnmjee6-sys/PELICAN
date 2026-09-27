from __future__ import annotations

import argparse
import json
import time

from scoped_mongodb import IDENTITY_FIELDS, ScopedMongoDB
from settings import load_settings


def field_paths(index: dict) -> set[str]:
    definition = index.get("latestDefinition") or index.get("definition") or {}
    return {field.get("path") for field in definition.get("fields", []) if field.get("path")}


def verify(wait_seconds: int) -> dict[str, object]:
    settings = load_settings(require_gemini=False)
    store = ScopedMongoDB(
        db_name=settings.mongodb_db_name,
        collection_name=settings.mongodb_collection_name,
        embedding_model_dims=settings.gemini_embedding_dims,
        mongo_uri=settings.mongodb_uri,
    )
    deadline = time.monotonic() + wait_seconds
    index: dict = {}
    while True:
        matches = list(store.collection.list_search_indexes(name=store.index_name))
        index = matches[0] if matches else {}
        if index.get("status") == "READY" and index.get("queryable", True):
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(5)

    required = {"embedding", *(f"payload.{field}" for field in IDENTITY_FIELDS)}
    paths = field_paths(index)
    result = {
        "ok": index.get("status") == "READY" and index.get("queryable", True) and required <= paths,
        "name": store.index_name,
        "status": index.get("status", "MISSING"),
        "queryable": index.get("queryable"),
        "required_paths": sorted(required),
        "actual_paths": sorted(paths),
    }
    store.client.close()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait", type=int, default=0, help="Seconds to wait for Atlas index readiness")
    args = parser.parse_args()
    report = verify(args.wait)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["ok"] else 1)
