"""Security, secret screening, and privacy classification for Context Passport."""

from __future__ import annotations

import re
from typing import Literal

MemoryClassification = Literal["general", "sensitive", "secret"]

# Credential and secret patterns (screened and skipped entirely)
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"AIza[0-9A-Za-z-_]{35}"),  # Google API key
    re.compile(r"sk-[a-zA-Z0-9_-]{20,}"),  # OpenAI key
    re.compile(r"ghp_[0-9a-zA-Z]{36}"),  # GitHub token
    re.compile(r"github_pat_[0-9a-zA-Z_]{82}"),  # GitHub fine-grained token
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key
    re.compile(r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-\.]{16,})['\"]?"),
    re.compile(r"(?i)\b(?:password|passwd|pwd)\s*(?:[:=]|\bis\b)\s*['\"]?([^\s'\"]{6,})['\"]?"),
    re.compile(r"(?i)\bBearer\s+([a-zA-Z0-9_\-\.]{20,})"),
    re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"),  # Credit card number
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # US Social Security Number
]

# Sensitive / uncertain indicators (requires user approval before sending to destination AI)
# Per spec: Allergies and uncertain facts MUST require approval, never automatic.
SENSITIVE_KEYWORDS = [
    # Allergies and acute health sensitivities
    "allergy", "allergies", "allergic", "epipen", "anaphylaxis",
    "gluten allergy", "dairy allergy", "nut allergy", "shellfish allergy",
    # Uncertain or unconfirmed facts
    "uncertain", "not sure", "unconfirmed", "tentative", "provisional", "speculative",
    # Financial and confidential
    "salary", "income", "bank account", "routing number", "credit score",
    "passport number", "social security", "confidential", "under nda",
    "proprietary", "internal only", "tax return",
    # Medical conditions, diagnoses, medications
    "medical condition", "diagnosis", "prescription", "medication", "doctor's note",
    "health record", "bipolar", "depression", "cancer", "hiv", "pregnancy", "addiction",
    "diabetes", "blood pressure", "hospitalized",
    # Identity, contact details, and protected personal information
    "home address", "street address", "mailing address", "phone number",
    "mobile number", "personal email", "religion", "religious belief",
    "sexual orientation", "gender identity", "political affiliation",
    "date of birth", "birthday", "legal name", "government id", "national id",
    "aadhaar", "aadhar", "pan number", "credit card", "debit card",
]

SENSITIVE_PATTERNS = [
    re.compile(rf"(?i)\b{re.escape(kw)}\b") for kw in SENSITIVE_KEYWORDS
]
SENSITIVE_PATTERNS.extend([
    re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    re.compile(r"(?i)\b(?:my|our|home|live at|reside at)\b.{0,30}\b\d{1,6}\s+[A-Za-z0-9 .'-]{2,50}\b(?:street|st\.?|road|rd\.?|avenue|ave\.?|lane|ln\.?|drive|dr\.?|boulevard|blvd\.?)\b"),
    re.compile(r"(?i)\b(?:my|contact|reach me at|email(?: address)? is)\b.{0,30}\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    re.compile(r"(?i)\b(?:my|call me at|reach me at|phone(?: number)? is)\b.{0,25}(?:\+?\d[\d .()\-]{8,}\d)\b"),
    re.compile(r"(?i)\b(?:i am|i'm|my faith is)\s+(?:a\s+)?(?:buddhist|christian|muslim|hindu|jewish|sikh|atheist)\b"),
])


def contains_secret(text: str) -> bool:
    """Returns True if the text contains recognizable credentials, private keys, or secrets."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def classify_text(text: str) -> MemoryClassification:
    """
    Classifies memory or message text as:
    - 'secret': contains recognizable credentials/keys/passwords -> MUST BE SKIPPED.
    - 'sensitive': contains sensitive personal, financial, medical or confidential info -> REQUIRES APPROVAL.
    - 'general': general facts or preferences -> ELIGIBLE FOR AUTOMATIC USE.
    """
    if not text:
        return "general"

    if contains_secret(text):
        return "secret"

    if any(pattern.search(text) for pattern in SENSITIVE_PATTERNS):
        return "sensitive"

    return "general"


def redact_secrets(text: str) -> str:
    """Redacts recognizable secrets from text, replacing them with [REDACTED_SECRET]."""
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED_SECRET]", redacted)
    return redacted


def redacted_evidence_excerpt(text: str, *, sensitive: bool = False, limit: int = 120) -> str:
    """Show a short proof hint without exposing stored credentials or contact details."""
    if sensitive:
        return "[Sensitive supporting detail hidden]"
    excerpt = redact_secrets(" ".join((text or "").split()))
    excerpt = re.sub(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[REDACTED_EMAIL]", excerpt)
    excerpt = re.sub(r"\b(?:\+?\d[\d .()\-]{8,}\d)\b", "[REDACTED_NUMBER]", excerpt)
    return excerpt[:limit].rstrip() + ("…" if len(excerpt) > limit else "")
