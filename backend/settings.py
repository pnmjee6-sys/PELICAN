from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    # Model Provider Settings
    llm_provider: str
    openrouter_api_key: str
    openrouter_base_url: str
    extraction_primary_model: str
    extraction_fallback_model: str
    embedding_provider: str
    embedding_model: str
    embedding_dims: int

    # Jev Validation Pass (TypeSafe AI via OpenRouter)
    enable_jev_validation: bool

    # Reliability & Cost Guards
    openrouter_spending_cap: float
    provider_timeout_seconds: float
    provider_max_retries: int

    # Legacy Gemini compatibility
    gemini_api_key: str
    gemini_extraction_model: str
    gemini_embedding_model: str
    gemini_embedding_dims: int

    # Storage Settings
    mongodb_uri: str
    mongodb_db_name: str
    mongodb_collection_name: str
    mongodb_vector_index_name: str
    history_db_path: str

    # Network & Localhost Runtime
    host: str = "127.0.0.1"
    port: int = 8000
    jev_spending_cap: float = 1.00
    jev_max_calls: int = 20
    jev_model: str = "typesafe/jev-router"


def load_settings(
    require_gemini: Optional[bool] = None,
    require_model_keys: bool = False,
) -> Settings:
    history_path = Path(os.getenv("MEM0_HISTORY_DB_PATH", "backend/mem0_history.db"))
    if not history_path.is_absolute():
        history_path = ROOT_DIR / history_path

    # Ensure parent directory exists with secure permissions (0700)
    try:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(history_path.parent, 0o700)
    except Exception:
        pass

    # Ensure SQLite history database file has secure permissions (0600)
    if history_path.exists() and os.name != "nt":
        try:
            os.chmod(history_path, 0o600)
        except Exception:
            pass

    llm_provider = os.getenv("LLM_PROVIDER", "openrouter").lower()
    embedding_provider = os.getenv("EMBEDDING_PROVIDER", "openrouter").lower()
    enable_jev = os.getenv("ENABLE_JEV_VALIDATION", "false").lower() == "true"

    # Backward compatibility with require_gemini parameter
    need_gemini = False
    if require_gemini is not None:
        need_gemini = require_gemini
    elif require_model_keys and llm_provider == "gemini":
        need_gemini = True

    gemini_key = _required("GEMINI_API_KEY") if need_gemini else os.getenv("GEMINI_API_KEY", "")

    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if require_model_keys and (llm_provider == "openrouter" or embedding_provider == "openrouter"):
        if not openrouter_key:
            raise RuntimeError("Missing required environment variable: OPENROUTER_API_KEY")

    jev_model = os.getenv("JEV_MODEL", "typesafe/jev-router").strip() or "typesafe/jev-router"

    raw_host = os.getenv("HOST", "127.0.0.1").strip()
    # Reject 0.0.0.0 and wildcard binding; strictly enforce loopback
    if raw_host in ("0.0.0.0", "", "::", "0:0:0:0:0:0:0:0"):
        host = "127.0.0.1"
    else:
        host = raw_host
    port = int(os.getenv("PORT", "8000"))

    return Settings(
        llm_provider=llm_provider,
        openrouter_api_key=openrouter_key,
        openrouter_base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        extraction_primary_model=os.getenv("EXTRACTION_PRIMARY_MODEL", "z-ai/glm-5.3-flash"),
        extraction_fallback_model=os.getenv("EXTRACTION_FALLBACK_MODEL", "google/gemini-2.5-flash-lite"),
        embedding_provider=embedding_provider,
        embedding_model=os.getenv("EMBEDDING_MODEL", "openai/text-embedding-3-small"),
        embedding_dims=int(os.getenv("EMBEDDING_DIMS", "1536")),
        enable_jev_validation=enable_jev,
        jev_model=jev_model,
        openrouter_spending_cap=float(os.getenv("OPENROUTER_SPENDING_CAP", "2.00")),
        provider_timeout_seconds=float(os.getenv("PROVIDER_TIMEOUT_SECONDS", "30.0")),
        provider_max_retries=int(os.getenv("PROVIDER_MAX_RETRIES", "2")),
        gemini_api_key=gemini_key,
        gemini_extraction_model=os.getenv("GEMINI_EXTRACTION_MODEL", "gemini-2.5-flash"),
        gemini_embedding_model=os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001"),
        gemini_embedding_dims=int(os.getenv("GEMINI_EMBEDDING_DIMS", "1536")),
        mongodb_uri=_required("MONGODB_URI"),
        mongodb_db_name=os.getenv("MONGODB_DB_NAME", "context_passport"),
        mongodb_collection_name=os.getenv("MONGODB_COLLECTION_NAME", "memories"),
        mongodb_vector_index_name=os.getenv("MONGODB_VECTOR_INDEX_NAME", "memories_vector_index_scoped"),
        history_db_path=str(history_path),
        host=host,
        port=port,
        jev_spending_cap=float(os.getenv("JEV_SPENDING_CAP", "1.00")),
        jev_max_calls=int(os.getenv("JEV_MAX_CALLS", "20")),
    )
