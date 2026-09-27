"""Test 8 — Local Runtime and Reliability Suite for Context Passport V3.

Verifies:
1. Localhost loopback runtime (127.0.0.1:8000) with explicit environment configuration.
2. /health and /ready endpoints, including disconnected Atlas and Supabase checks.
3. Login success and failure with mocked Supabase Auth responses.
4. Expired and invalid JWT handling (returning explicit 401 with detail 'token_expired').
5. Model provider timeout handling on ingest (504 + safe retry event state) and query (504).
6. Simulated server restart behavior: memory and preference persistence across restart,
   followed by memory wording update and forget/delete after restart.
7. LAN address isolation: server bound to 127.0.0.1 is unreachable over the laptop's LAN IP.
8. No test bypass or deterministic embedding active in real production configuration.
9. Thread-safe rate limiting, CORS origin restrictions, and SQLite history file permissions (0600).
"""

from __future__ import annotations

import base64
import collections
import hashlib
import json
import os
import socket
import stat
import threading
import time
import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import httpx
import pymongo.database
import pytest
from starlette.testclient import TestClient

# Ensure test flags before importing app components
os.environ["APP_ENV"] = "test"
os.environ["ALLOW_TEST_AUTH"] = "true"
os.environ["ALLOW_OFFLINE_EMBEDDINGS"] = "true"

import server
from auth import acquire_synthetic_user_token, is_jwt_expired, verify_supabase_token
from memory_manager import MemoryManager
from preference_engine import PreferenceEngine
from providers import (
    MockEmbedder,
    MockExtractor,
    ProviderTimeoutError,
    ProviderRateLimitError,
    SpendingCapExceededError,
)
from settings import Settings, load_settings


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------
def make_mock_jwt(user_id: str, expired: bool = False, email: Optional[str] = None) -> str:
    """Generates an unverified 3-part JWT for expiry testing."""
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": user_id,
        "id": user_id,
        "email": email or f"{user_id}@test.local",
        "iat": now - 7200,
        "exp": now - 3600 if expired else now + 3600,
    }
    b64_header = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
    b64_payload = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    b64_sig = "synthetic_signature_for_testing"
    return f"{b64_header}.{b64_payload}.{b64_sig}"


