"""Test suite for Step 2 — Provider interfaces and mock implementations.

Verifies:
1. Valid output parsing into ExtractionResult
2. Invalid JSON, non-list, and wrong element types fail closed
3. Empty output fails closed
4. Bounded timeout handling
5. HTTP 429 rate limit handling
6. Provider 5xx outage handling
7. Automatic fallback from primary to secondary model
8. Privacy: zero API keys, tokens, or message bodies in logs or error messages
9. Missing configuration fails visibly
10. Spending cap tracking and enforcement
11. Embedder dimension verification (1536 dims)
12. Feature-flagged Jev validation pass
13. Zero external paid API calls in mock tests
"""

from __future__ import annotations

import logging
import pytest
import httpx

from providers import (
    ExtractionResult,
    FactExtractor,
    Embedder,
    ValidatorPass,
    OpenRouterExtractor,
    OpenRouterEmbedder,
    JevValidator,
    MockExtractor,
    MockEmbedder,
    MockValidator,
    UsageTracker,
    ProviderError,
    ProviderTimeoutError,
    ProviderRateLimitError,
    ProviderOutageError,
    InvalidProviderResponseError,
    SpendingCapExceededError,
    MissingConfigurationError,
    parse_and_validate_facts_json,
)


# ---------------------------------------------------------------------------
# Helpers for Mock HTTP Transport
# ---------------------------------------------------------------------------
class MockHTTPTransport(httpx.BaseTransport):
    """Synthetic HTTP transport simulating OpenRouter and Jev responses."""

    def __init__(self, handler):
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


# ---------------------------------------------------------------------------
# 1. Output & Schema Parsing Tests
# ---------------------------------------------------------------------------
def test_parse_valid_facts_json():
    raw = '["User works at TechCorp", "User prefers dark mode"]'
    facts = parse_and_validate_facts_json(raw)
    assert facts == ["User works at TechCorp", "User prefers dark mode"]


def test_parse_valid_facts_with_markdown_fence():
    raw = '```json\n["User prefers TypeScript", "User has a cat"]\n```'
    facts = parse_and_validate_facts_json(raw)
    assert facts == ["User prefers TypeScript", "User has a cat"]


def test_parse_empty_array_facts():
    raw = "[]"
    facts = parse_and_validate_facts_json(raw)
    assert facts == []


def test_parse_invalid_json_fails_closed():
    with pytest.raises(InvalidProviderResponseError, match="not valid JSON"):
        parse_and_validate_facts_json("this is not json {")


def test_parse_non_list_fails_closed():
    with pytest.raises(InvalidProviderResponseError, match="must be a JSON array"):
        parse_and_validate_facts_json('{"fact": "user likes python"}')


def test_parse_non_string_items_fails_closed():
    with pytest.raises(InvalidProviderResponseError, match="must be a string"):
        parse_and_validate_facts_json("[123, 456]")


def test_parse_empty_string_fails_closed():
    with pytest.raises(InvalidProviderResponseError, match="empty response"):
        parse_and_validate_facts_json("   ")


def test_extracted_fact_containing_secret_is_screened():
    raw = '["User prefers dark mode", "AWS key AKIAIOSFODNN7EXAMPLE is used"]'
    facts = parse_and_validate_facts_json(raw)
    assert "User prefers dark mode" in facts
    assert not any("AKIA" in f for f in facts)


# ---------------------------------------------------------------------------
# 2. OpenRouter Extractor: Success, Fallback & Failure Modes
# ---------------------------------------------------------------------------
def test_openrouter_extractor_primary_success():
    def mock_handler(request: httpx.Request) -> httpx.Response:
        body = {
            "choices": [{"message": {"content": '["User prefers PostgreSQL"]'}}],
            "usage": {"prompt_tokens": 25, "completion_tokens": 10},
        }
        return httpx.Response(200, json=body)

    transport = MockHTTPTransport(mock_handler)
    client = httpx.Client(transport=transport)
    tracker = UsageTracker(spending_cap_usd=5.0)

    extractor = OpenRouterExtractor(
        api_key="test-openrouter-key-123",
        primary_model="z-ai/glm-5.3-flash",
        fallback_model="google/gemini-2.5-flash-lite",
        usage_tracker=tracker,
        http_client=client,
    )

    result = extractor.extract_facts("I always use PostgreSQL for production databases.", user_id="u1")
    assert result.facts == ["User prefers PostgreSQL"]
    assert result.model_used == "z-ai/glm-5.3-flash"
    assert result.fallback_used is False
    assert tracker.total_prompt_tokens == 25
    assert tracker.total_completion_tokens == 10
    assert len(transport.requests) == 1


