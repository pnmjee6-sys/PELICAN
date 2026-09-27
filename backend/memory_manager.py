"""Memory manager and tenant-safe facade around Mem0 OSS and MongoDB Atlas."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

# Mem0 telemetry creates an extra Atlas vector collection/index and sends
# lifecycle events. Neither is needed for this privacy-first prototype.
os.environ.setdefault("MEM0_TELEMETRY", "false")

from mem0 import Memory
from mem0.utils.factory import VectorStoreFactory
from pymongo import MongoClient

from security import classify_text, contains_secret, redacted_evidence_excerpt
from settings import Settings, load_settings

logger = logging.getLogger(__name__)
# Calibrated on the 2026-09-26 live 1536-dimension OpenRouter embedding benchmark:
# relevant scores 0.6706-0.8646, maximum irrelevant score 0.5974.
MIN_MEMORY_SCORE = float(os.getenv("MIN_MEMORY_SCORE", "0.63"))

_AMBIGUOUS_MEMORY_PHRASES = (
    "maybe", "might", "perhaps", "probably", "for now", "for this task",
    "for this page", "for this project", "this time", "just today", "tomorrow",
    "next week", "temporarily", "one time", "once", "i think",
    "uncertain", "not sure", "unconfirmed", "tentative", "provisional", "speculative",
)


def needs_jev_review(source_text: str, candidate: str) -> bool:
    """Review uncertain or task-scoped facts before they become durable memory."""
    combined = f"{source_text} {candidate}".lower()
    return "?" in source_text or any(phrase in combined for phrase in _AMBIGUOUS_MEMORY_PHRASES)


def generate_deterministic_embedding(text: str, dims: int = 1536) -> List[float]:
    """Generates a deterministic unit-normalized 1536-dimensional vector for offline test suites only."""
    clean_text = text.lower().strip()
    words = re.findall(r"\b\w+\b", clean_text)
    vector = [0.0] * dims
    stopwords = {
        "is", "my", "the", "a", "an", "and", "or", "in", "on", "at", "to",
        "for", "of", "with", "it", "this", "can", "you", "please", "what",
        "which", "how", "do", "i", "me", "are", "be", "so", "that"
    }

    for word in words:
        weight = 0.1 if word in stopwords else 1.0
        h = int(hashlib.sha256(word.encode("utf-8")).hexdigest(), 16)
        for seed in range(5):
            idx = (h >> (seed * 8)) % dims
            vector[idx] += weight * (1.0 / (seed + 1))

        if len(word) >= 3 and word not in stopwords:
            for i in range(len(word) - 2):
                ngram = word[i : i + 3]
                nh = int(hashlib.md5(ngram.encode("utf-8")).hexdigest(), 16)
                idx = nh % dims
                vector[idx] += 0.3

    norm = math.sqrt(sum(x * x for x in vector))
    if norm > 0:
        return [x / norm for x in vector]
    vector[0] = 1.0
    return vector


def build_mem0_config(settings: Any) -> dict[str, Any]:
    dims = getattr(settings, "embedding_dims", getattr(settings, "gemini_embedding_dims", 1536))
    config: dict[str, Any] = {
        "version": "v1.1",
        "history_db_path": getattr(settings, "history_db_path", "history.db"),
        "vector_store": {
            "provider": "mongodb",
            "config": {
                "mongo_uri": getattr(settings, "mongodb_uri", "mongodb://localhost:27017"),
                "db_name": getattr(settings, "mongodb_db_name", "mem0"),
                "collection_name": getattr(settings, "mongodb_collection_name", "memories"),
                "embedding_model_dims": dims,
            },
        },
    }

    provider = getattr(settings, "llm_provider", "openrouter")
    openrouter_api_key = getattr(settings, "openrouter_api_key", None)
    gemini_api_key = getattr(settings, "gemini_api_key", None)

    if provider == "openrouter" and openrouter_api_key:
        config["llm"] = {
            "provider": "openai",
            "config": {
                "api_key": openrouter_api_key,
                "model": getattr(settings, "extraction_primary_model", "z-ai/glm-5.3-flash"),
                "openai_base_url": getattr(settings, "openrouter_base_url", "https://openrouter.ai/api/v1"),
                "temperature": 0,
            },
        }
        config["embedder"] = {
            "provider": "openai",
            "config": {
                "api_key": openrouter_api_key,
                "model": getattr(settings, "embedding_model", "openai/text-embedding-3-small"),
                "openai_base_url": getattr(settings, "openrouter_base_url", "https://openrouter.ai/api/v1"),
                "embedding_dims": dims,
            },
        }
    elif gemini_api_key:
        config["llm"] = {
            "provider": "gemini",
            "config": {
                "api_key": gemini_api_key,
                "model": getattr(settings, "gemini_extraction_model", "gemini-2.5-flash"),
                "temperature": 0,
            },
        }
        config["embedder"] = {
            "provider": "gemini",
            "config": {
                "api_key": gemini_api_key,
                "model": getattr(settings, "gemini_embedding_model", "models/gemini-embedding-001"),
                "embedding_dims": getattr(settings, "gemini_embedding_dims", dims),
            },
        }
    return config


class MemoryManager:
    """Tenant-safe facade around Mem0 OSS and MongoDB Atlas vector storage."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        extractor: Optional[Any] = None,
        embedder: Optional[Any] = None,
        validator: Optional[Any] = None,
    ):
        live_integration = os.getenv("RUN_INTEGRATION_TESTS", "false").lower() == "true"
        allow_offline = os.getenv("ALLOW_OFFLINE_EMBEDDINGS", "false").lower() == "true" and not live_integration
        self._allow_offline = allow_offline
        self.settings = settings or load_settings(require_gemini=not allow_offline and os.getenv("LLM_PROVIDER") == "gemini")
        VectorStoreFactory.provider_to_class["mongodb"] = "scoped_mongodb.ScopedMongoDB"
        self.mongo_client = MongoClient(self.settings.mongodb_uri)
        self.db = self.mongo_client[self.settings.mongodb_db_name]
        self.collection = self.db[self.settings.mongodb_collection_name]
        self.forgotten_sources = self.db["forgotten_memory_sources"]

        # Attached provider layer instances
        self.extractor = extractor
        self.embedder = embedder
        self.validator = validator

        # Enforce required keys in real production mode
        has_openrouter = bool(getattr(self.settings, "openrouter_api_key", None))
        has_gemini = bool(getattr(self.settings, "gemini_api_key", None))
        provider = getattr(self.settings, "llm_provider", "openrouter")
        if not self._allow_offline:
            if provider == "openrouter" and not has_openrouter:
                raise RuntimeError("OPENROUTER_API_KEY is required in real mode. Silent fallback embeddings are disabled.")
            elif provider == "gemini" and not has_gemini:
                raise RuntimeError("GEMINI_API_KEY is required in real V1 mode. Silent fallback embeddings are disabled.")

        self._genai_client = None
        self._openrouter_embedder = None

        if self._allow_offline or (os.getenv("APP_ENV", "").lower() == "test" and not live_integration):
            from providers import create_extractor, create_embedder, create_validator
            if self.extractor is None:
                self.extractor = create_extractor(self.settings)
            if self.embedder is None:
                self.embedder = create_embedder(self.settings)
            if self.validator is None:
                self.validator = create_validator(self.settings)
            self.memory = None
        elif provider == "openrouter" and has_openrouter:
            self.memory = Memory.from_config(build_mem0_config(self.settings))
            from providers import OpenRouterEmbedder, OpenRouterExtractor
            if self.embedder is None:
                self._openrouter_embedder = OpenRouterEmbedder(
                    api_key=getattr(self.settings, "openrouter_api_key", None),
                    base_url=getattr(self.settings, "openrouter_base_url", "https://openrouter.ai/api/v1"),
                    model=self.active_embedding_model,
                    dimensions=getattr(self.settings, "embedding_dims", 1536),
                    timeout_seconds=getattr(self.settings, "provider_timeout_seconds", 30.0),
                )
                self.embedder = self._openrouter_embedder
            if self.extractor is None:
                self.extractor = OpenRouterExtractor(
                    api_key=getattr(self.settings, "openrouter_api_key", None),
                    base_url=getattr(self.settings, "openrouter_base_url", "https://openrouter.ai/api/v1"),
                    primary_model=getattr(self.settings, "extraction_primary_model", "z-ai/glm-5.3-flash"),
                    fallback_model=getattr(self.settings, "extraction_fallback_model", "google/gemini-2.5-flash-lite"),
                    timeout_seconds=getattr(self.settings, "provider_timeout_seconds", 30.0),
                )
        elif has_gemini:
            self.memory = Memory.from_config(build_mem0_config(self.settings))
            # Mem0 creates SDK clients without request deadlines. Reuse one
            # bounded client so a stalled provider cannot hang capture forever.
            from google import genai
            from google.genai import types

            self._genai_client = genai.Client(
                api_key=self.settings.gemini_api_key,
                http_options=types.HttpOptions(
                    timeout=30_000,
                    retry_options=types.HttpRetryOptions(attempts=2),
                ),
            )
            self.memory.llm.client = self._genai_client
            self.memory.embedding_model.client = self._genai_client
        else:
            self.memory = None

        if self.validator is None:
            from providers import create_validator
            self.validator = create_validator(self.settings)

    @property
    def active_embedding_model(self) -> str | None:
        if hasattr(self, "settings") and self.settings is not None:
            return getattr(
                self.settings,
                "embedding_model",
                getattr(self.settings, "gemini_embedding_model", None),
            )
        return None

    def embed_text(self, text: str) -> List[float]:
        """
        Generates 1536-dimensional embedding using active provider API.
        Never silently uses fallback embeddings in real mode.
        """
        embedder = getattr(self, "embedder", None)
        if embedder is not None:
            return embedder.embed(text)

        if getattr(self, "_openrouter_embedder", None) is not None:
            return self._openrouter_embedder.embed(text)

        if hasattr(self, "settings") and getattr(self.settings, "gemini_api_key", None) and getattr(self, "_genai_client", None) is not None:
            from google.genai import types

            config = types.EmbedContentConfig(output_dimensionality=self.settings.gemini_embedding_dims)
            resp = self._genai_client.models.embed_content(
                model=self.settings.gemini_embedding_model,
                contents=text,
                config=config,
            )
            if resp.embeddings and resp.embeddings[0].values:
                return list(resp.embeddings[0].values)
            raise RuntimeError("Gemini embed_content returned no embeddings")

        if getattr(self, "_allow_offline", False):
            dims = getattr(self.settings, "embedding_dims", 1536) if hasattr(self, "settings") and self.settings else 1536
            return generate_deterministic_embedding(text, dims)

        raise RuntimeError("Provider API key is required for embeddings in real mode.")

    def add(
        self,
        text: str,
        user_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Adds a memory after screening for secrets and assigning classification.
        - Extension injected memory blocks are REJECTED before saving.
        - Secrets are SKIPPED completely.
        - General facts tagged 'general'.
        - Sensitive/uncertain/allergy facts tagged 'sensitive'.
        """
        # Reject extension injected context blocks before saving
        from preference_engine import is_extension_injected_context
        if is_extension_injected_context(text):
            logger.info("MemoryManager.add rejected extension injected memory block")
            return {
                "status": "skipped",
                "reason": "extension_context_ignored",
                "results": [],
            }

        classification = classify_text(text)
        if classification == "secret":
            logger.info("Skipped memory creation: recognizable secret credential screened")
            return {
                "status": "skipped",
                "reason": "secret_credential_screened",
                "results": [],
            }

        # A provider/database failure may occur after Mem0 writes but before the
        # dedup event is marked memory_saved. On retry, reuse that event's facts.
        source_event_key = (metadata or {}).get("source_event_key")
        if source_event_key and self._source_forgotten(user_id, source_event_key):
            return {"status": "skipped", "reason": "forgotten_source", "results": []}
        if source_event_key:
            prior = list(self.collection.find({
                "payload.user_id": user_id,
                "payload.source_event_key": source_event_key,
            }))
            if prior:
                return {"results": [
                    {
                        "id": str(doc["_id"]),
                        "memory": doc.get("payload", {}).get("data") or doc.get("text", ""),
                        "classification": doc.get("payload", {}).get("classification", "sensitive"),
                        "event": "EXISTING",
                    }
                    for doc in prior
                ]}

        # If a dedicated FactExtractor is configured on the provider layer,
        # extract standalone durable facts through the provider abstraction.
        extractor = getattr(self, "extractor", None)
        if extractor is not None:
            extraction_result = extractor.extract_facts(text, user_id=user_id)
            saved_results: List[Dict[str, Any]] = []
            now_iso = datetime.now(timezone.utc).isoformat()
            validator = getattr(self, "validator", None)

            for fact_text in extraction_result.facts:
                clean_fact = fact_text.strip()
                if not clean_fact:
                    continue
                if contains_secret(clean_fact):
                    logger.info("Screened secret credential from extracted fact candidate: %s", user_id)
                    continue

                # An extractor may remove the uncertainty or health wording from
                # a candidate. Preserve the original message's privacy label.
                fact_classification = "sensitive" if classification == "sensitive" else classify_text(clean_fact)
                if needs_jev_review(text, clean_fact):
                    if validator is None or not validator.is_enabled():
                        logger.info("Skipped ambiguous memory candidate without Jev review")
                        continue
                    try:
                        v_res = validator.validate(clean_fact, context={"source_text": text})
                    except Exception as v_exc:
                        logger.warning("Jev review failed (%s); candidate skipped", type(v_exc).__name__)
                        continue
                    if not v_res.is_valid or v_res.classification not in ("general", "sensitive"):
                        logger.info("Jev rejected ambiguous memory candidate")
                        continue
                    # Jev can promote sensitivity, but rules retain the final veto.
                    if v_res.classification == "sensitive":
                        fact_classification = "sensitive"

                # Rule-based allergies and sensitive keywords always enforce sensitive
                if classify_text(clean_fact) == "sensitive":
                    fact_classification = "sensitive"

                doc_id = str(uuid.uuid4())
                embedding = self.embed_text(clean_fact)
                doc = {
                    "_id": doc_id,
                    "text": clean_fact,
                    "embedding": embedding,
                    "payload": {
                        "user_id": user_id,
                        "data": clean_fact,
                        "classification": fact_classification,
                        "status": "active",
                        "embedding_model": self.active_embedding_model,
                        "created_at": now_iso,
                        "support_excerpt": redacted_evidence_excerpt(
                            clean_fact, sensitive=fact_classification == "sensitive"
                        ),
                        "model_used": extraction_result.model_used,
                        "fallback_used": extraction_result.fallback_used,
                        **(metadata or {}),
                    },
                }
                self.collection.insert_one(doc)
                saved_results.append({
                    "id": doc_id,
                    "memory": clean_fact,
                    "event": "ADD",
                    "classification": fact_classification,
                })

            if source_event_key and self._source_forgotten(user_id, source_event_key):
                self._delete_source_memories(user_id, source_event_key)
                return {"status": "skipped", "reason": "forgotten_source", "results": []}

            return {"results": saved_results}

        # If Mem0 is available
        if self.memory:
            mem_meta = {
                "classification": classification,
                "status": "active",
                "embedding_model": self.active_embedding_model,
                "created_at": datetime.now(timezone.utc).isoformat(),
                **(metadata or {}),
            }
            res = self.memory.add(text, user_id=user_id, metadata=mem_meta)
            # Ensure classification, memory text, embedding_model, and status are stored in collection doc
            for item in res.get("results", []):
                m_id = item.get("id")
                if m_id:
                    self.collection.update_one(
                        {"_id": m_id},
                        {
                            "$set": {
                                "payload.classification": classification,
                                "payload.status": "active",
                                "payload.data": item.get("memory", text),
                                "payload.embedding_model": self.active_embedding_model,
                                "payload.support_excerpt": redacted_evidence_excerpt(
                                    item.get("memory", text), sensitive=classification == "sensitive"
                                ),
                            }
                        },
                    )
                item["classification"] = classification
            if source_event_key and self._source_forgotten(user_id, source_event_key):
                self._delete_source_memories(user_id, source_event_key)
                return {"status": "skipped", "reason": "forgotten_source", "results": []}
            return res

        # Standalone mode (only when explicit offline mode is enabled)
        if not self._allow_offline:
            raise RuntimeError("Gemini memory extraction is required in real V1 mode.")

        doc_id = str(uuid.uuid4())
        embedding = self.embed_text(text)
        now_iso = datetime.now(timezone.utc).isoformat()
        doc = {
            "_id": doc_id,
            "text": text,
            "embedding": embedding,
            "payload": {
                "user_id": user_id,
                "data": text,
                "classification": classification,
                "status": "active",
                "embedding_model": self.active_embedding_model,
                "created_at": now_iso,
                "support_excerpt": redacted_evidence_excerpt(text, sensitive=classification == "sensitive"),
                **(metadata or {}),
            },
        }
        self.collection.insert_one(doc)
        if source_event_key and self._source_forgotten(user_id, source_event_key):
            self._delete_source_memories(user_id, source_event_key)
            return {"status": "skipped", "reason": "forgotten_source", "results": []}
        return {
            "results": [
                {
                    "id": doc_id,
                    "memory": text,
                    "event": "ADD",
                    "classification": classification,
                }
            ]
        }

    def add_explicit_fact(
        self,
        text: str,
        user_id: str,
        *,
        sensitive: bool = False,
    ) -> Dict[str, Any]:
        """Save one user-confirmed onboarding fact in the normal recall store."""
        clean_text = " ".join(text.split()).strip()
        if not clean_text:
            raise ValueError("Imported memory cannot be empty")
        if contains_secret(clean_text):
            return {"status": "skipped", "reason": "secret_credential_screened"}

        normalized = clean_text.casefold()
        existing = self.collection.find_one({
            "payload.user_id": user_id,
            "payload.status": "active",
            "payload.normalized_text": normalized,
        })
        if existing:
            return {"status": "duplicate", "id": str(existing["_id"])}

        classification = "sensitive" if sensitive or classify_text(clean_text) == "sensitive" else "general"
        vector = self.embed_text(clean_text)
        doc_id = str(uuid.uuid4())
        self.collection.insert_one({
            "_id": doc_id,
            "text": clean_text,
            "embedding": vector,
            "payload": {
                "user_id": user_id,
                "data": clean_text,
                "normalized_text": normalized,
                "classification": classification,
                "status": "active",
                "embedding_model": self.active_embedding_model,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source_site": "manual",
                "source_kind": "onboarding",
                "support_excerpt": redacted_evidence_excerpt(clean_text, sensitive=classification == "sensitive"),
            },
        })
        return {"status": "saved", "id": doc_id, "text": clean_text, "classification": classification}

    def search(
        self,
        query: str,
        user_id: str,
        *,
        top_k: int = 10,
        max_general: int = 3,
    ) -> Dict[str, Any]:
        """
        Searches user memories using Atlas vector search pre-filtered on payload.user_id.
        FAILS CLOSED when classification or control metadata is missing or invalid.
        Returns:
        - 'general_memories': up to max_general relevant memories
        - 'sensitive_memories': sensitive candidates requiring approval
        """
        query_vector = self.embed_text(query)
        pipeline = [
            {
                "$vectorSearch": {
                    "index": self.settings.mongodb_vector_index_name,
                    "path": "embedding",
                    "queryVector": query_vector,
                    "numCandidates": min(max(top_k * 20, 100), 10000),
                    "limit": top_k,
                    "filter": {"payload.user_id": {"$eq": user_id}},
                }
            },
            {"$set": {"score": {"$meta": "vectorSearchScore"}}},
            {"$project": {"embedding": 0}},
        ]

        try:
            candidates = list(self.collection.aggregate(pipeline))
        except Exception as exc:
            logger.warning("Atlas vector search notice (%s)", exc)
            candidates = []

        # Atlas indexes new writes and updates asynchronously. Even when older candidates
        # exist, include direct-scored recent documents (created or updated) so a fresh or
        # corrected fact is immediately available during recall.
        active_model = self.active_embedding_model
        mixed_spaces = False
        if active_model is not None and hasattr(self.collection, "count_documents"):
            try:
                mixed_spaces = self.collection.count_documents({
                    "payload.user_id": user_id,
                    "payload.embedding_model": {"$ne": active_model},
                }) > 0
            except Exception:
                # Search still applies a strict model check below.
                pass

        direct_query: Dict[str, Any] = {"payload.user_id": user_id, "payload.status": "active"}
        if candidates and not mixed_spaces:
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            direct_query["payload.created_at"] = {"$gte": cutoff}
        user_docs = list(self.collection.find(direct_query))

        if candidates and not mixed_spaces:
            try:
                updated_docs = list(self.collection.find({
                    "payload.user_id": user_id,
                    "payload.status": "active",
                    "payload.updated_at": {"$gte": cutoff},
                }))
                existing_ids = {str(d["_id"]) for d in user_docs}
                for doc in updated_docs:
                    if str(doc["_id"]) not in existing_ids:
                        user_docs.append(doc)
            except Exception:
                pass

        # Direct-scored recent documents must strictly match the active embedding model space
        if active_model is not None:
            user_docs = [
                d for d in user_docs
                if d.get("payload", {}).get("embedding_model") == active_model
                or (not d.get("payload", {}).get("embedding_model") and active_model == "models/gemini-embedding-001")
            ]

        combined = {str(doc["_id"]): doc for doc in candidates}
        query_norm = math.sqrt(sum(value * value for value in query_vector))
        for doc in user_docs:
            doc_emb = doc.get("embedding", [])
            if not doc_emb or len(doc_emb) != len(query_vector) or not query_norm:
                continue
            doc_norm = math.sqrt(sum(value * value for value in doc_emb))
            if not doc_norm:
                continue
            cosine = sum(a * b for a, b in zip(query_vector, doc_emb)) / (query_norm * doc_norm)
            # Atlas cosine vectorSearchScore maps cosine [-1, 1] to [0, 1].
            doc["score"] = (1.0 + cosine) / 2.0
            key = str(doc["_id"])
            if key not in combined or doc["score"] > combined[key].get("score", 0):
                combined[key] = doc
        candidates = sorted(combined.values(), key=lambda d: d.get("score", 0), reverse=True)[:top_k]

        general_memories: List[Dict[str, Any]] = []
        sensitive_memories: List[Dict[str, Any]] = []

        for doc in candidates:
            # Atlas cosine scores can be high even for unrelated prompts. Do not
            # send a weak match to another assistant merely because it is top-k.
            score = doc.get("score")
            if not isinstance(score, (int, float)) or not math.isfinite(score) or score < MIN_MEMORY_SCORE:
                continue
            payload = doc.get("payload")
            # FAIL CLOSED: Missing or malformed payload
            if not payload or not isinstance(payload, dict):
                continue

            # FAIL CLOSED: Mismatched ownership
            if payload.get("user_id") != user_id:
                continue

            # FAIL CLOSED: Status must be strictly 'active' (absent, blocked, or deleted fails closed)
            if payload.get("status") != "active":
                continue

            # FAIL CLOSED: Vector space mismatch rejection
            # Never expose or mix memories embedded with a different model space
            doc_model = payload.get("embedding_model")
            active_model = self.active_embedding_model
            if active_model is not None:
                if doc_model and doc_model != active_model:
                    continue
                if not doc_model and active_model != "models/gemini-embedding-001":
                    continue

            text_content = payload.get("data") or doc.get("text", "")
            if not text_content:
                continue

            # Defense-in-depth: Secret credentials in text are completely screened
            if contains_secret(text_content):
                continue

            # A missing policy label is not approval to expose memory text.
            raw_classification = payload.get("classification")
            if raw_classification not in ("general", "sensitive"):
                continue
            classification = raw_classification

            # If text indicates allergy or uncertain, enforce sensitive
            if classify_text(text_content) == "sensitive":
                classification = "sensitive"

            memory_obj = {
                "id": str(doc["_id"]),
                "text": text_content,
                "memory": text_content,
                "score": doc.get("score"),
                "classification": classification,
                "created_at": payload.get("created_at"),
            }

            if classification == "sensitive":
                sensitive_memories.append(memory_obj)
            elif classification == "general":
                if len(general_memories) < max_general:
                    general_memories.append(memory_obj)
            # Anything else fails closed

        return {
            "results": general_memories + sensitive_memories,
            "general_memories": general_memories,
            "sensitive_memories": sensitive_memories,
        }

    def get_all(self, user_id: str) -> List[Dict[str, Any]]:
        """Returns all non-deleted memories for the user."""
        docs = self.collection.find({
            "payload.user_id": user_id,
            "payload.status": {"$ne": "deleted"},
        })
        results = []
        for doc in docs:
            payload = doc.get("payload", {})
            text = payload.get("data") or doc.get("text", "")
            source = payload.get("source_site") or self._source_from_conversation(payload.get("conversation_id", ""))
            results.append({
                "id": str(doc["_id"]),
                "text": text,
                "memory": text,
                "classification": payload.get("classification", "sensitive"),
                "status": payload.get("status", "active"),
                "created_at": payload.get("created_at"),
                "source": source,
                "evidence_excerpt": payload.get("support_excerpt") or redacted_evidence_excerpt(
                    text, sensitive=payload.get("classification") == "sensitive"
                ),
            })
        return results

    @staticmethod
    def _source_from_conversation(conversation_id: str) -> str:
        for prefix, site in (("chatgpt", "chatgpt.com"), ("claude", "claude.ai"), ("gemini", "gemini.google.com")):
            if conversation_id.startswith(prefix):
                return site
        return "Unknown source"

    def _source_forgotten(self, user_id: str, source_event_key: str) -> bool:
        if not hasattr(self, "forgotten_sources"):
            return False
        return bool(self.forgotten_sources.find_one({"_id": source_event_key, "user_id": user_id}))

    def _delete_source_memories(self, user_id: str, source_event_key: str) -> None:
        tombstone = self.forgotten_sources.find_one({"_id": source_event_key, "user_id": user_id}) or {}
        allowed_ids = set(tombstone.get("allowed_memory_ids", []))
        docs = list(self.collection.find({"payload.user_id": user_id, "payload.source_event_key": source_event_key}))
        for doc in docs:
            memory_id = str(doc["_id"])
            if memory_id in allowed_ids:
                continue
            if self.memory is not None:
                try:
                    self.memory.delete(memory_id)
                except Exception:
                    pass
                try:
                    self._purge_history(memory_id)
                except Exception:
                    pass
            if hasattr(self, "collection") and self.collection is not None:
                self.collection.delete_one({"_id": doc["_id"], "payload.user_id": user_id})

    def _assert_owner(self, memory_id: str, user_id: str) -> Dict[str, Any]:
        if hasattr(self, "collection") and self.collection is not None:
            doc = self.collection.find_one({"_id": memory_id})
            if not doc or doc.get("payload", {}).get("user_id") != user_id:
                raise PermissionError("Memory does not exist for this user")
            return doc
        elif hasattr(self, "memory") and self.memory is not None:
            mem = self.memory.get(memory_id)
            if not mem or mem.get("user_id") != user_id:
                raise PermissionError("Memory does not exist for this user")
            return mem
        raise PermissionError("No backend store available to verify ownership")

    def update(self, memory_id: str, user_id: str, *, text: str) -> Dict[str, Any]:
        self._assert_owner(memory_id, user_id)
        text = text.strip()
        if not text:
            raise ValueError("Memory wording cannot be empty")
        classification = classify_text(text)
        if classification == "secret":
            raise ValueError("Cannot update memory to contain secrets")

        now_iso = datetime.now(timezone.utc).isoformat()
        new_vector = self.embed_text(text)
        new_excerpt = redacted_evidence_excerpt(text, sensitive=classification == "sensitive")

        if hasattr(self, "memory") and self.memory is not None:
            try:
                self.memory.update(memory_id, text=text)
            except Exception as exc:
                logger.warning("Mem0 update notice: %s", exc)

        if hasattr(self, "collection") and self.collection is not None:
            self.collection.update_one(
                {"_id": memory_id, "payload.user_id": user_id},
                {
                    "$set": {
                        "text": text,
                        "embedding": new_vector,
                        "payload.data": text,
                        "payload.classification": classification,
                        "payload.embedding_model": self.active_embedding_model,
                        "payload.updated_at": now_iso,
                        "payload.support_excerpt": new_excerpt,
                    }
                },
            )
        return {"id": memory_id, "text": text, "memory": text, "classification": classification}

    def block(self, memory_id: str, user_id: str) -> bool:
        """Immediately blocks a memory from active retrieval."""
        self._assert_owner(memory_id, user_id)
        if hasattr(self, "collection") and self.collection is not None:
            res = self.collection.update_one(
                {"_id": memory_id, "payload.user_id": user_id},
                {"$set": {"payload.status": "blocked", "payload.blocked_at": datetime.now(timezone.utc).isoformat()}},
            )
            return res.modified_count > 0
        return True

    def delete(self, memory_id: str, user_id: str) -> bool:
        """Deletes a memory for the user."""
        doc = self._assert_owner(memory_id, user_id)
        source_event_key = doc.get("payload", {}).get("source_event_key")
        if source_event_key and hasattr(self, "forgotten_sources"):
            siblings = [str(item["_id"]) for item in self.collection.find({
                "payload.user_id": user_id, "payload.source_event_key": source_event_key,
            }) if str(item["_id"]) != memory_id]
            self.forgotten_sources.update_one(
                {"_id": source_event_key, "user_id": user_id},
                {"$setOnInsert": {"_id": source_event_key, "user_id": user_id,
                                  "allowed_memory_ids": siblings,
                                  "created_at": datetime.now(timezone.utc).isoformat()}},
                upsert=True,
            )
            self.forgotten_sources.update_one(
                {"_id": source_event_key, "user_id": user_id},
                {"$pull": {"allowed_memory_ids": memory_id}},
            )
        if hasattr(self, "memory") and self.memory is not None:
            # Block recall first. Mem0 appends a DELETE history row but retains
            # plaintext ADD/UPDATE rows, so purge those rows after deletion.
            self.block(memory_id, user_id)
            try:
                self.memory.delete(memory_id)
            except Exception:
                pass
            try:
                self._purge_history(memory_id)
            except Exception:
                pass
            if hasattr(self, "collection") and self.collection is not None:
                self.collection.delete_one({"_id": memory_id, "payload.user_id": user_id})
            return True
        if hasattr(self, "collection") and self.collection is not None:
            self.collection.delete_one({"_id": memory_id, "payload.user_id": user_id})
        return True

    def _purge_history(self, memory_id: str) -> None:
        """Remove plaintext Mem0 history for one owned, deleted memory."""
        if not self.memory:
            return
        if not hasattr(self.memory, "db"):
            if isinstance(self.memory, Memory):
                raise RuntimeError("Mem0 history database is unavailable for purge")
            return  # Lightweight fakes used by ownership unit tests have no history.
        history_db = self.memory.db
        with history_db._lock:
            history_db.connection.execute("DELETE FROM history WHERE memory_id = ?", (memory_id,))
            history_db.connection.commit()
            remaining = history_db.connection.execute(
                "SELECT COUNT(*) FROM history WHERE memory_id = ?", (memory_id,)
            ).fetchone()[0]
        if remaining:
            raise RuntimeError("Memory history purge could not be verified")

    def history(self, memory_id: str, user_id: str):
        """Retrieves history for a memory after verifying tenant ownership."""
        self._assert_owner(memory_id, user_id)
        if hasattr(self, "memory") and self.memory is not None:
            return self.memory.history(memory_id)
        return []

    def delete_all(self, user_id: str):
        """Deletes all memories belonging to user_id."""
        if hasattr(self, "collection") and self.collection is not None:
            ids = [str(doc["_id"]) for doc in self.collection.find({"payload.user_id": user_id}, {"_id": 1})]
            self.collection.delete_many({"payload.user_id": user_id})
            if getattr(self, "memory", None) is not None:
                for memory_id in ids:
                    self._purge_history(memory_id)
        if hasattr(self, "memory") and self.memory is not None:
            try:
                self.memory.delete_all(user_id=user_id)
            except Exception:
                pass
        if hasattr(self, "forgotten_sources"):
            self.forgotten_sources.delete_many({"user_id": user_id})