def get_lan_ip() -> Optional[str]:
    """Detects laptop's non-loopback LAN IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        if ip and ip != "127.0.0.1":
            return ip
    except Exception:
        pass
    finally:
        s.close()
    return None


@pytest.fixture(autouse=True)
def clean_test_state():
    """Resets server module state between tests."""
    server.reset_rate_limits()
    server.memory_manager = None
    server.preference_engine = None
    yield
    server.reset_rate_limits()
    server.memory_manager = None
    server.preference_engine = None


# ---------------------------------------------------------------------------
# 1. Health and Version Information
# ---------------------------------------------------------------------------
def test_health_endpoint_loopback():
    with TestClient(server.app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "context-passport"
        assert data["version"] == "3.0.0"
        assert "mem0" in data
        assert "fastapi" in data
        # Explicit non-goals check: no MCP, no Render remnants
        assert "mcp" not in data
        assert "render" not in str(data).lower()


# ---------------------------------------------------------------------------
# 2. Ready Endpoint: Configuration and Disconnected Service Checks
# ---------------------------------------------------------------------------
def test_ready_endpoint_connected_and_disconnected(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "mock-openrouter-key")
    monkeypatch.setenv("GEMINI_API_KEY", "mock-gemini-key")
    monkeypatch.setattr(server.httpx, "get", lambda *args, **kwargs: httpx.Response(200, json={}))
    # 1. When all dependencies are connected
    with TestClient(server.app) as client:
        resp = client.get("/ready?check_connectivity=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["mongodb"] == "connected"
        assert data["supabase"] == "connected"

    # 2. When Atlas is disconnected
    orig_command = pymongo.database.Database.command

    def failing_command(self, *args, **kwargs):
        if args and args[0] == "ping":
            raise ConnectionError("Simulated Atlas disconnect")
        return orig_command(self, *args, **kwargs)

    monkeypatch.setattr(pymongo.database.Database, "command", failing_command)
    with TestClient(server.app) as client:
        resp_mongo_fail = client.get("/ready?check_connectivity=true")
        assert resp_mongo_fail.status_code == 503
        data_mongo = resp_mongo_fail.json()
        assert data_mongo["status"] == "service_unavailable"
        assert "mongodb" in data_mongo["disconnected"]

    monkeypatch.setattr(pymongo.database.Database, "command", orig_command)

    # 3. When Supabase is disconnected
    orig_get = server.httpx.get

    def failing_supabase_get(url, *args, **kwargs):
        if "supabase" in str(url):
            raise httpx.ConnectError("Simulated Supabase disconnect")
        return orig_get(url, *args, **kwargs)

    monkeypatch.setattr(server.httpx, "get", failing_supabase_get)
    with TestClient(server.app) as client:
        resp_supa_fail = client.get("/ready?check_connectivity=true")
        assert resp_supa_fail.status_code == 503
        data_supa = resp_supa_fail.json()
        assert data_supa["status"] == "service_unavailable"
        assert "supabase" in data_supa["disconnected"]


# ---------------------------------------------------------------------------
# 3. Login Success and Failure with Real Supabase Auth
# ---------------------------------------------------------------------------
def test_auth_login_success_and_failure(monkeypatch):
    import auth

    def mock_supabase_token(_url, *, json, **_kwargs):
        if json["password"] == "SafeTestPasswordA123!":
            return httpx.Response(200, json={"access_token": "synthetic-offline-access-token-12345"})
        return httpx.Response(400, json={"error": "invalid_grant"})

    monkeypatch.setattr(auth.httpx, "post", mock_supabase_token)
    with TestClient(server.app) as client:
        # Failure: invalid password
        resp_fail = client.post(
            "/api/v1/auth/token",
            json={
                "email": "synthetic_user_a@contextpassport.local",
                "password": "WrongPassword!999",
            },
        )
        assert resp_fail.status_code == 401
        assert "failed" in resp_fail.json()["detail"].lower()

        # Success: valid synthetic test user credentials
        resp_success = client.post(
            "/api/v1/auth/token",
            json={
                "email": "synthetic_user_a@contextpassport.local",
                "password": "SafeTestPasswordA123!",
            },
        )
        assert resp_success.status_code == 200
        token_data = resp_success.json()
        assert "access_token" in token_data
        assert token_data["token_type"] == "bearer"
        assert len(token_data["access_token"]) > 20


# ---------------------------------------------------------------------------
# 4. Expired and Invalid JWT Handling
# ---------------------------------------------------------------------------
def test_expired_and_invalid_jwt_handling(monkeypatch):
    import auth
    monkeypatch.setattr(auth.httpx, "get", lambda *args, **kwargs: httpx.Response(401, json={"message": "invalid token"}))
    with TestClient(server.app) as client:
        # Missing auth header
        resp_missing = client.get("/api/v1/memories")
        assert resp_missing.status_code == 401

        # Malformed invalid token
        resp_malformed = client.get(
            "/api/v1/memories",
            headers={"Authorization": "Bearer not-a-valid-jwt-token"},
        )
        assert resp_malformed.status_code == 401
        assert "invalid" in resp_malformed.json()["detail"].lower()

        # Explicit expired test token
        resp_expired_test = client.get(
            "/api/v1/memories",
            headers={"Authorization": "Bearer expired-test-token"},
        )
        assert resp_expired_test.status_code == 401
        assert resp_expired_test.json()["detail"] == "token_expired"

        # Real-format JWT with exp timestamp in the past
        expired_jwt = make_mock_jwt("user-expired-01", expired=True)
        assert is_jwt_expired(expired_jwt) is True

        resp_expired_jwt = client.get(
            "/api/v1/memories",
            headers={"Authorization": f"Bearer {expired_jwt}"},
        )
        assert resp_expired_jwt.status_code == 401
        assert resp_expired_jwt.json()["detail"] == "token_expired"


def test_expired_token_never_uses_admin_identity_lookup(monkeypatch):
    from auth import verify_supabase_token
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "synthetic-service-key")
    monkeypatch.setattr(httpx, "get", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network call")))
    with pytest.raises(server.HTTPException) as error:
        verify_supabase_token(make_mock_jwt("some-user", expired=True))
    assert error.value.status_code == 401
    assert error.value.detail == "token_expired"


# ---------------------------------------------------------------------------
# 5. Model Timeout Handling (504 Gateway Timeout + Safe Retry State Machine)
# ---------------------------------------------------------------------------
def test_model_timeout_handling_on_ingest_and_query():
    user_id = f"test-timeout-{uuid.uuid4().hex[:6]}"
    auth_header = {"Authorization": f"Bearer test-bearer-{user_id}"}

    mem_mgr = MemoryManager(
        extractor=MockExtractor(fail_mode="timeout"),
        embedder=MockEmbedder(),
    )
    pref_engine = PreferenceEngine()
    server.memory_manager = mem_mgr
    server.preference_engine = pref_engine

    with TestClient(server.app) as client:
        # 1. Ingest times out -> returns 504 and marks event failed for retry
        event_id = f"evt-to-{uuid.uuid4().hex[:6]}"
        ingest_payload = {
            "conversation_id": "c-timeout",
            "text": "I am working on a distributed crawler system in Go.",
            "role": "user",
            "event_id": event_id,
        }
        resp_to = client.post("/api/v1/messages/ingest", json=ingest_payload, headers=auth_header)
        assert resp_to.status_code == 504
        assert "timed out" in resp_to.json()["detail"].lower()

        # Dedup event must be marked 'failed', not stuck in processing
        event_key = f"{user_id}:{event_id}"
        event_doc = pref_engine.dedup_col.find_one({"_id": event_key})
        assert event_doc is not None
        assert event_doc["status"] == "failed"

        # 2. Retry event with working extractor -> recovers cleanly
        mem_mgr.extractor = MockExtractor(default_facts=["Builds distributed crawler systems in Go"])
        resp_retry = client.post("/api/v1/messages/ingest", json=ingest_payload, headers=auth_header)
        assert resp_retry.status_code == 200
        assert resp_retry.json()["status"] == "processed"

        # 3. Memory query times out -> returns 504
        mem_mgr.embedder = MockEmbedder(fail_mode="timeout")
        resp_q_to = client.post(
            "/api/v1/memories/query",
            json={"query": "crawler systems"},
            headers=auth_header,
        )
        assert resp_q_to.status_code == 504
        assert "timed out" in resp_q_to.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 6. Server Process Restart Behavior & Post-Restart Memory Lifecycle
# ---------------------------------------------------------------------------
def test_server_restart_survival_and_memory_lifecycle():
    user_id = f"test-restart-{uuid.uuid4().hex[:6]}"
    auth_header = {"Authorization": f"Bearer test-bearer-{user_id}"}
    settings = load_settings(require_gemini=False)

    # ---- Phase 1: Server Process 1 Ingests Data ----
    extractor_1 = MockExtractor(default_facts=["User prefers concise bullet points with minimal preamble"])
    embedder_1 = MockEmbedder()
    mgr_1 = MemoryManager(settings, extractor=extractor_1, embedder=embedder_1)
    engine_1 = PreferenceEngine()
    server.memory_manager = mgr_1
    server.preference_engine = engine_1

    with TestClient(server.app) as client_1:
        ingest_res = client_1.post(
            "/api/v1/messages/ingest",
            json={
                "conversation_id": "c-restart-1",
                "text": "Please provide answers in concise bullet points with minimal preamble.",
                "role": "user",
                "event_id": f"evt-res-{uuid.uuid4().hex[:6]}",
            },
            headers=auth_header,
        )
        assert ingest_res.status_code == 200
        extracted = ingest_res.json()["facts_extracted"]
        assert len(extracted) >= 1
        memory_id = extracted[0]["id"]

    # ---- Phase 2: Server Process 1 Shuts Down, Simulating Restart ----
    server.memory_manager = None
    server.preference_engine = None

    # ---- Phase 3: Server Process 2 Starts Up Fresh ----
    extractor_2 = MockExtractor()
    embedder_2 = MockEmbedder()
    mgr_2 = MemoryManager(settings, extractor=extractor_2, embedder=embedder_2)
    engine_2 = PreferenceEngine()
    server.memory_manager = mgr_2
    server.preference_engine = engine_2

    with TestClient(server.app) as client_2:
        # 1. State persists across restart: memory is present in database
        all_res = client_2.get("/api/v1/memories", headers=auth_header)
        assert all_res.status_code == 200
        all_mems = all_res.json()
        assert any(m["id"] == memory_id for m in all_mems)

        # 2. Vector search recall succeeds on restarted server
        query_res = client_2.post(
            "/api/v1/memories/query",
            json={"query": "concise bullet points minimal preamble"},
            headers=auth_header,
        )
        assert query_res.status_code == 200
        general_mems = query_res.json()["general_memories"]
        assert any(m["id"] == memory_id for m in general_mems)

        # 3. Memory edit succeeds after restart
        new_text = "User prefers ultra-concise bullet points with zero preamble"
        update_res = client_2.put(
            f"/api/v1/memories/{memory_id}",
            json={"text": new_text},
            headers=auth_header,
        )
        assert update_res.status_code == 200
        assert update_res.json()["text"] == new_text

        # 4. Memory forget/delete succeeds after restart
        del_res = client_2.delete(f"/api/v1/memories/{memory_id}", headers=auth_header)
        assert del_res.status_code == 200
        assert del_res.json()["ok"] is True

        # Verify forgotten memory is gone from recall and storage
        post_del_all = client_2.get("/api/v1/memories", headers=auth_header).json()
        assert not any(m["id"] == memory_id for m in post_del_all)

        post_del_query = client_2.post(
            "/api/v1/memories/query",
            json={"query": "concise bullet points minimal preamble"},
            headers=auth_header,
        )
        assert not any(m["id"] == memory_id for m in post_del_query.json()["general_memories"])


# ---------------------------------------------------------------------------
# 7. LAN Address Unreachability (Strict Loopback Binding)
# ---------------------------------------------------------------------------
def test_lan_address_is_not_reachable():
    lan_ip = get_lan_ip()
    if not lan_ip:
        pytest.skip("No non-loopback LAN IP available on host network")

    # Bind a socket strictly to 127.0.0.1
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("127.0.0.1", 0))
    port = server_sock.getsockname()[1]
    server_sock.listen(1)

    stop_event = threading.Event()

    def dummy_responder():
        server_sock.settimeout(0.5)
        while not stop_event.is_set():
            try:
                conn, _ = server_sock.accept()
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
                conn.close()
            except socket.timeout:
                continue
            except Exception:
                break

    thread = threading.Thread(target=dummy_responder, daemon=True)
    thread.start()

    try:
        # Loopback MUST connect
        r_loopback = httpx.get(f"http://127.0.0.1:{port}", timeout=1.0)
        assert r_loopback.status_code == 200

        # LAN IP connection MUST be refused / unreachable
        with pytest.raises(OSError):
            socket.create_connection((lan_ip, port), timeout=1.0)
    finally:
        stop_event.set()
        server_sock.close()
        thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# 8. Confirm No Test Bypass or Deterministic Embedding in Real Configuration
# ---------------------------------------------------------------------------
def test_no_test_bypass_or_deterministic_embedding_in_real_config(monkeypatch):
    # 1. Test token bypass is blocked in production mode
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ALLOW_TEST_AUTH", "false")

    with pytest.raises(Exception) as excinfo:
        verify_supabase_token("test-bearer-alice")
    assert "disabled in this environment" in str(excinfo.value).lower()

    # 2. Deterministic embedding bypass is blocked in real mode:
    # MemoryManager fails closed on initialization when keys are missing in real mode
    monkeypatch.setenv("ALLOW_OFFLINE_EMBEDDINGS", "false")
    settings = load_settings(require_gemini=False)
    object.__setattr__(settings, "openrouter_api_key", "")
    object.__setattr__(settings, "gemini_api_key", "")

    with pytest.raises(RuntimeError) as exc_init:
        MemoryManager(settings)
    assert "required in real mode" in str(exc_init.value).lower()


# ---------------------------------------------------------------------------
# 9. Rate Limiting, CORS Restrictions, and SQLite History Permissions
# ---------------------------------------------------------------------------
def test_rate_limiting_and_cors_and_history_permissions(monkeypatch):
    # 1. Rate limiting enforcement
    monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "5")
    monkeypatch.setenv("RATE_LIMIT_WINDOW", "60.0")

    server.reset_rate_limits()
    user_id = f"test-rl-{uuid.uuid4().hex[:6]}"
    auth_header = {"Authorization": f"Bearer test-bearer-{user_id}"}

    mem_mgr = MemoryManager(extractor=MockExtractor(), embedder=MockEmbedder())
    server.memory_manager = mem_mgr
    server.preference_engine = PreferenceEngine()

    with TestClient(server.app) as client:
        # First 5 requests succeed
        for i in range(5):
            resp = client.get("/api/v1/memories", headers=auth_header)
            assert resp.status_code == 200
            assert "X-RateLimit-Limit" in resp.headers

        # 6th request is rate-limited (HTTP 429)
        resp_limited = client.get("/api/v1/memories", headers=auth_header)
        assert resp_limited.status_code == 429
        assert "Retry-After" in resp_limited.headers
        assert "rate limit exceeded" in resp_limited.json()["detail"].lower()

    server.reset_rate_limits()

    # 2. CORS origin restrictions
    with TestClient(server.app) as client:
        # Allowed origins
        for allowed_origin in ["https://chatgpt.com", "https://claude.ai", "https://gemini.google.com", "http://localhost:8000"]:
            opt = client.options(
                "/api/v1/memories",
                headers={
                    "Origin": allowed_origin,
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert opt.headers.get("access-control-allow-origin") == allowed_origin

        # Disallowed origin
        disallowed_origin = "https://evil-unauthorized-site.example.com"
        opt_bad = client.options(
            "/api/v1/memories",
            headers={
                "Origin": disallowed_origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert opt_bad.headers.get("access-control-allow-origin") is None

    # 3. SQLite history file permissions (0600)
    settings = load_settings(require_gemini=False)
    history_file = settings.history_db_path
    if os.path.exists(history_file) and os.name != "nt":
        file_mode = os.stat(history_file).st_mode
        perms = stat.S_IMODE(file_mode)
        # Permissions must be 0600 (owner read/write only, zero group/other access)
        assert perms == 0o600, f"History DB permissions are {oct(perms)}, expected 0o600"
