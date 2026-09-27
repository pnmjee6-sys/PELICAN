"""Destructive-on-test-data end-to-end proof for Gemini + Mem0 + MongoDB Atlas."""

from __future__ import annotations

import json
import time
import uuid

from memory_manager import MemoryManager


def memories(result: dict) -> list[dict]:
    return result.get("results", [])


def memory_texts(result: dict) -> list[str]:
    return [str(item.get("memory", "")) for item in memories(result)]


def run_proof() -> dict[str, object]:
    suffix = uuid.uuid4().hex[:10]
    alice = f"gate-alice-{suffix}"
    bob = f"gate-bob-{suffix}"
    manager = MemoryManager()
    report: dict[str, object] = {"users": [alice, bob]}

    try:
        alice_add = manager.add("My preferred database is PostgreSQL.", alice)
        bob_add = manager.add("My preferred database is MongoDB.", bob)
        alice_ids = [item["id"] for item in memories(alice_add) if item.get("id")]
        bob_ids = [item["id"] for item in memories(bob_add) if item.get("id")]
        assert alice_ids and bob_ids, "Gemini did not extract a memory for both users"

        collection = manager.memory.vector_store.collection
        alice_doc = collection.find_one({"_id": alice_ids[0]})
        assert alice_doc and len(alice_doc.get("embedding", [])) == manager.settings.gemini_embedding_dims
        report["stored_embedding_dimensions"] = len(alice_doc["embedding"])

        question = "Which data store do I like to use?"
        alice_search: dict = {}
        bob_search: dict = {}
        for _ in range(15):
            alice_search = manager.search(question, alice)
            bob_search = manager.search(question, bob)
            if memory_texts(alice_search) and memory_texts(bob_search):
                break
            time.sleep(1)
        alice_text = " ".join(memory_texts(alice_search)).lower()
        bob_text = " ".join(memory_texts(bob_search)).lower()
        assert "postgres" in alice_text and "mongodb" not in alice_text
        assert "mongodb" in bob_text and "postgres" not in bob_text
        report["semantic_retrieval"] = {"alice": memory_texts(alice_search), "bob": memory_texts(bob_search)}
        report["user_isolation"] = True

        try:
            manager.update(bob_ids[0], alice, text="Alice should never be allowed to write this")
            raise AssertionError("Cross-user update unexpectedly succeeded")
        except PermissionError:
            report["cross_user_update_blocked"] = True

        manager.update(alice_ids[0], alice, text="My preferred database is SQLite.")
        history = manager.history(alice_ids[0], alice)
        events = [entry.get("event") for entry in history]
        assert "ADD" in events and "UPDATE" in events
        report["history_events"] = events

        manager.delete(alice_ids[0], alice)
        assert not manager.search(question, alice).get("results")
        assert "mongodb" in " ".join(memory_texts(manager.search(question, bob))).lower()
        report["delete_behavior"] = True
        report["ok"] = True
        return report
    finally:
        manager.delete_all(alice)
        manager.delete_all(bob)


if __name__ == "__main__":
    proof = run_proof()
    print(json.dumps(proof, indent=2))
