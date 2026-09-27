import pytest
from starlette.testclient import TestClient

from server import app


def test_health_endpoint_exposes_pinned_versions():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["mem0"] == "2.2.0"
    assert body["fastapi"] == "0.141.1"
    assert "mcp" not in body


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("provider", ["gemini", "openrouter"])
def test_ready_endpoint_reports_configuration_without_secret_values(monkeypatch, configured, provider):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LLM_PROVIDER", provider)
    model_key = "GEMINI_API_KEY" if provider == "gemini" else "OPENROUTER_API_KEY"
    required = (
        model_key,
        "MONGODB_URI",
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
    )
    for name in required:
        if configured:
            monkeypatch.setenv(name, f"secret-{name.lower()}")
        else:
            monkeypatch.delenv(name, raising=False)

    with TestClient(app) as client:
        response = client.get("/ready")

    body = response.json()
    assert response.status_code == (200 if configured else 503)
    assert body["status"] == ("ready" if configured else "configuration_required")
    assert body["missing"] == ([] if configured else list(required))
    assert "secret-" not in response.text


def test_ready_endpoint_uses_same_openrouter_key_for_jev(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("ENABLE_JEV_VALIDATION", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-or-key")
    monkeypatch.setenv("MONGODB_URI", "secret-mongo")
    monkeypatch.setenv("SUPABASE_URL", "secret-supabase")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "secret-anon")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "secret-service")
    monkeypatch.delenv("JEV_API_KEY", raising=False)

    with TestClient(app) as client:
        resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"
    assert "secret-" not in resp.text