def test_openrouter_extractor_fallback_on_429_rate_limit():
    attempts = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            # Primary model hits 429
            return httpx.Response(429, json={"error": "Rate limit exceeded"})
        # Fallback model succeeds
        body = {
            "choices": [{"message": {"content": '["User uses Kubernetes"]'}}],
            "usage": {"prompt_tokens": 30, "completion_tokens": 8},
        }
        return httpx.Response(200, json=body)

    transport = MockHTTPTransport(mock_handler)
    client = httpx.Client(transport=transport)
    extractor = OpenRouterExtractor(
        api_key="test-key",
        primary_model="z-ai/glm-5.3-flash",
        fallback_model="google/gemini-2.5-flash-lite",
        http_client=client,
    )

    result = extractor.extract_facts("We deploy our microservices on Kubernetes.", user_id="u1")
    assert result.facts == ["User uses Kubernetes"]
    assert result.model_used == "google/gemini-2.5-flash-lite"
    assert result.fallback_used is True
    assert attempts == 2


def test_openrouter_extractor_fallback_on_5xx_outage():
    attempts = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:  # Primary model 503s
            return httpx.Response(503, json={"error": "Model temporarily unavailable"})
        # Fallback succeeds
        body = {
            "choices": [{"message": {"content": '["User builds audio plugins"]'}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 6},
        }
        return httpx.Response(200, json=body)

    transport = MockHTTPTransport(mock_handler)
    client = httpx.Client(transport=transport)
    extractor = OpenRouterExtractor(
        api_key="test-key",
        primary_model="z-ai/glm-5.3-flash",
        fallback_model="google/gemini-2.5-flash-lite",
        http_client=client,
    )

    result = extractor.extract_facts("I create WebAssembly audio DSP plugins.", user_id="u1")
    assert result.facts == ["User builds audio plugins"]
    assert result.model_used == "google/gemini-2.5-flash-lite"
    assert result.fallback_used is True


def test_openrouter_extractor_both_fail_raises_provider_error():
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "Complete service outage"})

    transport = MockHTTPTransport(mock_handler)
    client = httpx.Client(transport=transport)
    extractor = OpenRouterExtractor(
        api_key="test-key",
        http_client=client,
    )

    with pytest.raises(ProviderError, match="Fact extraction failed across primary"):
        extractor.extract_facts("Some user message", user_id="u1")


def test_openrouter_extractor_timeout_handling():
    def mock_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Read timed out")

    transport = MockHTTPTransport(mock_handler)
    client = httpx.Client(transport=transport)
    extractor = OpenRouterExtractor(
        api_key="test-key",
        max_retries=1,
        http_client=client,
    )

    with pytest.raises(ProviderError, match="Fact extraction failed"):
        extractor.extract_facts("Some text", user_id="u1")


def test_missing_api_key_raises_configuration_error():
    extractor = OpenRouterExtractor(api_key="")
    with pytest.raises(MissingConfigurationError, match="OPENROUTER_API_KEY is not configured"):
        extractor.extract_facts("Text", user_id="u1")


# ---------------------------------------------------------------------------
# 3. Privacy & Leak Prevention
# ---------------------------------------------------------------------------
def test_privacy_no_api_key_or_message_body_in_error_or_logs(caplog):
    secret_key = "sk-or-v1-9876543210abcdef9876543210abcdef"
    private_message = "My private medical condition is falcon-omega-syndrome"

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "Internal failure"})

    transport = MockHTTPTransport(mock_handler)
    client = httpx.Client(transport=transport)
    extractor = OpenRouterExtractor(
        api_key=secret_key,
        http_client=client,
    )

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ProviderError) as exc_info:
            extractor.extract_facts(private_message, user_id="u1")

    # Assert error text does not contain secret or message
    error_str = str(exc_info.value)
    assert secret_key not in error_str
    assert private_message not in error_str

    # Assert logged messages do not contain secret or message
    log_text = caplog.text
    assert secret_key not in log_text
    assert private_message not in log_text


