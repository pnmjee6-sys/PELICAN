#!/usr/bin/env python3
"""
Context Passport - Version 1 Complete Verification Script.

Executes all 6 required Version 1 gates:
1. Manually supplied test message creates a searchable fact.
2. Differently worded search finds it.
3. Three observations across two chats create one evidence-backed preference.
4. One observation does not.
5. A fake credential is skipped.
6. Two accounts cannot see each other's memories.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(backend_dir))

# Set test environment to permit test-bearer authentication in verification suite
os.environ["APP_ENV"] = "test"
os.environ["ALLOW_TEST_AUTH"] = "true"

from starlette.testclient import TestClient
from server import app
from memory_manager import MemoryManager
from preference_engine import PreferenceEngine


def run_all_v1_gates() -> dict[str, object]:
    report: dict[str, object] = {
        "version": "1.0.0",
        "description": "Version 1 — Working memory and learning brain",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gates": {},
    }

    client = TestClient(app)
    mem_mgr = MemoryManager()
    pref_engine = PreferenceEngine()

    suffix = uuid.uuid4().hex[:8]
    alice_id = f"v1-live-alice-{suffix}"
    bob_id = f"v1-live-bob-{suffix}"

    def auth(uid: str) -> dict[str, str]:
        return {"Authorization": f"Bearer test-bearer-{uid}"}

    try:
        # -------------------------------------------------------------
        # Gate 1 & Gate 2: Searchable fact and different wording search
        # -------------------------------------------------------------
        print("\n[+] Running Gate 1 & Gate 2: Fact Ingestion and Semantic Retrieval...")
        ingest_res = client.post(
            "/api/v1/messages/ingest",
            headers=auth(alice_id),
            json={
                "conversation_id": f"conv-g1-{suffix}",
                "text": "For frontend styling, I prefer using Tailwind CSS with dark mode enabled.",
                "role": "user",
            },
        )
        assert ingest_res.status_code == 200, f"Ingest failed: {ingest_res.text}"
        ingest_data = ingest_res.json()
        assert ingest_data["status"] == "processed"
        assert len(ingest_data["facts_extracted"]) >= 1

        time.sleep(1.0)

        query_res = client.post(
            "/api/v1/memories/query",
            headers=auth(alice_id),
            json={
                "query": "Which UI design framework and theme do I like to build websites with?",
                "max_general": 3,
            },
        )
        assert query_res.status_code == 200, f"Query failed: {query_res.text}"
        retrieved = query_res.json().get("general_memories", [])
        all_text = " ".join(item.get("text", "") for item in retrieved).lower()
        gate1_ok = len(retrieved) >= 1
        gate2_ok = "tailwind" in all_text

        report["gates"]["gate_1_fact_created"] = {
            "ok": gate1_ok,
            "fact": ingest_data["facts_extracted"][0],
        }
        report["gates"]["gate_2_different_wording_retrieval"] = {
            "ok": gate2_ok,
            "query": "Which UI design framework and theme do I like to build websites with?",
            "retrieved_count": len(retrieved),
            "retrieved_snippets": [item.get("text") for item in retrieved],
        }
        print(f"    - Gate 1 (Fact Created): {'PASS' if gate1_ok else 'FAIL'}")
        print(f"    - Gate 2 (Different Wording Retrieval): {'PASS' if gate2_ok else 'FAIL'}")

        # -------------------------------------------------------------
        # Gate 4: Single observation does NOT create preference
        # -------------------------------------------------------------
        print("\n[+] Running Gate 4: Negative Preference Promotion Boundary...")
        bob_chat = f"bob-chat-{suffix}"
        bob_resp_1 = client.post(
            "/api/v1/messages/ingest",
            headers=auth(bob_id),
            json={
                "conversation_id": bob_chat,
                "text": "Please explain step by step how to configure a reverse proxy.",
                "role": "user",
            },
        )
        assert bob_resp_1.status_code == 200
        bob_data_1 = bob_resp_1.json()
        bob_promoted_immediately = bob_data_1.get("preference_promoted", True)

        bob_resp_2 = client.post(
            "/api/v1/messages/ingest",
            headers=auth(bob_id),
            json={
                "conversation_id": bob_chat,
                "text": "Can you walk me through this step-by-step with Nginx?",
                "role": "user",
            },
        )
        bob_promoted_same_chat = bob_resp_2.json().get("preference_promoted", True)
        gate4_ok = (not bob_promoted_immediately) and (not bob_promoted_same_chat)

        report["gates"]["gate_4_single_or_same_chat_not_promoted"] = {
            "ok": gate4_ok,
            "promoted_after_1_obs": bob_promoted_immediately,
            "promoted_after_2_obs_same_chat": bob_promoted_same_chat,
        }
        print(f"    - Gate 4 (Negative Boundary: 1 Obs does NOT Promote): {'PASS' if gate4_ok else 'FAIL'}")

        # -------------------------------------------------------------
        # Gate 3: Three observations across two chats DO promote
        # -------------------------------------------------------------
        print("\n[+] Running Gate 3: Explanation-Style Preference Promotion...")
        chat_a = f"alice-chat-A-{suffix}"
        chat_b = f"alice-chat-B-{suffix}"

        # Obs 1 in Chat A
        client.post(
            "/api/v1/messages/ingest",
            headers=auth(alice_id),
            json={"conversation_id": chat_a, "text": "Please explain step by step how Redis works.", "role": "user"},
        )
        # Obs 2 in Chat A
        client.post(
            "/api/v1/messages/ingest",
            headers=auth(alice_id),
            json={"conversation_id": chat_a, "text": "Break this down step by step for me.", "role": "user"},
        )
        # Obs 3 in Chat B (second distinct conversation!)
        obs3_res = client.post(
            "/api/v1/messages/ingest",
            headers=auth(alice_id),
            json={"conversation_id": chat_b, "text": "Walk me through step by step how to deploy this.", "role": "user"},
        )
        obs3_data = obs3_res.json()
        promoted_list = obs3_data.get("promoted_preferences", [])
        gate3_ok = (
            obs3_data.get("preference_promoted") is True
            and len(promoted_list) >= 1
            and promoted_list[0].get("evidence_count", 0) >= 3
            and promoted_list[0].get("conversation_count", 0) >= 2
        )

        report["gates"]["gate_3_three_obs_two_chats_promotes"] = {
            "ok": gate3_ok,
            "promoted_preference": promoted_list[0] if promoted_list else None,
        }
        print(f"    - Gate 3 (3 Obs Across 2 Chats Promotes Preference): {'PASS' if gate3_ok else 'FAIL'}")

        # -------------------------------------------------------------
        # Gate 5: Fake credential is skipped
        # -------------------------------------------------------------
        print("\n[+] Running Gate 5: Secret Credential Screening...")
        secret_payload = "My secret key is AKIAIOSFODNN7EXAMPLE and api_key = 'abcdef1234567890abcdef1234567890'"
        sec_res = client.post(
            "/api/v1/messages/ingest",
            headers=auth(alice_id),
            json={"conversation_id": f"sec-chat-{suffix}", "text": secret_payload, "role": "user"},
        )
        sec_data = sec_res.json()
        gate5_ok = (
            sec_data.get("status") == "skipped"
            and sec_data.get("reason") == "secret_credential_screened"
        )
        report["gates"]["gate_5_credential_skipped"] = {
            "ok": gate5_ok,
            "status": sec_data.get("status"),
            "reason": sec_data.get("reason"),
        }
        print(f"    - Gate 5 (Fake Credential Screened and Skipped): {'PASS' if gate5_ok else 'FAIL'}")

        # -------------------------------------------------------------
        # Gate 6: Two accounts cannot see each other's memories
        # -------------------------------------------------------------
        print("\n[+] Running Gate 6: Multi-Tenant Vector Isolation...")
        iso_user_a = f"iso-a-{suffix}"
        iso_user_b = f"iso-b-{suffix}"

        client.post(
            "/api/v1/messages/ingest",
            headers=auth(iso_user_a),
            json={
                "conversation_id": "iso-chat-a",
                "text": "My confidential project codename is Project FalconOmega.",
                "role": "user",
            },
        )
        client.post(
            "/api/v1/messages/ingest",
            headers=auth(iso_user_b),
            json={
                "conversation_id": "iso-chat-b",
                "text": "My team is currently working on Project DolphinBeta.",
                "role": "user",
            },
        )

        time.sleep(1.0)

        # Bob queries for Alice's codename
        bob_probe = client.post(
            "/api/v1/memories/query",
            headers=auth(iso_user_b),
            json={"query": "Tell me about FalconOmega and confidential project codenames."},
        )
        bob_items = (
            bob_probe.json().get("general_memories", [])
            + bob_probe.json().get("sensitive_memories", [])
        )
        bob_seen = " ".join(item.get("text", "") for item in bob_items).lower()

        # Alice queries for Alice's codename
        alice_probe = client.post(
            "/api/v1/memories/query",
            headers=auth(iso_user_a),
            json={"query": "What is my confidential project codename?"},
        )
        alice_items = (
            alice_probe.json().get("general_memories", [])
            + alice_probe.json().get("sensitive_memories", [])
        )
        alice_seen = " ".join(item.get("text", "") for item in alice_items).lower()

        gate6_ok = ("falconomega" not in bob_seen) and ("falconomega" in alice_seen)
        report["gates"]["gate_6_tenant_isolation"] = {
            "ok": gate6_ok,
            "bob_leak_prevented": "falconomega" not in bob_seen,
            "alice_retrieval_success": "falconomega" in alice_seen,
        }
        print(f"    - Gate 6 (Two Accounts Cannot See Each Other's Memories): {'PASS' if gate6_ok else 'FAIL'}")

    finally:
        # Cleanup
        mem_mgr.delete_all(alice_id)
        mem_mgr.delete_all(bob_id)
        mem_mgr.delete_all(f"iso-a-{suffix}")
        mem_mgr.delete_all(f"iso-b-{suffix}")
        pref_engine.clear_user_data(alice_id)
        pref_engine.clear_user_data(bob_id)

    all_passed = all(g.get("ok") for g in report["gates"].values())
    report["all_gates_passed"] = all_passed
    return report


if __name__ == "__main__":
    result = run_all_v1_gates()
    print("\n" + "=" * 60)
    print("VERSION 1 VERIFICATION REPORT")
    print("=" * 60)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("all_gates_passed") else 1)
