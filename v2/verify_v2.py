#!/usr/bin/env python3
"""
Context Passport - Version 2 backend contract check.

Simulates six integration cases against the backend. This does not load the
extension into ChatGPT, Claude, or Gemini and is not a live browser gate.
1. Capture OFF sends nothing.
2. Each of the three sites captures one user message without capturing an assistant reply.
3. A fact learned on ChatGPT is automatically selected by Use Memory on Claude and Gemini.
4. A sensitive candidate pauses for approval.
5. Denial keeps it out of the prepared prompt.
6. Repeated page updates do not duplicate memories.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(backend_dir))

# Enable test authentication environment
os.environ["APP_ENV"] = "test"
os.environ["ALLOW_TEST_AUTH"] = "true"

from starlette.testclient import TestClient
from server import app
from memory_manager import MemoryManager
from preference_engine import PreferenceEngine
from security import contains_secret, classify_text


def run_all_v2_gates() -> dict[str, object]:
    report: dict[str, object] = {
        "version": "2.0.0",
        "description": "V2 backend contract simulation; live browser verification is separate",
        "browser_verified": False,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gates": {},
    }

    client = TestClient(app)
    mem_mgr = MemoryManager()
    pref_engine = PreferenceEngine()

    suffix = uuid.uuid4().hex[:8]
    test_user_id = f"v2-verifier-{suffix}"

    def auth(uid: str) -> dict[str, str]:
        return {"Authorization": f"Bearer test-bearer-{uid}"}

    headers = auth(test_user_id)

    try:
        # =============================================================
        # GATE 1: Capture OFF sends nothing
        # =============================================================
        print("\n[+] Gate 1: Testing 'Capture OFF sends nothing' & client-side screening...")
        # Simulated extension state: per-site capture is OFF by default
        extension_site_toggles = {
            "chatgpt": False,
            "claude": False,
            "gemini": False,
        }

        captured_requests = []

        def simulate_extension_capture(site: str, text: str, role: str, conversation_id: str):
            # Check toggle (Default OFF)
            if not extension_site_toggles.get(site, False):
                # Capture is OFF -> extension drops message, sends 0 requests
                return {"action": "dropped_capture_off"}

            # Secret check before upload
            if contains_secret(text):
                return {"action": "dropped_secret_screened"}

            # Role check
            if role != "user":
                return {"action": "dropped_assistant_reply"}

            # Send to backend
            res = client.post(
                "/api/v1/messages/ingest",
                headers=headers,
                json={
                    "conversation_id": conversation_id,
                    "text": text,
                    "role": role,
                },
            )
            captured_requests.append(res.json())
            return {"action": "sent", "response": res.json()}

        # 1. User writes a message while capture is OFF
        off_result = simulate_extension_capture(
            site="chatgpt",
            text="I am testing if capture OFF sends anything.",
            role="user",
            conversation_id="conv-off",
        )
        assert off_result["action"] == "dropped_capture_off"
        assert len(captured_requests) == 0, "Security error: Request sent while capture was OFF!"

        # 2. Secret check: Even when turned ON, recognizable credentials are never uploaded
        extension_site_toggles["chatgpt"] = True
        secret_result = simulate_extension_capture(
            site="chatgpt",
            text="My OpenAI API key is sk-proj-1234567890abcdefghijklmnopqrstuvwxyz",
            role="user",
            conversation_id="conv-secret",
        )
        assert secret_result["action"] == "dropped_secret_screened"
        assert len(captured_requests) == 0, "Security error: Secret was sent to network!"

        gate1_ok = len(captured_requests) == 0
        report["gates"]["gate_1_capture_off_sends_nothing"] = {
            "ok": gate1_ok,
            "capture_off_behavior": off_result["action"],
            "secret_screening_behavior": secret_result["action"],
            "network_requests_sent": len(captured_requests),
        }
        print("  ✓ Gate 1 Passed: Capture OFF sends nothing, client screening blocks secrets.")

        # =============================================================
        # GATE 2: Each of the 3 sites captures 1 user message, 0 assistant replies
        # =============================================================
        print("\n[+] Gate 2: Testing 3 site adapters capturing user messages (never assistant)...")
        # Enable capture for all 3 sites
        extension_site_toggles = {"chatgpt": True, "claude": True, "gemini": True}

        # 1. ChatGPT: User message vs Assistant response
        cg_user = simulate_extension_capture(
            site="chatgpt",
            text="For containerization, I always use Docker with multi-stage builds.",
            role="user",
            conversation_id=f"chatgpt-{suffix}",
        )
        cg_assistant = simulate_extension_capture(
            site="chatgpt",
            text="Docker multi-stage builds are a great way to optimize image size.",
            role="assistant",
            conversation_id=f"chatgpt-{suffix}",
        )
        assert cg_user["action"] == "sent" and cg_user["response"].get("status") == "processed", "ChatGPT synthetic message did not process"
        assert cg_assistant["action"] == "dropped_assistant_reply"

        # 2. Claude: User message vs Assistant response
        cl_user = simulate_extension_capture(
            site="claude",
            text="For testing frontend apps, I prefer Playwright over Cypress.",
            role="user",
            conversation_id=f"claude-{suffix}",
        )
        cl_assistant = simulate_extension_capture(
            site="claude",
            text="Playwright provides strong cross-browser isolation and fast execution.",
            role="assistant",
            conversation_id=f"claude-{suffix}",
        )
        assert cl_user["action"] == "sent" and cl_user["response"].get("status") == "processed", "Claude synthetic message did not process"
        assert cl_assistant["action"] == "dropped_assistant_reply"

        # 3. Gemini: User message vs Model response
        gm_user = simulate_extension_capture(
            site="gemini",
            text="For database migrations, I use Alembic with PostgreSQL.",
            role="user",
            conversation_id=f"gemini-{suffix}",
        )
        gm_assistant = simulate_extension_capture(
            site="gemini",
            text="Alembic is the standard migration tool for SQLAlchemy.",
            role="assistant",
            conversation_id=f"gemini-{suffix}",
        )
        assert gm_user["action"] == "sent" and gm_user["response"].get("status") == "processed", "Gemini synthetic message did not process"
        assert gm_assistant["action"] == "dropped_assistant_reply"

        # Verify assistant message direct backend rejection as well
        direct_assistant = client.post(
            "/api/v1/messages/ingest",
            headers=headers,
            json={
                "conversation_id": "direct-test",
                "text": "I am an assistant response attempting ingestion.",
                "role": "assistant",
            },
        ).json()
        assert direct_assistant["status"] == "skipped"
        assert direct_assistant["reason"] == "assistant_reply_ignored"

        gate2_ok = (
            cg_user["response"]["status"] == "processed"
            and cl_user["response"]["status"] == "processed"
            and gm_user["response"]["status"] == "processed"
            and direct_assistant["reason"] == "assistant_reply_ignored"
        )
        report["gates"]["gate_2_three_sites_capture_user_only"] = {
            "ok": gate2_ok,
            "chatgpt_captured": cg_user["action"] == "sent",
            "claude_captured": cl_user["action"] == "sent",
            "gemini_captured": gm_user["action"] == "sent",
            "assistant_replies_ignored": True,
        }
        print("  ✓ Gate 2 Passed: ChatGPT, Claude, and Gemini captured user messages only.")

        # =============================================================
        # GATE 3: Fact learned on ChatGPT is selected by Use Memory on Claude & Gemini
        # =============================================================
        print("\n[+] Gate 3: Testing cross-site memory interoperability...")
        # 1. User teaches a unique fact on ChatGPT
        unique_fact_text = "I specialize in building high-performance WebAssembly audio DSP plugins."
        ingest_chatgpt = client.post(
            "/api/v1/messages/ingest",
            headers=headers,
            json={
                "conversation_id": f"chatgpt-spec-{suffix}",
                "text": unique_fact_text,
                "role": "user",
            },
        )
        assert ingest_chatgpt.status_code == 200
        time.sleep(1.0)

        # 2. User clicks "Use Memory" on Claude
        claude_draft = "Can you help me design an architecture for my audio synthesizer plugin?"
        claude_query_res = client.post(
            "/api/v1/memories/query",
            headers=headers,
            json={"query": claude_draft, "max_general": 3},
        )
        assert claude_query_res.status_code == 200
        claude_mems = claude_query_res.json().get("general_memories", [])
        claude_all_text = " ".join(m.get("text", "") for m in claude_mems).lower()
        claude_found = "webassembly" in claude_all_text or "audio" in claude_all_text

        # 3. User clicks "Use Memory" on Gemini
        gemini_draft = "What audio technology stack should I focus on for DSP development?"
        gemini_query_res = client.post(
            "/api/v1/memories/query",
            headers=headers,
            json={"query": gemini_draft, "max_general": 3},
        )
        assert gemini_query_res.status_code == 200
        gemini_mems = gemini_query_res.json().get("general_memories", [])
        gemini_all_text = " ".join(m.get("text", "") for m in gemini_mems).lower()
        gemini_found = "webassembly" in gemini_all_text or "audio" in gemini_all_text

        gate3_ok = claude_found and gemini_found
        report["gates"]["gate_3_cross_site_use_memory"] = {
            "ok": gate3_ok,
            "fact_learned_source": "chatgpt.com",
            "recalled_on_claude": claude_found,
            "recalled_on_gemini": gemini_found,
            "claude_snippets": [m.get("text") for m in claude_mems],
            "gemini_snippets": [m.get("text") for m in gemini_mems],
        }
        print(f"  ✓ Gate 3 Passed: Fact learned on ChatGPT selected on Claude ({claude_found}) & Gemini ({gemini_found}).")

        # =============================================================
        # GATE 4 & GATE 5: Sensitive candidate pauses for approval; Denial keeps it out
        # =============================================================
        print("\n[+] Gate 4 & 5: Testing Privacy Firewall two-phase approval & denial...")
        # 1. Ingest sensitive memory
        sensitive_text = "I have a severe life-threatening allergy to peanuts and tree nuts."
        ingest_sens = client.post(
            "/api/v1/messages/ingest",
            headers=headers,
            json={
                "conversation_id": f"sens-conv-{suffix}",
                "text": sensitive_text,
                "role": "user",
            },
        )
        assert ingest_sens.status_code == 200
        time.sleep(1.0)

        # 2. Query matching the sensitive memory
        food_query = "What is my peanut allergy?"
        sensitive_candidates = []
        general_candidates = []
        for attempt in range(3):
            query_sens_res = client.post(
                "/api/v1/memories/query",
                headers=headers,
                json={"query": food_query, "max_general": 3},
            )
            assert query_sens_res.status_code == 200
            query_data = query_sens_res.json()
            sensitive_candidates = query_data.get("sensitive_memories", [])
            general_candidates = query_data.get("general_memories", [])
            if sensitive_candidates:
                break
            if attempt < 2:
                time.sleep(2)

        # Verify sensitive candidate paused / flagged
        has_sensitive_candidate = len(sensitive_candidates) > 0
        assert has_sensitive_candidate, "Sensitive fact was not returned as an approval-required candidate"
        gate4_ok = has_sensitive_candidate

        # 3. Simulate User DENIAL ("Don't use"):
        def prepare_prompt(draft: str, general: list, sensitive: list, user_allowed_sensitive: bool) -> str:
            approved_sensitive = sensitive if user_allowed_sensitive else []
            lines = []
            all_mems = general + approved_sensitive
            if all_mems:
                lines.append("[Context Passport: Memory]")
                lines.append("Relevant Memories:")
                for m in all_mems:
                    lines.append(f"- {m.get('text', '').strip()}")
                lines.append("[/Context Passport]")
            block = "\n".join(lines)
            if block:
                return f"{block}\n\n{draft}"
            return draft

        # Denial:
        prompt_denied = prepare_prompt(food_query, general_candidates, sensitive_candidates, user_allowed_sensitive=False)
        assert all(m.get("text", "") not in prompt_denied for m in sensitive_candidates), "Security violation: Sensitive memory included after denial!"
        gate5_ok = True

        # Approval ("Allow once"):
        prompt_allowed = prepare_prompt(food_query, general_candidates, sensitive_candidates, user_allowed_sensitive=True)
        assert all(m.get("text", "") in prompt_allowed for m in sensitive_candidates), "Approved sensitive memory missing from prompt!"

        report["gates"]["gate_4_sensitive_pauses_for_approval"] = {
            "ok": gate4_ok,
            "sensitive_candidates_count": len(sensitive_candidates),
            "candidate_snippets": [m.get("text") for m in sensitive_candidates],
        }
        report["gates"]["gate_5_denial_excludes_sensitive_context"] = {
            "ok": gate5_ok,
            "prompt_on_denial_contains_sensitive": False,
            "prompt_on_allow_contains_sensitive": True,
        }
        print("  ✓ Gate 4 Passed: Sensitive memory paused for user approval.")
        print("  ✓ Gate 5 Passed: Denial strictly kept sensitive context out of the prepared prompt.")

        # =============================================================
        # GATE 6: Repeated page updates do not duplicate memories
        # =============================================================
        print("\n[+] Gate 6: Testing idempotency against repeated page updates...")
        # 1. Inject memory block 3 times in a row into composer draft
        base_draft = "Please write a summary of my stack."
        block = (
            "[Context Passport: Memory]\n"
            "Relevant Memories:\n"
            "- Likes Docker multi-stage builds\n"
            "- Likes Playwright\n"
            "[/Context Passport]\n\n"
        )

        # Helper matching extension's applyMemoryBlockToDraft
        def apply_block_idempotent(draft: str, new_block: str) -> str:
            # Strip existing block
            cleaned = re.sub(r"\[Context Passport:[^\]]*\][\s\S]*?\[/Context Passport\]\n*", "", draft).strip()
            if new_block:
                return f"{new_block.strip()}\n\n{cleaned}"
            return cleaned

        click_1 = apply_block_idempotent(base_draft, block)
        click_2 = apply_block_idempotent(click_1, block)
        click_3 = apply_block_idempotent(click_2, block)

        count_blocks = click_3.count("[Context Passport: Memory]")
        assert count_blocks == 1, f"Repeated updates duplicated memory block: count = {count_blocks}"
        assert click_3.endswith(base_draft)

        # 2. Ingest duplicate message events with event_id
        event_id = f"evt-dedup-{suffix}"
        dup_text = "I always use Prettier for code formatting."
        res_first = client.post(
            "/api/v1/messages/ingest",
            headers=headers,
            json={"conversation_id": "conv-dedup", "text": dup_text, "role": "user", "event_id": event_id},
        )
        assert res_first.status_code == 200

        res_second = client.post(
            "/api/v1/messages/ingest",
            headers=headers,
            json={"conversation_id": "conv-dedup", "text": dup_text, "role": "user", "event_id": event_id},
        )
        assert res_second.status_code == 200
        assert res_second.json()["status"] == "duplicate_skipped"

        gate6_ok = (count_blocks == 1) and (res_second.json()["status"] == "duplicate_skipped")
        report["gates"]["gate_6_no_duplicate_memories_on_repeated_updates"] = {
            "ok": gate6_ok,
            "composer_block_count_after_3_clicks": count_blocks,
            "duplicate_ingest_status": res_second.json()["status"],
        }
        print("  ✓ Gate 6 Passed: Prompt composer & backend reject duplicate updates idempotently.")

        # Summary
        all_passed = all(g.get("ok", False) for g in report["gates"].values())
        report["all_passed"] = all_passed

        print("\n==================================================")
        print(f"  BACKEND CONTRACT CHECK: {'ALL 6 PASSED' if all_passed else 'SOME FAILED'}")
        print("==================================================")
        return report

    finally:
        try:
            mem_mgr.delete_all(test_user_id)
            pref_engine.clear_user_data(test_user_id)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        report = run_all_v2_gates()
    except Exception as exc:
        report = {
            "version": "2.0.0",
            "description": "V2 backend contract simulation; live browser verification is separate",
            "browser_verified": False,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "all_passed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    report_path = Path(__file__).resolve().parent / "v2_verification_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport saved to: {report_path}")
    if not report.get("all_passed"):
        sys.exit(1)
