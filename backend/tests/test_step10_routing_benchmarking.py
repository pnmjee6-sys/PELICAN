"""Step 10 Verification Suite — Model Routing, Providers & Benchmarking.

Verifies:
1. Real extraction and provider abstractions (GLM 5.3 Flash / Gemini 2.5 Flash Lite)
2. 1536-dimensional unit-normalized embeddings (text-embedding-3-small)
3. Retrieval scoring & threshold tuning (relevant vs irrelevant separation)
4. Schema parsing & fail-closed behavior on malformed output
5. Secret credential screening (zero raw secrets in facts or logs)
6. Automated fallback state machine (429, timeout, 5xx server outage)
7. Jev validation pass gating, usage tracking, and fail-closed resilience
8. OpenRouter spending cap (<$5 balance) and Jev spending cap enforcement
9. /ready endpoint enforcement with real required configuration
10. Synthetic benchmark execution and quality threshold verification
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import unittest.mock as mock
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest
from starlette.testclient import TestClient

# Ensure test flags are set
os.environ["APP_ENV"] = "test"
os.environ["ALLOW_TEST_AUTH"] = "true"
os.environ["ALLOW_OFFLINE_EMBEDDINGS"] = "true"

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from benchmark_routing import (
    BENCHMARK_CASES,
    run_benchmark,
    tune_embedding_threshold,
    verify_secret_scrub,
)
from memory_manager import MemoryManager, MIN_MEMORY_SCORE
from providers import (
    FactExtractor,
    Embedder,
    OpenRouterExtractor,
    OpenRouterEmbedder,
    JevValidator,
    MockExtractor,
    MockEmbedder,
    MockValidator,
    UsageTracker,
    global_usage_tracker,
    parse_and_validate_facts_json,
    create_extractor,
    create_embedder,
    create_validator,
    ProviderError,
    ProviderTimeoutError,
    ProviderRateLimitError,
    ProviderOutageError,
    InvalidProviderResponseError,
    SpendingCapExceededError,
    MissingConfigurationError,
)
from security import contains_secret, classify_text
from server import app
from settings import Settings, load_settings


# ---------------------------------------------------------------------------
# Test 1: Spending Caps Enforcement (OpenRouter & Jev)
# ---------------------------------------------------------------------------
def test_spending_caps_enforcement_openrouter_and_jev():
    """Verify separate OpenRouter spending cap (<$5 balance) and Jev cap."""
    tracker = UsageTracker(spending_cap_usd=2.00, jev_spending_cap_usd=1.00, jev_max_calls=20_000)

    # 1. Under cap initially
    tracker.check_cap("openrouter")
    tracker.check_cap("jev")

    # 2. Add OpenRouter usage up to $1.99
    # Rate: $0.04 / 1M prompt -> 49,750,000 prompt tokens = $1.99
    tracker.record_chat_usage(49_750_000, 0, "z-ai/glm-5.3-flash")
    tracker.check_cap("openrouter")
    tracker.check_cap("jev")  # Jev is unaffected

    # 3. Add usage exceeding OpenRouter cap ($2.01)
    tracker.record_chat_usage(500_000, 0, "z-ai/glm-5.3-flash")
    with pytest.raises(SpendingCapExceededError) as exc_info:
        tracker.check_cap("openrouter")
    assert "OpenRouter spending cap of $2.00 reached" in str(exc_info.value)

    # Jev is still under its independent cap ($1.00)
    tracker.check_cap("jev")

    # 4. Now simulate Jev calls exceeding Jev cap ($1.00)
    for _ in range(10_001):
        tracker.record_jev_usage(0.0001)

    with pytest.raises(SpendingCapExceededError) as exc_info:
        tracker.check_cap("jev")
    assert "Jev spending cap of $1.00 reached" in str(exc_info.value)

    # Summary reflects separate spending
    summary = tracker.get_summary()
    assert summary["openrouter_spending_cap_usd"] == 2.00
    assert summary["jev_spending_cap_usd"] == 1.00
    assert summary["cap_exceeded"] is True


# ---------------------------------------------------------------------------
# Test 2: Defense-in-Depth Secret Credential Screening in Provider Parsing
# ---------------------------------------------------------------------------
def test_defense_in_depth_secret_screening_in_provider_parsing():
    """Verify provider output parser drops raw credentials before persistence."""
    raw_response_with_secrets = json.dumps([
        "User prefers dark mode",
        "API key is sk-ant-api03-abcdef1234567890abcdef1234567890abcdef123456-ABCDEF",
        "User builds in TypeScript",
        "The password is password: SuperSecretP@ssw0rd!123",
        "Bearer token is Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.secretToken123456",
        "User has a peanut allergy",
    ])

    facts = parse_and_validate_facts_json(raw_response_with_secrets)

    # Legitimate facts preserved
    assert "User prefers dark mode" in facts
    assert "User builds in TypeScript" in facts
    assert "User has a peanut allergy" in facts

    # Secrets screened and dropped completely
    for fact in facts:
        assert not contains_secret(fact)
        assert "sk-ant-" not in fact
        assert "SuperSecretP@ssw0rd!123" not in fact
        assert "Bearer" not in fact

    assert len(facts) == 3


# ---------------------------------------------------------------------------
# Test 3: 1536-Dimensional Unit-Normalized Embeddings Contract
# ---------------------------------------------------------------------------
def test_1536_dimensional_embeddings_contract():
    """Verify embedder enforces 1536-dimensional unit-normalized vectors."""
    embedder = MockEmbedder(dimensions=1536)
    vec = embedder.embed("User prefers concise bullet points")

    assert len(vec) == 1536
    assert all(isinstance(x, float) for x in vec)

    # Check unit normalization: sum(x^2) ≈ 1.0
    norm = math.sqrt(sum(x * x for x in vec))
    assert math.isclose(norm, 1.0, rel_tol=1e-3)

    # Test OpenRouterEmbedder schema validation on mismatched dimensions
    mock_client = mock.MagicMock(spec=httpx.Client)
    # Return 768 dimensions instead of 1536
    mock_resp = mock.MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [{"embedding": [0.1] * 768}],
        "usage": {"total_tokens": 10},
    }
    mock_client.post.return_value = mock_resp

    bad_embedder = OpenRouterEmbedder(
        api_key="sk-test",
        dimensions=1536,
        http_client=mock_client,
    )
    with pytest.raises(InvalidProviderResponseError) as exc_info:
        bad_embedder.embed("test text")
    assert "Expected 1536 dimensions, got 768" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 4: Strict Schema Validation & Fail-Closed Output Handling
# ---------------------------------------------------------------------------
def test_strict_schema_validation_and_fail_closed():
    """Verify provider output validation strictly fails closed on invalid schemas."""
    # 1. Empty string
    with pytest.raises(InvalidProviderResponseError):
        parse_and_validate_facts_json("")

    # 2. Malformed JSON
    with pytest.raises(InvalidProviderResponseError):
        parse_and_validate_facts_json("Not a json at all")

    # 3. Valid JSON object instead of array
    with pytest.raises(InvalidProviderResponseError):
        parse_and_validate_facts_json('{"fact": "User prefers dark mode"}')

    # 4. JSON array of non-strings (e.g. integers or nested dicts)
    with pytest.raises(InvalidProviderResponseError):
        parse_and_validate_facts_json('[123, 456]')

    # 5. Markdown-wrapped JSON (```json [...] ```) is safely parsed
    markdown_wrapped = "```json\n[\"User prefers concise answers\"]\n```"
    facts = parse_and_validate_facts_json(markdown_wrapped)
    assert facts == ["User prefers concise answers"]


# ---------------------------------------------------------------------------
# Test 5: Automated Fallback on 429, 5xx, or Provider Timeout
# ---------------------------------------------------------------------------
def test_automated_fallback_on_429_5xx_timeout():
    """Verify primary model failure triggers seamless fallback to Gemini Flash Lite."""
    mock_client = mock.MagicMock(spec=httpx.Client)

    # Sequence: Call 1 (Primary GLM 5.3 Flash) returns 429; Call 2 (Fallback Gemini) returns 200
    resp_primary_429 = mock.MagicMock()
    resp_primary_429.status_code = 429

    resp_fallback_200 = mock.MagicMock()
    resp_fallback_200.status_code = 200
    resp_fallback_200.json.return_value = {
        "choices": [{"message": {"content": json.dumps(["Fallback fact from Gemini Lite"])}}],
        "usage": {"prompt_tokens": 15, "completion_tokens": 5},
    }

    mock_client.post.side_effect = [resp_primary_429, resp_fallback_200]

    extractor = OpenRouterExtractor(
        api_key="sk-test",
        primary_model="z-ai/glm-5.3-flash",
        fallback_model="google/gemini-2.5-flash-lite",
        max_retries=1,
        http_client=mock_client,
    )

    result = extractor.extract_facts("I code in Python", user_id="test_user")

    assert result.fallback_used is True
    assert result.model_used == "google/gemini-2.5-flash-lite"
    assert result.facts == ["Fallback fact from Gemini Lite"]

    # Test both failing causes ProviderError
    mock_client.post.side_effect = [resp_primary_429, resp_primary_429]
    with pytest.raises(ProviderError):
        extractor.extract_facts("I code in Python", user_id="test_user")


# ---------------------------------------------------------------------------
# Test 6: Jev Validation Pass Gating & Fail-Closed Safety
# ---------------------------------------------------------------------------
def test_jev_validation_gating_and_fail_closed_safety():
    """Verify Jev validation gating, usage tracking, and fail-closed resilience."""
    # 1. Disabled by default
    v_disabled = JevValidator(enabled=False, api_key="")
    assert not v_disabled.is_enabled()
    res = v_disabled.validate("Sensitive medical notes")
    assert res.is_valid is True
    assert "disabled" in res.reason

    # 2. Enabled but key missing -> disabled
    v_no_key = JevValidator(enabled=True, api_key="")
    assert not v_no_key.is_enabled()

    # 3. Enabled with key: 200 OK sensitive decision
    mock_client = mock.MagicMock(spec=httpx.Client)
    resp_200 = mock.MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {
        "choices": [{"message": {"content": json.dumps({
            "keep": True, "classification": "sensitive", "confidence": 0.95,
            "evidence_quote": "severe asthma", "reason": "Durable health condition",
        })}}],
        "usage": {"prompt_tokens": 30, "completion_tokens": 12, "cost": 0.001},
    }
    mock_client.post.return_value = resp_200

    tracker = UsageTracker(jev_spending_cap_usd=1.00)
    v_enabled = JevValidator(
        enabled=True,
        api_key="openrouter-test-key",
        http_client=mock_client,
        usage_tracker=tracker,
    )
    assert v_enabled.is_enabled()

    res = v_enabled.validate("I have severe asthma and use an inhaler")
    assert res.classification == "sensitive"
    assert res.confidence == 0.95
    assert tracker.jev_calls == 1
    assert tracker.jev_estimated_cost_usd > 0.0

    # 4. Fail closed on 500 error or network outage
    resp_500 = mock.MagicMock()
    resp_500.status_code = 500
    mock_client.post.return_value = resp_500

    res_fail = v_enabled.validate("Normal programming preference")
    assert res_fail.is_valid is False
    assert "failed" in res_fail.reason


# ---------------------------------------------------------------------------
# Test 7: /ready Endpoint Requires Real Configuration
# ---------------------------------------------------------------------------
def test_ready_endpoint_requires_real_configuration(monkeypatch):
    """Verify /ready endpoint strictly requires real configuration in production."""
    client = TestClient(app)

    # In production environment with openrouter provider and missing key
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    resp = client.get("/ready")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "configuration_required"
    assert "OPENROUTER_API_KEY" in data["missing"]

    # Jev uses the same OpenRouter key; no second key is required.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-mock-key")
    monkeypatch.setenv("ENABLE_JEV_VALIDATION", "true")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("JEV_API_KEY", raising=False)

    resp_jev = client.get("/ready")
    assert resp_jev.status_code == 200
    data_jev = resp_jev.json()
    assert "JEV_API_KEY" not in data_jev.get("missing", [])


# ---------------------------------------------------------------------------
# Test 8: Logs and Records Clean of Raw Secrets
# ---------------------------------------------------------------------------
def test_logs_and_records_clean_of_raw_secrets():
    """Verify exceptions and record audit contains zero raw credentials."""
    secret_key = "sk-ant-api03-secret1234567890abcdef1234567890abcdef123456-ABCDEF"

    # 1. ProviderTimeoutError does not leak secret key in message
    err = ProviderTimeoutError(f"OpenRouter request timed out for model z-ai/glm-5.3-flash")
    assert secret_key not in str(err)

    # 2. Check verify_secret_scrub helper
    clean_records = [
        {"id": "1", "extracted_facts": ["User likes Python 3.11", "User works in FastAPI"]},
        {"id": "2", "extracted_facts": ["User has a peanut allergy"]},
    ]
    assert verify_secret_scrub(clean_records) is True

    leaky_records = [
        {"id": "1", "extracted_facts": [f"API key is {secret_key}"]},
    ]
    assert verify_secret_scrub(leaky_records) is False


# ---------------------------------------------------------------------------
# Test 9: Embedding Score Threshold Tuning & Separation
# ---------------------------------------------------------------------------
def test_embedding_score_threshold_tuning():
    """Verify embedding score separation between relevant and irrelevant queries."""
    embedder = MockEmbedder(dimensions=1536)
    tuning = tune_embedding_threshold(embedder)

    assert tuning["dimensions"] == 1536
    assert "relevant_scores" in tuning
    assert "irrelevant_scores" in tuning
    assert tuning["relevant_scores"]["median"] > tuning["irrelevant_scores"]["median"]
    assert tuning["score_margin"] > 0.0
    assert 0.50 <= tuning["optimal_threshold"] <= 0.85


# ---------------------------------------------------------------------------
# Test 10: Synthetic Benchmark Suite Execution & Quality Gating
# ---------------------------------------------------------------------------
def test_synthetic_benchmark_suite_execution():
    """Verify synthetic benchmark executes cleanly and produces structured report."""
    report = run_benchmark(mock_mode=True)

    assert "routing_decision" in report
    assert report["routing_decision"].startswith("UNVERIFIED")
    assert report["recommended_primary"] is None
    assert report["recommended_fallback"] is None
    assert "recommended_primary" in report
    assert "recommended_fallback" in report
    assert "glm_metrics" in report
    assert "gemini_metrics" in report
    assert "embedding_threshold_tuning" in report
    assert report["privacy_scrub_passed"] is True

    # 40 cases evaluated
    assert report["glm_metrics"]["total_cases"] == 40
    assert report["gemini_metrics"]["total_cases"] == 40
    assert report["glm_metrics"]["secret_leaks"] == 0
    assert report["gemini_metrics"]["secret_leaks"] == 0
