"""Explanation-style preference engine and self-improvement learning brain.

Enforces the strict rule:
A preference is promoted ONLY after THREE distinct user-authored observations
across at least TWO conversations.
User corrections lock the preference text so later automatic guesses cannot overwrite it.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pymongo import MongoClient
from pymongo.collection import Collection
from security import classify_text, redacted_evidence_excerpt

logger = logging.getLogger(__name__)

# Explanation style pattern matchers: (preference_key, regex_pattern, standard_wording)
EXPLANATION_PATTERNS: List[Tuple[str, re.Pattern, str]] = [
    (
        "step_by_step",
        re.compile(
            r"(?i)\b(?:explain(?:\s+it)?\s+step\s*[-–—]?\s*by\s*[-–—]?\s*step|step\s*[-–—]?\s*by\s*[-–—]?\s*step\s+instructions?|walk\s+me\s+through\s+(?:this\s+)?step\s*[-–—]?\s*by\s*[-–—]?\s*step|break\s+(?:this|it)?\s*down\s+step\s*[-–—]?\s*by\s*[-–—]?\s*step|break\s+(?:this|it)\s+(?:down\s+)?into\s+(?:small|simple|clear)\s+steps|walk\s+me\s+through\s+(?:this\s+)?in\s+(?:small|simple)\s+steps)\b"
        ),
        "Prefers step-by-step explanations",
    ),
    (
        "concise",
        re.compile(
            r"(?i)\b(?:keep\s+it\s+concise|be\s+concise|brief\s+summary|no\s+fluff|short\s+and\s+sweet|keep\s+explanations?\s+short|give\s+a\s+high\s*[-–—]?\s*level\s+summary|concise\s+answers?\s+only)\b"
        ),
        "Prefers concise, direct explanations without unnecessary preamble",
    ),
    (
        "simple_eli5",
        re.compile(
            r"(?i)\b(?:explain\s+(?:it\s+)?simply|explain\s+(?:this|it)?\s*in\s+(?:very\s+)?simple\s+language|explain\s+like\s+i['’]?m\s+5|eli5|in\s+simple\s+terms|plain\s+english\s+without\s+jargon|make\s+(?:every\s+)?technical\s+terms?\s+easy\s+to\s+understand)\b"
        ),
        "Prefers simple explanations with minimal technical jargon",
    ),
    (
        "code_first",
        re.compile(
            r"(?i)\b(?:show\s+code\s+first|just\s+the\s+code|code\s+example\s+first|code\s+without\s+explanation)\b"
        ),
        "Prefers code examples first before textual explanation",
    ),
    (
        "detailed_deep_dive",
        re.compile(
            r"(?i)\b(?:detailed\s+(?:explanation|breakdown|deep\s*dive)|(?:examples?\s+and\s+detailed|detailed\s+and\s+(?:with\s+)?examples?)\s+explanations?|in\s*[-–—]?\s*depth\s+(?:breakdown|analysis)|thorough\s+analysis)\b"
        ),
        "Prefers comprehensive, detailed explanations with in-depth analysis",
    ),
]

EXTENSION_CONTEXT_MARKERS = [
    "[context passport",
    "<!-- context-passport",
    "[memory context",
    "[user memory",
    "[retrieved memory",
    "[retrieved memories",
    "<!-- memory-block",
    "[injected memory",
    "[/context passport]",
]


def is_extension_injected_context(text: str) -> bool:
    """Returns True if the text contains context injected by the extension."""
    if not text:
        return False
    lower = text.lower()
    return any(marker in lower for marker in EXTENSION_CONTEXT_MARKERS)


def extract_explanation_evidence(text: str) -> List[Tuple[str, str, str]]:
    """
    Extracts explanation-style preference matches from user text.
    Returns list of (preference_key, raw_matched_snippet, standard_wording).
    """
    matches: List[Tuple[str, str, str]] = []
    for key, pattern, default_text in EXPLANATION_PATTERNS:
        match = pattern.search(text)
        if match:
            matches.append((key, match.group(0), default_text))
    return matches


class PreferenceEngine:
    """
    Manages observations and promoted preferences in MongoDB Atlas.
    Uses database 'context_passport', collections:
    - 'observations'
    - 'preferences'
    - 'dedup_events'
    """

    def __init__(self, mongo_client: Optional[MongoClient] = None, db_name: Optional[str] = None):
        if mongo_client:
            self.client = mongo_client
        else:
            uri = os.getenv("MONGODB_URI")
            if not uri:
                raise RuntimeError("MONGODB_URI is required for PreferenceEngine")
            self.client = MongoClient(uri, serverSelectionTimeoutMS=10_000)

        self.db_name = db_name or os.getenv("MONGODB_DB_NAME", "context_passport")
        self.db = self.client[self.db_name]
        self.observations_col: Collection = self.db["observations"]
        self.preferences_col: Collection = self.db["preferences"]
        self.dedup_col: Collection = self.db["dedup_events"]
        self.forgotten_preferences_col: Collection = self.db["forgotten_preferences"]
        self._ensure_indexes()

    def _ensure_indexes(self):
        try:
            self.observations_col.create_index([("user_id", 1), ("preference_key", 1)])
            self.observations_col.create_index([("user_id", 1), ("conversation_id", 1)])
            self.preferences_col.create_index([("user_id", 1), ("preference_key", 1)], unique=True)
            self.forgotten_preferences_col.create_index([("user_id", 1), ("preference_key", 1)], unique=True)
            self.dedup_col.create_index([("user_id", 1), ("message_hash", 1)], unique=True)
        except Exception as exc:
            logger.warning("Index creation notice: %s", exc)

    @staticmethod
    def _event_key(user_id: str, message_hash: str, event_id: Optional[str]) -> str:
        # Client event IDs are not globally unique and must never cross tenants.
        return f"{user_id}:{event_id or message_hash}"

    def check_and_start_event(
        self,
        user_id: str,
        conversation_id: str,
        message_hash: str,
        event_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Checks dedup status with safe retry state machine:
        - 'completed': blocked as duplicate.
        - 'pending' (<60s): blocked as in_progress.
        - 'failed' or stale 'pending' (>60s) or new: allows retry, marks 'pending'.
        """
        dedup_id = self._event_key(user_id, message_hash, event_id)
        existing = self.dedup_col.find_one({"_id": dedup_id})
        if existing and existing.get("message_hash") != message_hash:
            return False, "event_id_reused_for_different_message"
        # Also covers older records whose _id did not include the user ID.
        same_message = self.dedup_col.find_one({"user_id": user_id, "message_hash": message_hash})
        if same_message and same_message.get("_id") != dedup_id:
            return False, "duplicate_message"
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        if existing:
            status = existing.get("status", "completed")
            if status == "completed":
                return False, "duplicate_completed"
            elif status == "memory_saved":
                return True, "memory_saved"
            elif status == "pending":
                created_dt_str = existing.get("updated_at") or existing.get("created_at")
                try:
                    created_dt = datetime.fromisoformat(created_dt_str)
                    age_seconds = (now - created_dt).total_seconds()
                except Exception:
                    age_seconds = 999
                if age_seconds < 60:
                    return False, "in_progress"
                logger.info("Retrying stale pending event %s", dedup_id)

        # Upsert as pending
        self.dedup_col.update_one(
            {"_id": dedup_id},
            {
                "$set": {
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                    "message_hash": message_hash,
                    "status": "pending",
                    "updated_at": now_iso,
                },
                "$setOnInsert": {
                    "created_at": now_iso,
                },
            },
            upsert=True,
        )
        return True, "pending"

    def mark_event_completed(self, user_id: str, message_hash: str, event_id: Optional[str] = None):
        """Marks event as successfully completed."""
        dedup_id = self._event_key(user_id, message_hash, event_id)
        self.dedup_col.update_one(
            {"_id": dedup_id, "user_id": user_id, "status": "memory_saved"},
            {"$set": {"status": "completed", "updated_at": datetime.now(timezone.utc).isoformat()}},
        )

    def mark_memory_saved(self, user_id: str, message_hash: str, facts: List[Dict[str, Any]], event_id: Optional[str] = None):
        result = self.dedup_col.update_one(
            {"_id": self._event_key(user_id, message_hash, event_id), "user_id": user_id, "status": "pending"},
            {"$set": {"status": "memory_saved", "facts_extracted": facts, "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
        if result.matched_count != 1:
            raise RuntimeError("Could not record saved memory event")

    def mark_event_failed(self, user_id: str, message_hash: str, error_msg: str, event_id: Optional[str] = None):
        """Marks event as failed so future retries can succeed."""
        dedup_id = self._event_key(user_id, message_hash, event_id)
        self.dedup_col.update_one(
            {"_id": dedup_id, "user_id": user_id, "status": "pending"},
            {
                "$set": {
                    "status": "failed",
                    "error_type": error_msg[:100],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )

    def process_message(
        self,
        user_id: str,
        conversation_id: str,
        text: str,
        role: str = "user",
        is_extension_context: bool = False,
        event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Evaluates a completed message for explanation-style preferences.
        Guarantees:
        - Only role == 'user' is processed.
        - Extension-injected context is skipped.
        - Duplicate events are skipped safely.
        - Records evidence observations.
        - Evaluates promotion: >= 3 distinct observations across >= 2 conversations.
        - User corrections (locked == True) are never overwritten.
        """
        if role != "user":
            return {"status": "skipped", "reason": "assistant_reply_ignored"}

        if is_extension_context or is_extension_injected_context(text):
            return {"status": "skipped", "reason": "extension_context_ignored"}

        cleaned_text = text.strip()
        if not cleaned_text:
            return {"status": "skipped", "reason": "empty_text"}

        message_hash = hashlib.sha256(cleaned_text.encode("utf-8")).hexdigest()
        can_proceed, status_reason = self.check_and_start_event(
            user_id, conversation_id, message_hash, event_id=event_id
        )
        if not can_proceed:
            return {
                "status": "duplicate_skipped",
                "message_hash": message_hash,
                "reason": status_reason,
            }

        event = self.dedup_col.find_one({"_id": self._event_key(user_id, message_hash, event_id), "user_id": user_id})
        return {
            "status": status_reason,
            "message_hash": message_hash,
            "facts_extracted": event.get("facts_extracted", []) if event else [],
        }

    def complete_message(
        self, user_id: str, conversation_id: str, text: str, message_hash: str,
        event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Commit evidence only after the memory write succeeds; retries reuse the same IDs."""
        dedup_id = self._event_key(user_id, message_hash, event_id)
        event = self.dedup_col.find_one({"_id": dedup_id, "user_id": user_id})
        if not event or event.get("status") != "memory_saved":
            raise RuntimeError("Memory must be saved before learning evidence is committed")
        if event.get("message_hash") != message_hash or event.get("conversation_id") != conversation_id:
            raise RuntimeError("Event identity changed during retry")

        recorded_obs = []
        promoted_preferences = []
        extracted = extract_explanation_evidence(text)
        for key, raw_evidence, standard_text in extracted:
            tombstone = self.forgotten_preferences_col.find_one({"user_id": user_id, "preference_key": key})
            if tombstone and event.get("created_at", "") <= tombstone.get("forgotten_at", ""):
                continue
            obs_id = hashlib.sha256(f"{dedup_id}:{key}".encode("utf-8")).hexdigest()
            obs_doc = {
                "_id": obs_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "message_hash": message_hash,
                "preference_key": key,
                "raw_evidence": raw_evidence,
                "default_text": standard_text,
                "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self.observations_col.update_one({"_id": obs_id, "user_id": user_id}, {"$setOnInsert": obs_doc}, upsert=True)
            self.observations_col.update_one({"_id": obs_id, "user_id": user_id, "status": "pending"}, {"$set": {"status": "active"}})
            observation = self.observations_col.find_one({"_id": obs_id, "user_id": user_id})
            if observation and observation.get("status") == "active":
                recorded_obs.append({"id": obs_id, "preference_key": key, "evidence": raw_evidence})

            # Evaluate promotion
            promoted = self._evaluate_and_promote(user_id, key, standard_text)
            if promoted:
                promoted_preferences.append(promoted)

        self.mark_event_completed(user_id, message_hash, event_id=event_id)

        return {
            "status": "processed",
            "observations_recorded": recorded_obs,
            "preference_promoted": bool(promoted_preferences),
            "promoted_preferences": promoted_preferences,
            "message_hash": message_hash,
        }

    def _evaluate_and_promote(self, user_id: str, preference_key: str, default_text: str) -> Optional[Dict[str, Any]]:
        """
        Promotes an explanation-style preference if and only if:
        - At least 3 distinct observations exist
        - Across at least 2 distinct conversations
        """
        all_obs = list(self.observations_col.find({
            "user_id": user_id, "preference_key": preference_key, "status": "active"
        }))
        # One authored message is one observation, including legacy retry rows.
        all_obs = list({o["message_hash"]: o for o in all_obs}.values())
        obs_count = len(all_obs)
        distinct_conversations = {o["conversation_id"] for o in all_obs}
        conv_count = len(distinct_conversations)

        logger.info(
            "User %s preference %s evaluation: %d observations across %d conversations",
            user_id, preference_key, obs_count, conv_count
        )

        existing = self.preferences_col.find_one({"user_id": user_id, "preference_key": preference_key})
        if obs_count < 3 or conv_count < 2:
            if existing:
                status = existing.get("status", "active") if existing.get("locked") or existing.get("status") == "blocked" else "insufficient_evidence"
                self.preferences_col.update_one(
                    {"_id": existing["_id"], "user_id": user_id},
                    {"$set": {"evidence_count": obs_count, "conversation_count": conv_count,
                              "evidence_ids": [o["_id"] for o in all_obs],
                              "evidence_snippets": [o.get("raw_evidence", "") for o in all_obs[:5]],
                              "status": status, "updated_at": datetime.now(timezone.utc).isoformat()}},
                )
            return None

        # Promotion rule satisfied! Check existing preference.
        now_iso = datetime.now(timezone.utc).isoformat()
        evidence_ids = [o["_id"] for o in all_obs]
        evidence_snippets = [o.get("raw_evidence", "") for o in all_obs[:5]]

        if existing and existing.get("locked"):
            # User has locked their preference wording; update evidence/metadata without overwriting wording
            self.preferences_col.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "evidence_count": obs_count,
                        "conversation_count": conv_count,
                        "evidence_ids": evidence_ids,
                        "evidence_snippets": evidence_snippets,
                        "conversation_ids": list(distinct_conversations),
                        "status": existing.get("status", "active"),
                        "updated_at": now_iso,
                    }
                },
            )
            return existing

        pref_doc = {
            "user_id": user_id,
            "category": "explanation_style",
            "preference_key": preference_key,
            "preference_text": existing.get("preference_text") if existing else default_text,
            "evidence_count": obs_count,
            "conversation_count": conv_count,
            "evidence_ids": evidence_ids,
            "conversation_ids": list(distinct_conversations),
            "evidence_snippets": evidence_snippets,
            "status": "blocked" if existing and existing.get("status") == "blocked" else "active",
            "locked": False,
            "updated_at": now_iso,
        }

        if existing:
            self.preferences_col.update_one({"_id": existing["_id"]}, {"$set": pref_doc})
            pref_doc["_id"] = str(existing["_id"])
        else:
            pref_doc["_id"] = str(uuid.uuid4())
            pref_doc["created_at"] = now_iso
            self.preferences_col.insert_one(pref_doc)

        return pref_doc

    def get_user_preferences(self, user_id: str, status_filter: str = "active") -> List[Dict[str, Any]]:
        """Returns all promoted preferences for the user."""
        query: Dict[str, Any] = {"user_id": user_id}
        if status_filter:
            query["status"] = status_filter
        cursor = self.preferences_col.find(query)
        result = []
        for doc in cursor:
            pref = dict(doc)
            pref["_id"] = str(doc["_id"])
            pref["id"] = pref["_id"]
            observations = list(self.observations_col.find({
                "user_id": user_id, "preference_key": doc["preference_key"], "status": "active"
            }))
            observations = list({obs.get("message_hash") or str(obs["_id"]): obs for obs in observations}.values())
            pref["supporting_observations"] = [
                {"id": str(obs["_id"]), "excerpt": redacted_evidence_excerpt(obs.get("raw_evidence", "")),
                 "conversation_id": obs.get("conversation_id", "")}
                for obs in observations
            ]
            pref["evidence_snippets"] = [item["excerpt"] for item in pref["supporting_observations"][:5]]
            result.append(pref)
        return result

    def update_preference_text(self, user_id: str, preference_id: str, new_text: str, lock: bool = True) -> Dict[str, Any]:
        """
        User correction: updates preference wording and locks it from later automatic overrides.
        """
        new_text = new_text.strip()
        if not new_text or classify_text(new_text) == "secret":
            raise ValueError("Preference wording must be nonempty and contain no credentials")
        pref = self.preferences_col.find_one({"_id": preference_id, "user_id": user_id})
        if not pref:
            raise ValueError(f"Preference {preference_id} not found for user")

        now_iso = datetime.now(timezone.utc).isoformat()
        self.preferences_col.update_one(
            {"_id": preference_id, "user_id": user_id},
            {
                "$set": {
                    "preference_text": new_text,
                    "locked": lock,
                    "status": "blocked" if pref.get("status") == "blocked" else "active",
                    "updated_at": now_iso,
                }
            },
        )
        pref["preference_text"] = new_text
        pref["locked"] = lock
        pref["status"] = "blocked" if pref.get("status") == "blocked" else "active"
        pref["updated_at"] = now_iso
        pref["_id"] = str(pref["_id"])
        return pref

    def remove_observation(self, user_id: str, observation_id: str) -> bool:
        observation = self.observations_col.find_one({"_id": observation_id, "user_id": user_id, "status": "active"})
        if not observation:
            return False
        self.observations_col.update_many(
            {"user_id": user_id, "preference_key": observation["preference_key"],
             "message_hash": observation["message_hash"], "status": "active"},
            {"$set": {"status": "removed", "raw_evidence": "", "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
        self._evaluate_and_promote(user_id, observation["preference_key"], observation.get("default_text", ""))
        return True

    def forget_preference(self, user_id: str, preference_id: str) -> bool:
        pref = self.preferences_col.find_one({"_id": preference_id, "user_id": user_id})
        if not pref:
            return False
        now_iso = datetime.now(timezone.utc).isoformat()
        self.forgotten_preferences_col.update_one(
            {"user_id": user_id, "preference_key": pref["preference_key"]},
            {"$set": {"forgotten_at": now_iso}}, upsert=True,
        )
        self.observations_col.update_many(
            {"user_id": user_id, "preference_key": pref["preference_key"]},
            {"$set": {"status": "removed", "raw_evidence": "", "updated_at": now_iso}},
        )
        return self.preferences_col.delete_one({"_id": preference_id, "user_id": user_id}).deleted_count == 1

    def block_preference(self, user_id: str, preference_id: str) -> bool:
        """Blocks a preference from active retrieval immediately."""
        res = self.preferences_col.update_one(
            {"_id": preference_id, "user_id": user_id},
            {"$set": {"status": "blocked", "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
        return res.modified_count > 0

    def clear_user_data(self, user_id: str):
        """Clears all observations, preferences, and dedup events for user (test teardown)."""
        self.observations_col.delete_many({"user_id": user_id})
        self.preferences_col.delete_many({"user_id": user_id})
        self.dedup_col.delete_many({"user_id": user_id})
        self.forgotten_preferences_col.delete_many({"user_id": user_id})
