"""Version 1 Verification Gates Suite.

Tests the 6 mandatory Version 1 completion criteria:
1. Manually supplied test messages create a searchable fact.
2. Differently worded search finds it.
3. Three observations across two chats create one evidence-backed preference.
4. One observation does not.
5. A fake credential is skipped.
6. Two accounts cannot see each other's memories.
"""

from __future__ import annotations

import os
import time
import uuid
import pytest
from starlette.testclient import TestClient

os.environ["APP_ENV"] = "test"
os.environ["ALLOW_TEST_AUTH"] = "true"

pytestmark = pytest.mark.integration

from server import app
from memory_manager import MemoryManager
from preference_engine import PreferenceEngine


@pytest.fixture(scope="module")
def v1_client():
    with TestClient(app) as client:
        yield client


def make_test_user(prefix: str) -> str:
    return f"v1-{prefix}-{uuid.uuid4().hex[:8]}"


def cleanup_user(user_id: str):
    try:
        mem = MemoryManager()
        pref = PreferenceEngine()
        mem.delete_all(user_id)
        pref.clear_user_data(user_id)
    except Exception:
        pass


def auth_header(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer test-bearer-{user_id}"}


def test_gate_1_and_2_create_and_find_searchable_fact(v1_client):
    """
    Gate 1: Manually supplied test message creates a searchable fact.
    Gate 2: Differently worded search finds it.
    """
    user_id = make_test_user("alice-search")
    headers = auth_header(user_id)

    try:
        # 1. Manually supplied test message
        ingest_resp = v1_client.post(
            "/api/v1/messages/ingest",
            headers=headers,
            json={
                "conversation_id": "conv-general-01",
                "text": "For frontend styling, I prefer using Tailwind CSS with dark mode enabled.",
                "role": "user",
                "is_extension_context": False,
            },
        )
        assert ingest_resp.status_code == 200, ingest_resp.text
        data = ingest_resp.json()
        assert data["status"] == "processed"
        assert len(data["facts_extracted"]) >= 1
        created_fact = data["facts_extracted"][0]
        assert created_fact.get("classification") == "general"

        time.sleep(1.0)

        # 2. Differently worded search query
        query_resp = v1_client.post(
            "/api/v1/memories/query",
            headers=headers,
            json={
                "query": "Which UI design framework and theme do I like to build websites with?",
                "max_general": 3,
            },
        )
        assert query_resp.status_code == 200, query_resp.text
        q_data = query_resp.json()
        general = q_data.get("general_memories", [])
        assert len(general) >= 1, "Expected differently worded search to find Tailwind CSS fact"
        retrieved_texts = " ".join(item.get("text", "") for item in general).lower()
        assert "tailwind" in retrieved_texts
    finally:
        cleanup_user(user_id)


def test_gate_3_and_4_explanation_preference_engine(v1_client):
    """
    Gate 3: Three observations across two chats create one evidence-backed preference.
    Gate 4: One observation does not.
    """
    alice_id = make_test_user("alice-pref")
    bob_id = make_test_user("bob-pref")

    try:
        # --- GATE 4 PROOF: Single observation on Bob does NOT create preference ---
        bob_headers = auth_header(bob_id)
        bob_resp_1 = v1_client.post(
            "/api/v1/messages/ingest",
            headers=bob_headers,
            json={
                "conversation_id": "bob-chat-1",
                "text": "Please explain step by step how to configure a reverse proxy.",
                "role": "user",
            },
        )
        assert bob_resp_1.status_code == 200
        bob_data_1 = bob_resp_1.json()
        assert bob_data_1["status"] == "processed"
        assert len(bob_data_1["observations_recorded"]) == 1
        # MUST NOT be promoted on 1 observation!
        assert bob_data_1["preference_promoted"] is False
        assert bob_data_1["promoted_preferences"] == []

        # Verify Bob has 0 promoted preferences
        bob_pref_resp = v1_client.get("/api/v1/preferences", headers=bob_headers)
        assert bob_pref_resp.status_code == 200
        assert len(bob_pref_resp.json()) == 0

        # Even a second observation in the SAME chat does NOT promote (need >= 2 chats)
        bob_resp_2 = v1_client.post(
            "/api/v1/messages/ingest",
            headers=bob_headers,
            json={
                "conversation_id": "bob-chat-1",
                "text": "Can you walk me through this step-by-step with Nginx?",
                "role": "user",
            },
        )
        assert bob_resp_2.status_code == 200
        assert bob_resp_2.json()["preference_promoted"] is False

        # --- GATE 3 PROOF: Three observations across two chats on Alice DO create preference ---
        alice_headers = auth_header(alice_id)

        # Observation 1 (Chat A)
        obs_1 = v1_client.post(
            "/api/v1/messages/ingest",
            headers=alice_headers,
            json={
                "conversation_id": "alice-chat-A",
                "text": "Please explain step by step how Redis caching works.",
                "role": "user",
            },
        )
        assert obs_1.json()["preference_promoted"] is False

        # Observation 2 (Chat A)
        obs_2 = v1_client.post(
            "/api/v1/messages/ingest",
            headers=alice_headers,
            json={
                "conversation_id": "alice-chat-A",
                "text": "Break this down step by step so I understand each phase.",
                "role": "user",
            },
        )
        assert obs_2.json()["preference_promoted"] is False

        # Observation 3 (Chat B - 2nd distinct conversation!)
        obs_3 = v1_client.post(
            "/api/v1/messages/ingest",
            headers=alice_headers,
            json={
                "conversation_id": "alice-chat-B",
                "text": "Can you provide step-by-step instructions for deploying to Kubernetes?",
                "role": "user",
            },
        )
        obs_3_data = obs_3.json()
        # Now rule is met: 3 observations across 2 conversations!
        assert obs_3_data["preference_promoted"] is True
        promoted = obs_3_data["promoted_preferences"]
        assert len(promoted) == 1
        pref = promoted[0]
        assert pref["preference_key"] == "step_by_step"
        assert "step-by-step" in pref["preference_text"].lower()
        assert pref["evidence_count"] >= 3
        assert pref["conversation_count"] >= 2
        assert len(pref["evidence_ids"]) >= 3
        assert "alice-chat-A" in pref["conversation_ids"]
        assert "alice-chat-B" in pref["conversation_ids"]

        # Verify querying preferences returns it
        alice_pref_resp = v1_client.get("/api/v1/preferences", headers=alice_headers)
        assert alice_pref_resp.status_code == 200
        assert len(alice_pref_resp.json()) == 1
    finally:
        cleanup_user(alice_id)
        cleanup_user(bob_id)


def test_gate_5_fake_credential_skipped(v1_client):
    """
    Gate 5: A fake credential is skipped.
    """
    user_id = make_test_user("alice-secret")
    headers = auth_header(user_id)

    try:
        # Ingest message containing recognizable credentials
        secret_text = "Here is my AWS key: AKIAIOSFODNN7EXAMPLE and secret key api_key = 'abcdef1234567890abcdef1234567890'"
        resp = v1_client.post(
            "/api/v1/messages/ingest",
            headers=headers,
            json={
                "conversation_id": "conv-secret-test",
                "text": secret_text,
                "role": "user",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "skipped"
        assert data["reason"] == "secret_credential_screened"

        # Confirm it was not stored in memories
        memories_resp = v1_client.get("/api/v1/memories", headers=headers)
        all_texts = " ".join(m.get("text", "") for m in memories_resp.json())
        assert "AKIAIOSFODNN7EXAMPLE" not in all_texts
    finally:
        cleanup_user(user_id)


def test_gate_6_two_accounts_cannot_see_each_others_memories(v1_client):
    """
    Gate 6: Two accounts cannot see each other's memories.
    """
    alice_id = make_test_user("alice-iso")
    bob_id = make_test_user("bob-iso")
    alice_headers = auth_header(alice_id)
    bob_headers = auth_header(bob_id)

    try:
        # Alice creates a private memory
        v1_client.post(
            "/api/v1/messages/ingest",
            headers=alice_headers,
            json={
                "conversation_id": "alice-secret-project",
                "text": "My confidential project codename is Project FalconOmega.",
                "role": "user",
            },
        )

        # Bob creates a different memory
        v1_client.post(
            "/api/v1/messages/ingest",
            headers=bob_headers,
            json={
                "conversation_id": "bob-project",
                "text": "My team is currently working on Project DolphinBeta.",
                "role": "user",
            },
        )

        time.sleep(1.0)

        # Bob queries searching specifically for FalconOmega
        bob_query = v1_client.post(
            "/api/v1/memories/query",
            headers=bob_headers,
            json={"query": "Tell me about FalconOmega and confidential project codenames."},
        )
        assert bob_query.status_code == 200
        bob_results = (
            bob_query.json().get("general_memories", [])
            + bob_query.json().get("sensitive_memories", [])
        )
        bob_texts = " ".join(item.get("text", "") for item in bob_results).lower()
        assert "falconomega" not in bob_texts, "Security violation: Bob saw Alice's memory!"

        # Alice queries for FalconOmega -> she CAN see it
        alice_query = v1_client.post(
            "/api/v1/memories/query",
            headers=alice_headers,
            json={"query": "What is my confidential project codename?"},
        )
        assert alice_query.status_code == 200
        alice_results = (
            alice_query.json().get("general_memories", [])
            + alice_query.json().get("sensitive_memories", [])
        )
        alice_texts = " ".join(item.get("text", "") for item in alice_results).lower()
        assert "falconomega" in alice_texts
    finally:
        cleanup_user(alice_id)
        cleanup_user(bob_id)


def test_reject_injected_memory_blocks(v1_client):
    """
    Item 2: Reject injected memory blocks before saving them.
    """
    user_id = make_test_user("alice-inject")
    headers = auth_header(user_id)

    try:
        # Injected memory block marked by flag
        resp1 = v1_client.post(
            "/api/v1/messages/ingest",
            headers=headers,
            json={
                "conversation_id": "conv-inject-01",
                "text": "What is Docker? [Context Passport Memory: Likes TypeScript]",
                "role": "user",
                "is_extension_context": True,
            },
        )
        assert resp1.status_code == 200
        assert resp1.json()["status"] == "skipped"
        assert resp1.json()["reason"] == "extension_context_ignored"

        # Injected memory block containing marker text even if is_extension_context is False
        resp2 = v1_client.post(
            "/api/v1/messages/ingest",
            headers=headers,
            json={
                "conversation_id": "conv-inject-02",
                "text": "<!-- context-passport memory: User prefers dark mode --> Please help me fix CSS.",
                "role": "user",
                "is_extension_context": False,
            },
        )
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "skipped"
        assert resp2.json()["reason"] == "extension_context_ignored"

        # Confirm nothing was saved
        memories = v1_client.get("/api/v1/memories", headers=headers).json()
        assert len(memories) == 0
    finally:
        cleanup_user(user_id)


def test_allergy_and_uncertainty_classified_sensitive(v1_client):
    """
    Item 5: Treat allergy and uncertain as approval-required (sensitive), never automatic.
    """
    user_id = make_test_user("alice-sensitive")
    headers = auth_header(user_id)

    try:
        # Allergy fact
        resp_allergy = v1_client.post(
            "/api/v1/messages/ingest",
            headers=headers,
            json={
                "conversation_id": "conv-allergy",
                "text": "I have a severe peanut allergy and always carry an EpiPen.",
                "role": "user",
            },
        )
        assert resp_allergy.status_code == 200
        facts = resp_allergy.json().get("facts_extracted", [])
        assert len(facts) >= 1
        assert facts[0]["classification"] == "sensitive", "Allergy must be classified as sensitive"

        # Uncertain / speculative fact
        resp_uncertain = v1_client.post(
            "/api/v1/messages/ingest",
            headers=headers,
            json={
                "conversation_id": "conv-uncertain",
                "text": "I am uncertain and not sure if I will move to Chicago next year.",
                "role": "user",
            },
        )
        assert resp_uncertain.status_code == 200
        facts_u = resp_uncertain.json().get("facts_extracted", [])
        # Jev may reject speculative facts entirely. Any fact it keeps must
        # retain the source message's sensitive classification.
        assert all(fact["classification"] == "sensitive" for fact in facts_u)

        # Querying memories: both must appear in sensitive_memories (requiring approval), NEVER in general_memories
        query_res = v1_client.post(
            "/api/v1/memories/query",
            headers=headers,
            json={"query": "What are my health conditions, plans, or allergies?"},
        )
        assert query_res.status_code == 200
        q_data = query_res.json()
        assert len(q_data.get("general_memories", [])) == 0, "Allergy/uncertain must NEVER be automatic general memory"
        sensitive_items = q_data.get("sensitive_memories", [])
        assert len(sensitive_items) >= 1, "Expected allergy/uncertain in sensitive_memories"
        for s in sensitive_items:
            assert s["classification"] == "sensitive"
    finally:
        cleanup_user(user_id)


def test_request_size_limit_and_auth_bypass_guard(v1_client, monkeypatch):
    """
    Item 1: Test-bearer authentication bypass strictly gated.
    Item 7: Request-size limits enforced (HTTP 413).
    """
    user_id = make_test_user("alice-guards")

    # 1. Test-bearer bypass blocked in production/non-test environment
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ALLOW_TEST_AUTH", "false")
    resp_blocked = v1_client.get("/api/v1/memories", headers=auth_header(user_id))
    assert resp_blocked.status_code == 401
    assert "disabled" in resp_blocked.json()["detail"].lower()

    # Restore test env
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ALLOW_TEST_AUTH", "true")

    # 2. Request payload exceeds 64KB
    oversized_headers = {
        **auth_header(user_id),
        "Content-Length": "70000",
    }
    resp_large = v1_client.post(
        "/api/v1/messages/ingest",
        headers=oversized_headers,
        json={"conversation_id": "c1", "text": "x" * 100, "role": "user"},
    )
    assert resp_large.status_code == 413