# ---------------------------------------------------------------------------
# 4. Usage Tracking & Spending Cap Enforcement
# ---------------------------------------------------------------------------
def test_spending_cap_enforcement():
    tracker = UsageTracker(spending_cap_usd=0.0001)  # Very low cap
    # Simulate usage
    tracker.record_chat_usage(prompt_tokens=1000, completion_tokens=1000, model="z-ai/glm-5.3-flash")

    # Second call should exceed cap
    with pytest.raises(SpendingCapExceededError, match="Spending cap"):
        tracker.check_cap()

    summary = tracker.get_summary()
    assert summary["cap_exceeded"] is True
    assert summary["total_calls"] == 1


# ---------------------------------------------------------------------------
# 5. OpenRouter Embedder Tests
# ---------------------------------------------------------------------------
def test_openrouter_embedder_valid_vector():
    fake_vector = [0.01 * (i % 10) for i in range(1536)]

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [{"embedding": fake_vector, "index": 0}],
                "usage": {"total_tokens": 8},
            },
        )

    transport = MockHTTPTransport(mock_handler)
    client = httpx.Client(transport=transport)
    embedder = OpenRouterEmbedder(
        api_key="test-key",
        dimensions=1536,
        http_client=client,
    )

    vector = embedder.embed("Test embedding text")
    assert len(vector) == 1536
    assert vector == fake_vector


def test_openrouter_embedder_dimension_mismatch_fails_closed():
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [{"embedding": [0.1] * 768, "index": 0}],
                "usage": {"total_tokens": 5},
            },
        )

    transport = MockHTTPTransport(mock_handler)
    client = httpx.Client(transport=transport)
    embedder = OpenRouterEmbedder(
        api_key="test-key",
        dimensions=1536,
        http_client=client,
    )

    with pytest.raises(InvalidProviderResponseError, match="Expected 1536 dimensions, got 768"):
        embedder.embed("Test text")


# ---------------------------------------------------------------------------
# 6. Jev (TypeSafe AI) Feature Flag Tests
# ---------------------------------------------------------------------------
def test_jev_validator_disabled_by_default():
    jev = JevValidator(enabled=False, api_key="some-key")
    assert jev.is_enabled() is False
    res = jev.validate("I have a severe peanut allergy")
    assert res.is_valid is True
    assert res.reason == "Jev validation is disabled"


def test_jev_validator_enabled_flow():
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer jev-key-123"
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"keep":true,"classification":"sensitive","confidence":0.95,"evidence_quote":"severe peanut allergy","reason":"durable"}'}}],
            "usage": {"cost": 0.001},
        })

    transport = MockHTTPTransport(mock_handler)
    client = httpx.Client(transport=transport)
    jev = JevValidator(
        enabled=True,
        api_key="jev-key-123",
        http_client=client,
    )

    assert jev.is_enabled() is True
    res = jev.validate("I have a severe peanut allergy")
    assert res.is_valid is True
    assert res.classification == "sensitive"
    assert res.confidence == 0.95


# ---------------------------------------------------------------------------
# 7. Mock Classes: Zero External Calls & Configuration Proof
# ---------------------------------------------------------------------------
def test_mock_extractor_makes_zero_external_calls():
    mock_ext = MockExtractor(default_facts=["Learned to use Vite"])
    result = mock_ext.extract_facts("I switched our build tool to Vite.", user_id="u1")
    assert result.facts == ["Learned to use Vite"]
    assert mock_ext.call_count == 1
    assert result.fallback_used is False


def test_mock_embedder_deterministic_vectors():
    mock_emb = MockEmbedder(dimensions=1536)
    vec1 = mock_emb.embed("Hello world")
    vec2 = mock_emb.embed("Hello world")
    assert len(vec1) == 1536
    assert vec1 == vec2
    assert mock_emb.call_count == 2
