"""Offline contract tests for selective Jev review through OpenRouter."""

from __future__ import annotations

import json

import httpx

from memory_manager import MemoryManager
from providers import (
    JevValidator,
    MockEmbedder,
    MockExtractor,
    OpenRouterEmbedder,
    OpenRouterExtractor,
    SpendingCapExceededError,
    UsageTracker,
    create_embedder,
    create_extractor,
    create_validator,
)
from settings import load_settings


def _jev_response(*, keep=True, classification="general", quote="prefer Rust", cost=0.001):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps({
            "keep": keep,
            "classification": classification,
            "confidence": 0.92,
            "evidence_quote": quote,
            "reason": "Supported by user message",
        })}}],
        "usage": {"prompt_tokens": 25, "completion_tokens": 10, "cost": cost},
    })


def test_all_provider_factories_share_one_openrouter_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "one-test-openrouter-key")
    monkeypatch.setenv("ENABLE_JEV_VALIDATION", "true")
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.setenv("USE_MOCK_EXTRACTOR", "false")
    monkeypatch.setenv("USE_MOCK_EMBEDDER", "false")
    settings = load_settings(require_gemini=False)

    extractor = create_extractor(settings)
    embedder = create_embedder(settings)
    validator = create_validator(settings)

    assert isinstance(extractor, OpenRouterExtractor)
    assert isinstance(embedder, OpenRouterEmbedder)
    assert isinstance(validator, JevValidator)
    assert extractor.api_key == embedder.api_key == validator.api_key == "one-test-openrouter-key"
    assert validator.model == "typesafe/jev-router"
    assert validator.is_enabled()


def test_jev_uses_openrouter_chat_and_records_actual_cost():
    requests = []

    def handle(request):
        requests.append(request)
        return _jev_response()

    tracker = UsageTracker()
    client = httpx.Client(transport=httpx.MockTransport(handle))
    validator = JevValidator(enabled=True, api_key="one-test-key", http_client=client, usage_tracker=tracker)
    result = validator.validate("User prefers Rust", context={"source_text": "Maybe I prefer Rust for this task."})

    assert result.is_valid is True
    assert result.evidence_quote == "prefer Rust"
    assert len(requests) == 1
    assert str(requests[0].url) == "https://openrouter.ai/api/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer one-test-key"
    assert json.loads(requests[0].content)["model"] == "typesafe/jev-router"
    assert tracker.jev_attempts == tracker.jev_calls == 1
    assert tracker.jev_estimated_cost_usd == tracker.openrouter_estimated_cost_usd == 0.001


def test_jev_screens_secret_before_http_call():
    requests = []
    client = httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request) or _jev_response()))
    validator = JevValidator(enabled=True, api_key="one-test-key", http_client=client)

    result = validator.validate(
        "User has a key",
        context={"source_text": "My API key is sk-ant-api03-abcdef1234567890abcdef1234567890abcdef123456-ABCDEF"},
    )
    assert result.is_valid is False
    assert requests == []


def test_jev_invalid_evidence_and_http_failure_fail_closed():
    for response in (_jev_response(quote="invented evidence"), httpx.Response(503)):
        client = httpx.Client(transport=httpx.MockTransport(lambda _request: response))
        validator = JevValidator(enabled=True, api_key="one-test-key", http_client=client, usage_tracker=UsageTracker())
        result = validator.validate("User prefers Rust", context={"source_text": "Maybe I prefer Rust for this task."})
        assert result.is_valid is False


def test_jev_unknown_cost_is_explicit_and_failed_attempts_are_bounded():
    tracker = UsageTracker(jev_max_calls=2)
    response = _jev_response(cost=None)
    requests = []

    def handle(request):
        requests.append(request)
        return response

    client = httpx.Client(transport=httpx.MockTransport(handle))
    validator = JevValidator(enabled=True, api_key="one-test-key", http_client=client, usage_tracker=tracker)
    validator.validate("User prefers Rust", context={"source_text": "Maybe I prefer Rust for this task."})
    assert tracker.get_summary()["jev_unknown_cost_calls"] == 1
    assert tracker.get_summary()["jev_estimated_cost_usd"] == 0
    validator.validate("User prefers Rust", context={"source_text": "Maybe I prefer Rust for this task."})
    try:
        validator.validate("User prefers Rust", context={"source_text": "Maybe I prefer Rust for this task."})
        assert False, "A third Jev call must exceed the configured limit"
    except SpendingCapExceededError:
        pass
    assert len(requests) == 2


def test_only_ambiguous_candidates_get_jev_review_and_rejected_ones_are_not_saved():
    requests = []

    def handle(request):
        requests.append(request)
        return _jev_response(keep=False)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    validator = JevValidator(enabled=True, api_key="one-test-key", http_client=client, usage_tracker=UsageTracker())
    manager = MemoryManager(
        extractor=MockExtractor(default_facts=["User prefers Rust"]),
        embedder=MockEmbedder(),
        validator=validator,
    )

    direct = manager.add("I prefer Rust every day.", user_id="jev-test-user")
    ambiguous = manager.add("Maybe I prefer Rust for this task.", user_id="jev-test-user")

    assert len(direct["results"]) == 1
    assert ambiguous["results"] == []
    assert len(requests) == 1
    assert manager.collection.count_documents({"payload.user_id": "jev-test-user"}) == 1


def test_invalid_jev_evidence_cannot_create_ambiguous_memory():
    client = httpx.Client(transport=httpx.MockTransport(lambda _request: _jev_response(quote="invented evidence")))
    validator = JevValidator(enabled=True, api_key="one-test-key", http_client=client, usage_tracker=UsageTracker())
    manager = MemoryManager(
        extractor=MockExtractor(default_facts=["User prefers Rust"]),
        embedder=MockEmbedder(),
        validator=validator,
    )
    result = manager.add("Maybe I prefer Rust for this task.", user_id="invalid-jev-user")
    assert result["results"] == []
    assert manager.collection.count_documents({"payload.user_id": "invalid-jev-user"}) == 0


def test_jev_cannot_downgrade_sensitive_fact():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda _request: _jev_response(classification="general", quote="severe peanut allergy")
    ))
    validator = JevValidator(enabled=True, api_key="one-test-key", http_client=client, usage_tracker=UsageTracker())
    manager = MemoryManager(
        extractor=MockExtractor(default_facts=["User has a severe peanut allergy"]),
        embedder=MockEmbedder(),
        validator=validator,
    )

    result = manager.add("Maybe I have a severe peanut allergy.", user_id="sensitive-user")
    assert result["results"][0]["classification"] == "sensitive"
    doc = manager.collection.find_one({"payload.user_id": "sensitive-user"})
    assert doc["payload"]["classification"] == "sensitive"


def test_ambiguous_fact_skipped_when_jev_disabled():
    manager = MemoryManager(
        extractor=MockExtractor(default_facts=["User prefers Rust"]),
        embedder=MockEmbedder(),
        validator=JevValidator(enabled=False),
    )
    result = manager.add("Maybe I prefer Rust for this task.", user_id="no-jev-user")
    assert result["results"] == []
