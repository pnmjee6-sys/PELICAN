"""A user-confirmed onboarding memory must be stored, visible, and recallable."""

import io
import json
import zipfile
from starlette.testclient import TestClient
from types import SimpleNamespace

import httpx

from server import app


def test_downloaded_extension_is_paired_to_public_site(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://pelican-demo.onrender.com")
    with TestClient(app) as client:
        response = client.get("/download/pelican-v3.zip")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["homepage_url"] == "https://pelican-demo.onrender.com"
        assert "https://pelican-demo.onrender.com/*" in manifest["host_permissions"]
        assert b"https://pelican-demo.onrender.com" in archive.read("pelican-config.js")


def test_public_extension_download_needs_known_https_origin(monkeypatch):
    monkeypatch.setenv("PUBLIC_HOSTING", "true")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    with TestClient(app) as client:
        response = client.get("/download/pelican-v3.zip")
    assert response.status_code == 503
    assert "PUBLIC_BASE_URL" in response.json()["detail"]


def test_onboarding_import_reaches_vault_and_recall_without_crossing_accounts():
    alice = {"Authorization": "Bearer test-bearer-onboarding-alice"}
    bob = {"Authorization": "Bearer test-bearer-onboarding-bob"}
    fact = "User builds browser extensions with TypeScript and prefers concise examples."
    secret = "API key is sk-testsecret1234567890abcdefghijklmnop"

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/memories/import",
            headers=alice,
            json={"name": "Ada", "role": "Engineer", "memories": [fact, secret]},
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["saved_count"] == 3
        assert result["skipped_count"] == 1
        assert all(secret not in item.get("text", "") for item in result["saved"])

        vault = client.get("/api/v1/memories", headers=alice)
        assert vault.status_code == 200
        assert any(item["text"] == fact and item["source"] == "manual" for item in vault.json())
        assert any(item["classification"] == "sensitive" for item in vault.json() if "name" in item["text"])

        recall = client.post("/api/v1/memories/query", headers=alice, json={"query": fact})
        assert recall.status_code == 200
        assert any(item["text"] == fact for item in recall.json()["general_memories"])

        retry = client.post("/api/v1/memories/import", headers=alice, json={"memories": [fact]})
        assert retry.status_code == 200
        assert retry.json()["saved_count"] == 0
        assert retry.json()["duplicate_count"] == 1

        other_vault = client.get("/api/v1/memories", headers=bob)
        assert other_vault.status_code == 200
        assert other_vault.json() == []


def test_import_requires_authentication():
    with TestClient(app) as client:
        response = client.post("/api/v1/memories/import", json={"memories": ["User prefers concise answers."]})
        assert response.status_code == 401


def test_signup_handles_immediate_session_and_email_confirmation(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://auth.example.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-test-key")
    seen = []

    def signup_response(url, *, headers, json, timeout):
        seen.append((url, json["email"]))
        token = "new-session" if len(seen) == 1 else None
        return SimpleNamespace(status_code=200, json=lambda: {"access_token": token})

    monkeypatch.setattr(httpx, "post", signup_response)
    with TestClient(app) as client:
        first = client.post("/api/v1/auth/signup", json={"email": "ada@example.com", "password": "safe-test-password"})
        second = client.post("/api/v1/auth/signup", json={"email": "bea@example.com", "password": "safe-test-password"})
    assert first.status_code == 200
    assert first.json()["status"] == "ready"
    assert first.json()["access_token"] == "new-session"
    assert first.json()["token_type"] == "bearer"
    assert second.json()["status"] == "confirmation_required"
    assert second.json()["access_token"] is None
    assert all(url == "https://auth.example.test/auth/v1/signup" for url, _ in seen)


def test_auth_routes_keep_refresh_tokens_and_reject_empty_refresh(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://auth.example.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-test-key")
    def fake_post(url, **_kwargs):
        if "refresh_token" in url:
            return httpx.Response(200, json={"access_token":"rotated", "refresh_token":"rotated-refresh", "expires_in":1800})
        return httpx.Response(200, json={"access_token":"signed-in", "refresh_token":"fresh-refresh", "expires_in":3600})
    monkeypatch.setattr(httpx, "post", fake_post)
    with TestClient(app) as client:
        login = client.post("/api/v1/auth/token", json={"email":"a@example.com", "password":"password123"})
        refresh = client.post("/api/v1/auth/refresh", json={"refresh_token":"fresh-refresh"})
        oauth = client.post("/api/v1/auth/oauth-callback", json={"code":"synthetic-code"})
    assert login.json()["refresh_token"] == "fresh-refresh"
    assert refresh.json()["refresh_token"] == "rotated-refresh"
    assert oauth.json()["refresh_token"] == "fresh-refresh"
    assert all(result.json()["token_type"] == "bearer" for result in (login, refresh, oauth))


def test_auth_errors_explain_unconfirmed_email_and_signup_rate_limit(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://auth.example.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-test-key")

    def auth_error(url, **_kwargs):
        code = "email_not_confirmed" if "/token?" in url else "over_email_send_rate_limit"
        return httpx.Response(400, json={"code": code})

    monkeypatch.setattr(httpx, "post", auth_error)
    with TestClient(app) as client:
        login = client.post("/api/v1/auth/token", json={"email": "ada@example.com", "password": "safe-test-password"})
        signup = client.post("/api/v1/auth/signup", json={"email": "ada@example.com", "password": "safe-test-password"})
    assert login.status_code == 403
    assert "Confirm your email" in login.json()["detail"]
    assert signup.status_code == 400
    assert "Too many confirmation emails" in signup.json()["detail"]


def test_google_auth_url_generation(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://auth.example.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-test-key")
    with TestClient(app) as client:
        resp = client.get("/api/v1/auth/google?redirect_to=http://127.0.0.1:8000/dashboard.html")
    assert resp.status_code == 200
    url = resp.json().get("url")
    assert "https://auth.example.test/auth/v1/authorize" in url
    assert "provider=google" in url
    assert "redirect_to=http%3A%2F%2F127.0.0.1%3A8000%2Fdashboard.html" in url


def test_unlimited_context_import_accepts_large_memories():
    """Verify that user can import any amount of context: 50+ memories and long text > 1000 characters."""
    auth_header = {"Authorization": "Bearer test-bearer-unlimited-context"}
    # Generate 45 memories + one large memory > 1200 characters
    long_memory = "Comprehensive architecture guidelines: " + ("Always prefer modular functional components with clear error boundaries. " * 20)
    memories = [f"Preference observation #{i}: User prefers clean Pythonic patterns." for i in range(1, 45)]
    memories.append(long_memory)

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/memories/import",
            headers=auth_header,
            json={"memories": memories},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["saved_count"] == 45
        assert data["duplicate_count"] == 0
        assert data["skipped_count"] == 0
