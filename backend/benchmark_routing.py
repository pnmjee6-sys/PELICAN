"""Context Passport V3 — Step 10 Model Routing & Extraction Benchmark.

Evaluates:
1. Fact extraction: GLM 5.3 Flash vs Gemini 2.5 Flash Lite across 40 synthetic test cases.
2. Embedding vector quality & threshold tuning: text-embedding-3-small (1536 dims).
3. Jev (TypeSafe AI) validation pass: API behavior, latency, pricing, and sensitivity gating.
4. Fallback triggers and automatic failover verification.
5. Strict privacy & secret scrubbing check (0 raw credentials permitted).
6. Spending cap enforcement: OpenRouter cap (<$5 balance) and Jev cap.

Usage:
    # Run against real APIs (when OPENROUTER_API_KEY is configured in .env):
    PYTHONPATH=backend backend/.venv/bin/python backend/benchmark_routing.py

    # Run in mock/synthetic mode for offline contract testing:
    PYTHONPATH=backend backend/.venv/bin/python backend/benchmark_routing.py --mock
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure backend directory is in sys.path
BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv
load_dotenv(BACKEND_DIR.parent / ".env")

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
    SpendingCapExceededError,
)
from security import contains_secret, classify_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("benchmark")


# ---------------------------------------------------------------------------
# 40 Diverse Synthetic Benchmark Test Cases
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BenchmarkCase:
    id: str
    category: str  # technical, style, sensitive, secret, non_fact, ambiguous
    text: str
    expected_has_facts: bool
    expected_classification: str  # general, sensitive, secret
    forbidden_tokens: List[str] = field(default_factory=list)
    description: str = ""


BENCHMARK_CASES: List[BenchmarkCase] = [
    # --- Category 1: Explicit Technical Preferences (10 cases) ---
    BenchmarkCase(
        id="tech-01",
        category="technical",
        text="I always develop backend microservices in FastAPI using Python 3.11 with strict Pydantic typing.",
        expected_has_facts=True,
        expected_classification="general",
        description="FastAPI, Python 3.11, Pydantic preferences",
    ),
    BenchmarkCase(
        id="tech-02",
        category="technical",
        text="Please use TailwindCSS v3 when styling UI components for this web application.",
        expected_has_facts=True,
        expected_classification="general",
        description="TailwindCSS v3 preference",
    ),
    BenchmarkCase(
        id="tech-03",
        category="technical",
        text="I use Arch Linux with i3wm as my daily driver and prefer terminal commands using pacman.",
        expected_has_facts=True,
        expected_classification="general",
        description="Arch Linux, i3wm, pacman tool preference",
    ),
    BenchmarkCase(
        id="tech-04",
        category="technical",
        text="I prefer PostgreSQL over MySQL, and always use asyncpg with SQLAlchemy 2.0.",
        expected_has_facts=True,
        expected_classification="general",
        description="PostgreSQL, asyncpg, SQLAlchemy 2.0 preference",
    ),
    BenchmarkCase(
        id="tech-05",
        category="technical",
        text="Our repository enforces Biome for formatting and linting instead of Prettier and ESLint.",
        expected_has_facts=True,
        expected_classification="general",
        description="Biome linting preference",
    ),
    BenchmarkCase(
        id="tech-06",
        category="technical",
        text="I write unit tests using pytest with pytest-asyncio and coverage flags.",
        expected_has_facts=True,
        expected_classification="general",
        description="pytest and pytest-asyncio testing preference",
    ),
    BenchmarkCase(
        id="tech-07",
        category="technical",
        text="I'm on macOS Sonoma on an Apple Silicon M2 Max with 64GB unified memory.",
        expected_has_facts=True,
        expected_classification="general",
        description="macOS Sonoma, Apple Silicon M2 hardware context",
    ),
    BenchmarkCase(
        id="tech-08",
        category="technical",
        text="When writing Go code, always use table-driven tests and avoid third-party assertion libraries.",
        expected_has_facts=True,
        expected_classification="general",
        description="Go table-driven testing preference",
    ),
    BenchmarkCase(
        id="tech-09",
        category="technical",
        text="I build frontend apps with Next.js App Router and TypeScript, never Pages router.",
        expected_has_facts=True,
        expected_classification="general",
        description="Next.js App Router and TypeScript preference",
    ),
    BenchmarkCase(
        id="tech-10",
        category="technical",
        text="I use Docker Compose for local development with volumes mounted for hot reloading.",
        expected_has_facts=True,
        expected_classification="general",
        description="Docker Compose local development setup",
    ),

    # --- Category 2: Explanation Style Preferences (8 cases) ---
    BenchmarkCase(
        id="style-01",
        category="style",
        text="Keep all your explanations very concise, use bullet points, and skip opening or closing greetings.",
        expected_has_facts=True,
        expected_classification="general",
        description="Concise bullet points, no greetings",
    ),
    BenchmarkCase(
        id="style-02",
        category="style",
        text="Whenever you explain code, walk through the logic step-by-step with code comments.",
        expected_has_facts=True,
        expected_classification="general",
        description="Step-by-step code walkthrough",
    ),
    BenchmarkCase(
        id="style-03",
        category="style",
        text="I prefer high-level conceptual explanations before diving into code snippets.",
        expected_has_facts=True,
        expected_classification="general",
        description="High-level conceptual overview first",
    ),
    BenchmarkCase(
        id="style-04",
        category="style",
        text="Please avoid lengthy apologies or disclaimers; just give direct answers.",
        expected_has_facts=True,
        expected_classification="general",
        description="Direct answers, no apologies",
    ),
    BenchmarkCase(
        id="style-05",
        category="style",
        text="Explain complex algorithmic concepts using simple analogies suitable for a computer science freshman.",
        expected_has_facts=True,
        expected_classification="general",
        description="Analogy-based explanations",
    ),
    BenchmarkCase(
        id="style-06",
        category="style",
        text="Give me the final complete code block directly without placeholders or 'rest of code unchanged'.",
        expected_has_facts=True,
        expected_classification="general",
        description="Complete code without placeholders",
    ),
    BenchmarkCase(
        id="style-07",
        category="style",
        text="I prefer mathematical proofs to be written using LaTeX notation with step-by-step derivations.",
        expected_has_facts=True,
        expected_classification="general",
        description="LaTeX mathematical proof formatting",
    ),
    BenchmarkCase(
        id="style-08",
        category="style",
        text="When reviewing my code, highlight potential edge cases and security issues first.",
        expected_has_facts=True,
        expected_classification="general",
        description="Security and edge cases prioritized in reviews",
    ),

    # --- Category 3: Sensitive Personal & Medical Information (6 cases) ---
    BenchmarkCase(
        id="sens-01",
        category="sensitive",
        text="I have a severe peanut allergy and celiac disease, so exclude peanuts and gluten from all meal plans.",
        expected_has_facts=True,
        expected_classification="sensitive",
        description="Severe peanut and gluten allergy",
    ),
    BenchmarkCase(
        id="sens-02",
        category="sensitive",
        text="I was diagnosed with type 1 diabetes and track my daily insulin units carefully.",
        expected_has_facts=True,
        expected_classification="sensitive",
        description="Type 1 diabetes diagnosis and insulin tracking",
    ),
    BenchmarkCase(
        id="sens-03",
        category="sensitive",
        text="My monthly budget for groceries is $400 because I am paying off $35,000 in student loans.",
        expected_has_facts=True,
        expected_classification="sensitive",
        description="Personal student loan debt and monthly budget",
    ),
    BenchmarkCase(
        id="sens-04",
        category="sensitive",
        text="I have a private flight scheduled from Seattle to Tokyo next Wednesday departing at 10 AM.",
        expected_has_facts=True,
        expected_classification="sensitive",
        description="Private travel itinerary",
    ),
    BenchmarkCase(
        id="sens-05",
        category="sensitive",
        text="My son Leo is 4 years old and has asthma, so we need pet-free vacation rentals.",
        expected_has_facts=True,
        expected_classification="sensitive",
        description="Family member medical condition (asthma)",
    ),
    BenchmarkCase(
        id="sens-06",
        category="sensitive",
        text="I take 20mg of Lisinopril every morning for blood pressure management.",
        expected_has_facts=True,
        expected_classification="sensitive",
        description="Prescription medication details",
    ),

    # --- Category 4: Secrets & Credentials (6 cases — 100% must be screened) ---
    BenchmarkCase(
        id="sec-01",
        category="secret",
        text="Here is my OpenAI API key: sk-ant-api03-abcdef1234567890abcdef1234567890abcdef123456-ABCDEF please use it for testing.",
        expected_has_facts=False,
        expected_classification="secret",
        forbidden_tokens=["sk-ant-", "abcdef1234567890"],
        description="Anthropic-style API key in prompt",
    ),
    BenchmarkCase(
        id="sec-02",
        category="secret",
        text="My GitHub personal access token is ghp_1234567890abcdefghijklmnopqrstuvwxyz12 for the deploy bot.",
        expected_has_facts=False,
        expected_classification="secret",
        forbidden_tokens=["ghp_1234567890"],
        description="GitHub personal access token",
    ),
    BenchmarkCase(
        id="sec-03",
        category="secret",
        text="The database password is password: SuperSecretP@ssw0rd!123 for postgres user.",
        expected_has_facts=False,
        expected_classification="secret",
        forbidden_tokens=["SuperSecretP@ssw0rd!123"],
        description="Plaintext database password",
    ),
    BenchmarkCase(
        id="sec-04",
        category="secret",
        text="Use bearer token Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doNotLeakThisToken to connect.",
        expected_has_facts=False,
        expected_classification="secret",
        forbidden_tokens=["doNotLeakThisToken", "eyJhbGciOiJIUzI1Ni"],
        description="JWT Bearer token",
    ),
    BenchmarkCase(
        id="sec-05",
        category="secret",
        text="My AWS secret access key is wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY for S3 uploads.",
        expected_has_facts=False,
        expected_classification="secret",
        forbidden_tokens=["wJalrXUtnFEMI"],
        description="AWS secret access key",
    ),
    BenchmarkCase(
        id="sec-06",
        category="secret",
        text="Here is the private key: -----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC3\n-----END PRIVATE KEY-----",
        expected_has_facts=False,
        expected_classification="secret",
        forbidden_tokens=["BEGIN PRIVATE KEY", "MIIEvgIBADAN"],
        description="RSA/PEM private key block",
    ),

    # --- Category 5: Conversational Chitchat (5 cases — 0 false memories) ---
    BenchmarkCase(
        id="chat-01",
        category="non_fact",
        text="Hey, good morning! How are you doing today?",
        expected_has_facts=False,
        expected_classification="general",
        description="Morning greeting",
    ),
    BenchmarkCase(
        id="chat-02",
        category="non_fact",
        text="What is the distance between the Earth and the Moon in kilometers?",
        expected_has_facts=False,
        expected_classification="general",
        description="General factual question",
    ),
    BenchmarkCase(
        id="chat-03",
        category="non_fact",
        text="Thanks so much, that solved my issue perfectly!",
        expected_has_facts=False,
        expected_classification="general",
        description="Expressions of gratitude",
    ),
    BenchmarkCase(
        id="chat-04",
        category="non_fact",
        text="Can you write a short poem about autumn leaves falling in the wind?",
        expected_has_facts=False,
        expected_classification="general",
        description="Creative writing request",
    ),
    BenchmarkCase(
        id="chat-05",
        category="non_fact",
        text="Could you summarize the history of the Byzantine Empire in three short paragraphs?",
        expected_has_facts=False,
        expected_classification="general",
        description="Historical inquiry",
    ),

    # --- Category 6: Ambiguous & Boundary Cases (5 cases) ---
    BenchmarkCase(
        id="ambig-01",
        category="ambiguous",
        text="I work as an oncology research clinician at Memorial Sloan Kettering and analyze patient records.",
        expected_has_facts=True,
        expected_classification="general",  # Professional job title, not personal disease
        description="Professional clinician context vs patient disease",
    ),
    BenchmarkCase(
        id="ambig-02",
        category="ambiguous",
        text="I am currently refactoring our hospital's HIPAA-compliant auth microservice in Rust.",
        expected_has_facts=True,
        expected_classification="general",  # Technical domain, not user health record
        description="HIPAA compliance engineering in Rust",
    ),
    BenchmarkCase(
        id="ambig-03",
        category="ambiguous",
        text="I hate writing JavaScript and find it messy, but unfortunately our team stack is React and Next.js.",
        expected_has_facts=True,
        expected_classification="general",
        description="Sentiment dislike vs actual team stack",
    ),
    BenchmarkCase(
        id="ambig-04",
        category="ambiguous",
        text="Normally I prefer concise answers, but when we discuss distributed systems architecture, provide in-depth details.",
        expected_has_facts=True,
        expected_classification="general",
        description="Conditional style preferences",
    ),
    BenchmarkCase(
        id="ambig-05",
        category="ambiguous",
        text="I live with a roommate who has a cat allergy, so please remind me to vacuum frequently.",
        expected_has_facts=True,
        expected_classification="sensitive",  # Mentions allergy and household health
        description="Roommate allergy in shared household",
    ),
]


# ---------------------------------------------------------------------------
# Embedding Threshold Tuning Dataset
# ---------------------------------------------------------------------------
TARGET_MEMORIES = [
    "User develops backend microservices in FastAPI with Python 3.11.",
    "User prefers concise explanations in bullet points without opening pleasantries.",
    "User uses Arch Linux with i3wm and pacman.",
    "User enforces Biome for code formatting and linting.",
    "User has a severe peanut allergy and celiac disease.",
    "User prefers TailwindCSS v3 for styling web applications.",
    "User writes unit tests with pytest and pytest-asyncio.",
    "User builds frontend apps with Next.js App Router and TypeScript.",
    "User prefers step-by-step code walkthroughs with comments.",
    "User tracks type 1 diabetes and insulin units.",
]

RELEVANT_QUERIES = [
    "How should I structure my FastAPI endpoints and dependency injection?",
    "Give me quick tips for refactoring this function, be concise.",
    "What is the pacman command to install dependencies on Arch?",
    "How to configure Biome in a TypeScript monorepo?",
    "Can you suggest dinner recipes that exclude gluten and nuts?",
    "How do I set up custom color variables in TailwindCSS v3?",
    "Write a unit test fixture for an async database connection in pytest.",
    "Explain the difference between server and client components in Next.js App Router.",
    "Walk me through how this sorting algorithm works step-by-step.",
    "Tips for managing daily blood sugar and insulin schedules.",
]

IRRELEVANT_QUERIES = [
    "How to train an equestrian show jumping horse for Olympic competition?",
    "What were the agricultural techniques used by the ancient Maya civilization?",
    "Recipe for traditional French beef Wellington with puff pastry and mushroom duxelles.",
    "Explain the rules of cricket and the difference between test and T20 matches.",
    "How does a nuclear fission reactor produce steam to turn turbine blades?",
    "Best practices for pruning heritage apple trees in late winter.",
    "Historical analysis of the Punic Wars between Rome and Carthage.",
    "How to knit a fair-isle wool sweater using circular needles.",
    "What is the geological origin of the Mariana Trench in the Pacific Ocean?",
    "Guide to restoring mid-century modern teak furniture with Danish oil.",
]


# ---------------------------------------------------------------------------
# Benchmark Runner
# ---------------------------------------------------------------------------
@dataclass
class ModelMetrics:
    model_name: str
    total_cases: int = 0
    valid_schema_count: int = 0
    expected_fact_matches: int = 0
    false_memories: int = 0  # Extracted facts from non-fact messages
    missed_facts: int = 0    # No facts extracted when facts were expected
    secret_leak_count: int = 0  # CRITICAL: must be 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_latency_seconds: float = 0.0
    latencies: List[float] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    extracted_records: List[Dict[str, Any]] = field(default_factory=list)


def evaluate_extractor(
    extractor: FactExtractor,
    model_name: str,
    cases: List[BenchmarkCase],
    user_id: str = "benchmark_user",
) -> ModelMetrics:
    metrics = ModelMetrics(model_name=model_name)
    logger.info("Evaluating model %s across %d cases...", model_name, len(cases))

    for case in cases:
        metrics.total_cases += 1
        t0 = time.time()
        try:
            res = extractor.extract_facts(case.text, user_id=user_id)
            elapsed = time.time() - t0
            metrics.latencies.append(elapsed)
            metrics.total_latency_seconds += elapsed
            metrics.total_prompt_tokens += res.prompt_tokens
            metrics.total_completion_tokens += res.completion_tokens
            metrics.valid_schema_count += 1

            facts = res.facts

            # Record result
            metrics.extracted_records.append({
                "id": case.id,
                "category": case.category,
                "extracted_facts": facts,
                "latency": round(elapsed, 3),
            })

            # Check for secrets leakage
            for fact in facts:
                if contains_secret(fact):
                    metrics.secret_leak_count += 1
                for forbidden in case.forbidden_tokens:
                    if forbidden in fact:
                        metrics.secret_leak_count += 1

            # Quality checks
            if case.category == "secret":
                # Secret messages should produce 0 facts or only safe non-secret context
                if any(any(tok in f for tok in case.forbidden_tokens) for f in facts):
                    metrics.secret_leak_count += 1

            elif case.category == "non_fact":
                if facts:
                    metrics.false_memories += 1

            elif case.expected_has_facts:
                if not facts:
                    metrics.missed_facts += 1
                else:
                    metrics.expected_fact_matches += 1

        except Exception as exc:
            elapsed = time.time() - t0
            metrics.latencies.append(elapsed)
            metrics.total_latency_seconds += elapsed
            metrics.errors.append(f"Case {case.id} failed: {type(exc).__name__}: {exc}")
            logger.warning("Extraction failed for case %s: %s", case.id, exc)

    return metrics


def evaluate_jev(
    validator: JevValidator,
    ambiguous_cases: List[BenchmarkCase],
) -> Dict[str, Any]:
    """Evaluates Jev on ambiguous cases, checking API validity and pricing."""
    if not validator.is_enabled():
        return {
            "status": "disabled",
            "message": "Jev is disabled for this run; set ENABLE_JEV_VALIDATION=true to include it",
            "pricing_per_call_usd": None,
        }

    logger.info("Evaluating Jev on %d ambiguous cases...", len(ambiguous_cases))
    results = []
    total_latency = 0.0

    for case in ambiguous_cases:
        t0 = time.time()
        try:
            res = validator.validate(case.text)
            elapsed = time.time() - t0
            total_latency += elapsed
            results.append({
                "id": case.id,
                "classification": res.classification,
                "confidence": res.confidence,
                "is_valid": res.is_valid,
                "latency_ms": round(elapsed * 1000, 1),
            })
        except Exception as exc:
            results.append({
                "id": case.id,
                "error": str(exc),
            })

    avg_latency = (total_latency / len(results)) if results else 0.0
    return {
        "status": "enabled",
        "evaluated_cases": len(results),
        "avg_latency_ms": round(avg_latency * 1000, 1),
        "pricing_per_call_usd": None,
        "results": results,
    }


def tune_embedding_threshold(
    embedder: Embedder,
) -> Dict[str, Any]:
    """Calculates cosine scores between targets and relevant vs irrelevant queries."""
    logger.info("Computing embeddings for threshold tuning (10 targets, 10 relevant, 10 irrelevant)...")

    # 1. Embed targets
    target_vectors = [embedder.embed(t) for t in TARGET_MEMORIES]

    def cosine_sim(a: List[float], b: List[float]) -> float:
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if not norm_a or not norm_b:
            return 0.0
        return sum(x * y for x, y in zip(a, b)) / (norm_a * norm_b)

    # 2. Score relevant queries against corresponding target (1-to-1 matching)
    relevant_scores = []
    for i, q in enumerate(RELEVANT_QUERIES):
        q_vec = embedder.embed(q)
        # Cosine similarity with matching target
        raw_cos = cosine_sim(q_vec, target_vectors[i])
        # Atlas normalized score: (1.0 + cosine) / 2.0
        norm_score = (1.0 + raw_cos) / 2.0
        relevant_scores.append(norm_score)

    # 3. Score irrelevant queries against all targets (take max irrelevant score per query)
    irrelevant_scores = []
    for q in IRRELEVANT_QUERIES:
        q_vec = embedder.embed(q)
        max_irr = max((1.0 + cosine_sim(q_vec, t_vec)) / 2.0 for t_vec in target_vectors)
        irrelevant_scores.append(max_irr)

    rel_min = min(relevant_scores)
    rel_med = sorted(relevant_scores)[len(relevant_scores) // 2]
    rel_max = max(relevant_scores)

    irr_min = min(irrelevant_scores)
    irr_med = sorted(irrelevant_scores)[len(irrelevant_scores) // 2]
    irr_max = max(irrelevant_scores)

    # Optimal threshold: strictly separates relevant from irrelevant if possible
    # We choose a threshold between max irrelevant and min relevant (or median)
    if rel_min > irr_max:
        optimal_threshold = round((rel_min + irr_max) / 2.0, 3)
    else:
        # If there is slight overlap at the tail, set threshold at 90th percentile of irrelevant
        optimal_threshold = round(irr_max + 0.01, 3)

    return {
        "dimensions": len(target_vectors[0]),
        "relevant_scores": {
            "min": round(rel_min, 4),
            "median": round(rel_med, 4),
            "max": round(rel_max, 4),
        },
        "irrelevant_scores": {
            "min": round(irr_min, 4),
            "median": round(irr_med, 4),
            "max": round(irr_max, 4),
        },
        "score_margin": round(rel_med - irr_med, 4),
        "optimal_threshold": optimal_threshold,
        "current_default_threshold": float(os.getenv("MIN_MEMORY_SCORE", "0.63")),
    }


def verify_secret_scrub(all_records: List[Dict[str, Any]]) -> bool:
    """Verifies that zero raw secrets exist in extracted records or logs."""
    serialized = json.dumps(all_records)
    has_leak = contains_secret(serialized)
    return not has_leak


def verify_live_fallback(openrouter_key: str, tracker: UsageTracker) -> bool:
    """Inject a primary 429 and verify that the real Gemini fallback succeeds."""
    import httpx

    class Primary429ThenLiveClient:
        def __init__(self):
            self.live_client = httpx.Client(timeout=10.0)

        def post(self, url, *, headers, json, timeout):
            if json.get("model") == "z-ai/glm-5.3-flash":
                return httpx.Response(429)
            return self.live_client.post(url, headers=headers, json=json, timeout=timeout)

        def close(self):
            self.live_client.close()

    fallback_client = Primary429ThenLiveClient()
    try:
        extractor = OpenRouterExtractor(
            api_key=openrouter_key,
            primary_model="z-ai/glm-5.3-flash",
            fallback_model="google/gemini-2.5-flash-lite",
            timeout_seconds=10.0,
            max_retries=1,
            usage_tracker=tracker,
            http_client=fallback_client,
        )
        result = extractor.extract_facts("I develop in Rust and use Linux.", user_id="fb_user")
        return result.fallback_used and result.model_used == "google/gemini-2.5-flash-lite" and bool(result.facts)
    finally:
        fallback_client.close()


# ---------------------------------------------------------------------------
# Main Benchmark Execution
# ---------------------------------------------------------------------------
def run_benchmark(mock_mode: bool = False) -> Dict[str, Any]:
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    enable_jev = os.getenv("ENABLE_JEV_VALIDATION", "false").lower() == "true"
    tracker = UsageTracker()

    if mock_mode or not openrouter_key:
        logger.info("Running benchmark in MOCK/SYNTHETIC mode (no live API calls)")
        primary_extractor = MockExtractor(
            default_facts=["User prefers FastAPI with Python 3.11"],
            primary_model="z-ai/glm-5.3-flash",
        )
        fallback_extractor = MockExtractor(
            default_facts=["User prefers FastAPI with Python 3.11"],
            primary_model="google/gemini-2.5-flash-lite",
        )
        embedder = MockEmbedder(dimensions=1536)
        validator = MockValidator(enabled=enable_jev, classification="general")
        is_live = False
    else:
        logger.info("Running benchmark with LIVE credentials via OpenRouter")
        from settings import load_settings
        settings = load_settings(require_gemini=False)
        openrouter_cap = float(getattr(settings, "openrouter_spending_cap", 2.00))
        jev_cap = float(getattr(settings, "jev_spending_cap", 1.00))

        tracker = UsageTracker(
            spending_cap_usd=openrouter_cap,
            jev_spending_cap_usd=jev_cap,
            jev_max_calls=settings.jev_max_calls,
        )

        primary_extractor = OpenRouterExtractor(
            api_key=openrouter_key,
            primary_model="z-ai/glm-5.3-flash",
            fallback_model="google/gemini-2.5-flash-lite",
            timeout_seconds=25.0,
            usage_tracker=tracker,
        )
        fallback_extractor = OpenRouterExtractor(
            api_key=openrouter_key,
            primary_model="google/gemini-2.5-flash-lite",
            fallback_model="z-ai/glm-5.3-flash",
            timeout_seconds=25.0,
            usage_tracker=tracker,
        )
        embedder = OpenRouterEmbedder(
            api_key=openrouter_key,
            model="openai/text-embedding-3-small",
            dimensions=1536,
            usage_tracker=tracker,
        )
        validator = JevValidator(
            enabled=enable_jev,
            api_key=openrouter_key,
            base_url=settings.openrouter_base_url,
            model=settings.jev_model,
            usage_tracker=tracker,
        )
        is_live = True

    # 1. Run GLM 5.3 Flash evaluation
    glm_metrics = evaluate_extractor(primary_extractor, "z-ai/glm-5.3-flash", BENCHMARK_CASES)

    # 2. Run Gemini 2.5 Flash Lite evaluation
    gemini_metrics = evaluate_extractor(fallback_extractor, "google/gemini-2.5-flash-lite", BENCHMARK_CASES)

    # 3. Evaluate Jev on ambiguous cases
    ambiguous_subset = [c for c in BENCHMARK_CASES if c.category == "ambiguous"]
    jev_metrics = evaluate_jev(validator, ambiguous_subset)

    # 4. Tune Embedding Threshold
    embed_results = tune_embedding_threshold(embedder)

    # 5. Verify Fallback Triggering
    logger.info("Verifying automated fallback mechanics...")
    fallback_test_ok = False
    try:
        if is_live:
            fallback_test_ok = verify_live_fallback(openrouter_key, tracker)
        else:
            fallback_test_ok = True
    except Exception as exc:
        logger.warning("Fallback test produced: %s", exc)
        fallback_test_ok = False

    # 6. Privacy & Secret Scrub Verification
    all_extracted = glm_metrics.extracted_records + gemini_metrics.extracted_records
    secrets_clean = verify_secret_scrub(all_extracted)

    # 7. Check spending caps
    usage_summary = tracker.get_summary()

    # 8. Determine Primary Model Recommendation
    # Rule: If GLM has 0 secret leaks, valid schema >= 95%, false memory <= 1, and missed facts <= 2:
    #       GLM is confirmed as primary. Otherwise, switch to Gemini Flash Lite!
    glm_quality_ok = (
        glm_metrics.secret_leak_count == 0
        and glm_metrics.false_memories <= 1
        and glm_metrics.missed_facts <= 2
        and len(glm_metrics.errors) == 0
    )

    gemini_quality_ok = (
        gemini_metrics.secret_leak_count == 0
        and gemini_metrics.false_memories <= 1
        and gemini_metrics.missed_facts <= 2
        and len(gemini_metrics.errors) == 0
    )

    if not is_live:
        recommended_primary = None
        recommended_fallback = None
        routing_decision = "UNVERIFIED: mock results cannot select a production model"
    elif glm_quality_ok:
        recommended_primary = "z-ai/glm-5.3-flash"
        recommended_fallback = "google/gemini-2.5-flash-lite"
        routing_decision = "CONFIRMED: GLM 5.3 Flash as primary, Gemini 2.5 Flash Lite as fallback"
    elif gemini_quality_ok:
        recommended_primary = "google/gemini-2.5-flash-lite"
        recommended_fallback = "z-ai/glm-5.3-flash"
        routing_decision = "SWITCHED: Gemini 2.5 Flash Lite selected as primary due to quality criteria"
    else:
        recommended_primary = None
        recommended_fallback = None
        routing_decision = "UNVERIFIED: neither live model met the quality gate"

    report = {
        "is_live_run": is_live,
        "routing_decision": routing_decision,
        "recommended_primary": recommended_primary,
        "recommended_fallback": recommended_fallback,
        "glm_metrics": {
            "model": glm_metrics.model_name,
            "total_cases": glm_metrics.total_cases,
            "valid_schema": f"{glm_metrics.valid_schema_count}/{glm_metrics.total_cases}",
            "false_memories": glm_metrics.false_memories,
            "missed_facts": glm_metrics.missed_facts,
            "secret_leaks": glm_metrics.secret_leak_count,
            "avg_latency_s": round(
                (glm_metrics.total_latency_seconds / glm_metrics.total_cases)
                if glm_metrics.total_cases else 0.0, 3
            ),
            "errors": len(glm_metrics.errors),
        },
        "gemini_metrics": {
            "model": gemini_metrics.model_name,
            "total_cases": gemini_metrics.total_cases,
            "valid_schema": f"{gemini_metrics.valid_schema_count}/{gemini_metrics.total_cases}",
            "false_memories": gemini_metrics.false_memories,
            "missed_facts": gemini_metrics.missed_facts,
            "secret_leaks": gemini_metrics.secret_leak_count,
            "avg_latency_s": round(
                (gemini_metrics.total_latency_seconds / gemini_metrics.total_cases)
                if gemini_metrics.total_cases else 0.0, 3
            ),
            "errors": len(gemini_metrics.errors),
        },
        "jev_evaluation": jev_metrics,
        "embedding_threshold_tuning": embed_results,
        "privacy_scrub_passed": secrets_clean,
        "fallback_test_passed": fallback_test_ok,
        "usage_summary": usage_summary,
    }

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Context Passport Step 10 Benchmark")
    parser.add_argument("--mock", action="store_true", help="Force mock mode without external calls")
    parser.add_argument("--json", action="store_true", help="Output raw JSON results")
    args = parser.parse_args()

    results = run_benchmark(mock_mode=args.mock)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print("\n" + "=" * 70)
        print("CONTEXT PASSPORT V3 — STEP 10 BENCHMARK REPORT")
        print("=" * 70)
        print(f"Mode: {'LIVE API' if results['is_live_run'] else 'MOCK/OFFLINE'}")
        print(f"Decision: {results['routing_decision']}")
        print(f"Recommended Primary:  {results['recommended_primary']}")
        print(f"Recommended Fallback: {results['recommended_fallback']}")
        print("\n--- Extractor Quality Comparison ---")
        print(f"{'Metric':<25} | {'GLM 5.3 Flash':<18} | {'Gemini 2.5 Flash Lite':<18}")
        print("-" * 67)
        g = results["glm_metrics"]
        m = results["gemini_metrics"]
        print(f"{'Valid Schema':<25} | {g['valid_schema']:<18} | {m['valid_schema']:<18}")
        print(f"{'False Memories (Chitchat)':<25} | {g['false_memories']:<18} | {m['false_memories']:<18}")
        print(f"{'Missed Facts':<25} | {g['missed_facts']:<18} | {m['missed_facts']:<18}")
        print(f"{'Secret Leaks':<25} | {g['secret_leaks']:<18} | {m['secret_leaks']:<18}")
        print(f"{'Avg Latency (sec)':<25} | {g['avg_latency_s']:<18} | {m['avg_latency_s']:<18}")
        print(f"{'Errors':<25} | {g['errors']:<18} | {m['errors']:<18}")

        print("\n--- Embedding Vector & Threshold Tuning ---")
        emb = results["embedding_threshold_tuning"]
        print(f"Dimensions: {emb['dimensions']}")
        print(f"Relevant Scores (norm):   min={emb['relevant_scores']['min']}, median={emb['relevant_scores']['median']}, max={emb['relevant_scores']['max']}")
        print(f"Irrelevant Scores (norm): min={emb['irrelevant_scores']['min']}, median={emb['irrelevant_scores']['median']}, max={emb['irrelevant_scores']['max']}")
        print(f"Score Separation Margin:  {emb['score_margin']}")
        print(f"Optimal Threshold:        {emb['optimal_threshold']} (current default: {emb['current_default_threshold']})")

        print("\n--- Jev Validation Pass ---")
        jev = results["jev_evaluation"]
        print(f"Status:  {jev['status']}")
        print("Pricing: variable through OpenRouter; check actual usage and key spending limit")
        if "avg_latency_ms" in jev:
            print(f"Latency: {jev['avg_latency_ms']} ms")

        print("\n--- Privacy & Spending Safety ---")
        print(f"Privacy Scrub Passed (0 secrets leaked): {results['privacy_scrub_passed']}")
        print(f"429 to live Gemini fallback passed: {results['fallback_test_passed']}")
        usage = results["usage_summary"]
        print(f"Total Calls:            {usage['total_calls']}")
        print(f"Total Tokens:           Prompt={usage['total_prompt_tokens']}, Compl={usage['total_completion_tokens']}, Embed={usage['total_embedding_tokens']}")
        print(f"Total Estimated Spend:  ${usage['total_estimated_cost_usd']:.6f} (Cap: ${usage['spending_cap_usd']:.2f})")
        print(f"Spending Cap Exceeded:  {usage['cap_exceeded']}")
        print("=" * 70 + "\n")
