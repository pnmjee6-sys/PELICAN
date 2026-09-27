"""Verification and audit helper for Google Gemini API paid tier vs free tier.

PRIVACY NOTICE:
Under Google's API Terms of Service:
- Free Tier: Google may use customer prompts and model responses to improve Google products.
- Paid Tier: Google does NOT use customer prompts or outputs to train or improve models.
Real personal conversations MUST NOT be processed until the account has billing enabled.
"""

from __future__ import annotations

import json
import os
import sys
from dotenv import load_dotenv

load_dotenv()


def verify_paid_tier_prerequisites() -> dict[str, object]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return {
            "ok": False,
            "status": "NOT_CONFIGURED",
            "message": "GEMINI_API_KEY is not configured in .env",
        }

    report: dict[str, object] = {
        "gemini_api_key_present": True,
        "key_prefix": api_key[:6] + "..." if len(api_key) > 6 else "...",
        "extraction_model": os.getenv("GEMINI_EXTRACTION_MODEL", "gemini-3.5-flash-lite"),
        "embedding_model": os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001"),
    }

    from google import genai

    client = genai.Client(api_key=api_key)
    try:
        # Test basic generation
        res = client.models.generate_content(
            model=report["extraction_model"],
            contents="State 'OK' in one word.",
        )
        report["connectivity"] = {"ok": bool(res.text), "response": res.text.strip()}
    except Exception as exc:
        report["connectivity"] = {"ok": False, "error": str(exc)}
        report["ok"] = False
        return report

    report["privacy_boundary"] = {
        "rule": "Google Paid Tier required before real personal chats are processed.",
        "free_tier_warning": "Google Free tier logs prompts for product improvement. Never use Free tier for real personal data.",
        "paid_tier_guarantee": "Google Paid tier (Pay-as-you-go billing enabled in Google Cloud / AI Studio) excludes customer data from training.",
        "action_required": "Confirm billing is active on your Google Cloud Console project before deploying with personal data.",
    }
    # The Gemini API does not report the billing/data-handling tier for an API
    # key. Connectivity alone must never be presented as paid-tier proof.
    confirmed = os.getenv("GEMINI_PAID_TIER_CONFIRMED", "false").lower() == "true"
    report["paid_tier_operator_confirmed"] = confirmed
    report["ok"] = confirmed
    report["status"] = "OPERATOR_CONFIRMED" if confirmed else "BILLING_NOT_CONFIRMED"
    return report


if __name__ == "__main__":
    result = verify_paid_tier_prerequisites()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else 1)
