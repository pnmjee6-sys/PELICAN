from __future__ import annotations

import json
import os
import sys

import httpx
from google import genai
from pymongo import MongoClient

from settings import load_settings


def check() -> dict[str, dict[str, object]]:
    settings = load_settings()
    result: dict[str, dict[str, object]] = {}

    try:
        client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=10_000)
        client.admin.command("ping")
        result["mongodb_atlas"] = {"ok": True}
    except Exception as exc:
        result["mongodb_atlas"] = {"ok": False, "error": type(exc).__name__}

    active_provider = getattr(settings, "embedding_provider", getattr(settings, "llm_provider", "openrouter")).lower()

    if active_provider == "openrouter":
        try:
            from providers import OpenRouterEmbedder
            embedder = OpenRouterEmbedder(
                api_key=settings.openrouter_api_key,
                base_url=settings.openrouter_base_url,
                model=settings.embedding_model,
                dimensions=settings.embedding_dims,
            )
            emb = embedder.embed("Context Passport connectivity check")
            result["openrouter"] = {"ok": bool(emb and len(emb) == settings.embedding_dims), "dims": len(emb)}
        except Exception as exc:
            result["openrouter"] = {"ok": False, "error": type(exc).__name__, "detail": str(exc)[:100]}
    else:
        try:
            response = genai.Client(api_key=settings.gemini_api_key).models.embed_content(
                model=settings.gemini_embedding_model,
                contents="Context Passport connectivity check",
            )
            result["gemini"] = {"ok": bool(response.embeddings)}
        except Exception as exc:
            result["gemini"] = {"ok": False, "error": type(exc).__name__}

    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    supabase_key = os.getenv("SUPABASE_ANON_KEY", "")
    if supabase_url and supabase_key:
        try:
            response = httpx.get(
                f"{supabase_url}/auth/v1/health",
                headers={"apikey": supabase_key},
                timeout=10,
            )
            result["supabase"] = {"ok": response.is_success, "status": response.status_code}
        except Exception as exc:
            result["supabase"] = {"ok": False, "error": type(exc).__name__}
    else:
        result["supabase"] = {"ok": False, "error": "not_configured"}

    loopback_url = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
    try:
        response = httpx.get(f"{loopback_url}/health", timeout=2.0)
        result["local_loopback"] = {"ok": response.is_success, "status": response.status_code}
    except Exception as exc:
        result["local_loopback"] = {"ok": False, "error": type(exc).__name__}

    return result


if __name__ == "__main__":
    try:
        checks = check()
    except RuntimeError as exc:
        print(json.dumps({"configuration": {"ok": False, "error": str(exc)}}, indent=2))
        raise SystemExit(1) from exc
    print(json.dumps(checks, indent=2))
    sys.exit(0 if all(item["ok"] for item in checks.values()) else 1)
