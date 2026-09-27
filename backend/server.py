"""FastAPI backend server for Context Passport.

Implements Version 1 Working Memory and Learning Brain:
- Supabase Auth JWT verification with strict test environment gating
- Request-size limits (64KB payload, 16KB text) and rate limiting (60 req/min)
- Message ingestion with safe retry state machine and event_id handling
- Injected memory blocks rejected before storage
- Secret credential screening and allergy/uncertain sensitivity classification
- Explanation-style preference promotion (>= 3 obs across >= 2 chats)
- Scoped Atlas vector memory search with fail-closed metadata checks
"""

from __future__ import annotations

import collections
import hashlib
import io
import json
import logging
import os
import subprocess
import time
from contextlib import asynccontextmanager
from importlib.metadata import version
import threading
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse, StreamingResponse, RedirectResponse

from auth import AuthenticatedUser, get_current_user
from memory_manager import MemoryManager
from preference_engine import PreferenceEngine, is_extension_injected_context
from providers import (
    ProviderError,
    ProviderTimeoutError,
    ProviderRateLimitError,
    SpendingCapExceededError,
    ProviderOutageError,
    InvalidProviderResponseError,
    MissingConfigurationError,
)
from security import classify_text, contains_secret

logger = logging.getLogger(__name__)

BUILD_MARKER = os.getenv("BUILD_MARKER", "context-passport-v3-api-001")


def release_commit() -> str:
    if os.getenv("RENDER_GIT_COMMIT"):
        return os.environ["RENDER_GIT_COMMIT"][:12]
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=Path(__file__).resolve().parents[1], timeout=1, stderr=subprocess.DEVNULL,
        ).decode().strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


RELEASE_COMMIT = release_commit()

MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", "65536"))  # 64 KB limit
RATE_LIMIT_WINDOW = float(os.getenv("RATE_LIMIT_WINDOW", "60.0"))    # 60-second window
RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "60")) # 60 requests per minute

# Module-level singletons (lazy loaded)
memory_manager: Optional[MemoryManager] = None
preference_engine: Optional[PreferenceEngine] = None

# Rate limiter state: key -> deque of timestamps (thread-safe)
_rate_limit_lock = threading.Lock()
_rate_limit_records: dict[str, collections.deque] = collections.defaultdict(collections.deque)


def reset_rate_limits() -> None:
    """Resets rate limit state for tests."""
    with _rate_limit_lock:
        _rate_limit_records.clear()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Public binding requires an explicit deployment setting.
    host = os.getenv("HOST", "127.0.0.1").strip()
    public_hosting = os.getenv("PUBLIC_HOSTING", "false").lower() == "true"
    if host in ("0.0.0.0", "", "::", "0:0:0:0:0:0:0:0") and not public_hosting:
        logger.warning("Rejecting public host binding %s; enforcing loopback 127.0.0.1", host)
        os.environ["HOST"] = "127.0.0.1"
    logger.info("Context Passport API initialized on %s", os.getenv("HOST", "127.0.0.1"))
    yield
    # Shutdown: clean up singleton connections and reset in-memory state
    global memory_manager, preference_engine
    if preference_engine is not None and hasattr(preference_engine, "client") and preference_engine.client:
        try:
            preference_engine.client.close()
        except Exception:
            pass
    if memory_manager is not None and hasattr(memory_manager, "collection") and memory_manager.collection is not None:
        try:
            memory_manager.collection.database.client.close()
        except Exception:
            pass
    memory_manager = None
    preference_engine = None
    reset_rate_limits()
    logger.info("Context Passport API shutdown complete")


app = FastAPI(
    title="Context Passport API",
    description="Privacy-first browser memory backend and self-improvement engine.",
    version="3.0.0",
    lifespan=lifespan,
)

# Explicit CORS configuration for the 3 target AI sites, extension, and localhost
ALLOWED_ORIGINS = [
    "https://chatgpt.com",
    "https://claude.ai",
    "https://gemini.google.com",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost",
    "http://127.0.0.1",
]
ALLOWED_ORIGIN_REGEX = (
    r"^(chrome-extension://[a-z0-9]+"
    r"|https://([a-zA-Z0-9-]+\.)*(chatgpt\.com|claude\.ai|gemini\.google\.com)"
    r"|http://(localhost|127\.0\.0\.1)(:\d+)?)$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# Exception Handlers for clean provider error messages without leaking secrets
@app.exception_handler(ProviderTimeoutError)
async def provider_timeout_handler(request: Request, exc: ProviderTimeoutError):
    return JSONResponse(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        content={"detail": "Model provider request timed out. Please retry."},
    )


@app.exception_handler(ProviderRateLimitError)
async def provider_rate_limit_handler(request: Request, exc: ProviderRateLimitError):
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": "Model provider rate limit exceeded. Please try again shortly."},
        headers={"Retry-After": "30"},
    )


@app.exception_handler(SpendingCapExceededError)
async def spending_cap_handler(request: Request, exc: SpendingCapExceededError):
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": str(exc)},
    )


@app.exception_handler(ProviderOutageError)
async def provider_outage_handler(request: Request, exc: ProviderOutageError):
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Model provider service unavailable. Please retry later."},
    )


@app.exception_handler(InvalidProviderResponseError)
async def provider_invalid_resp_handler(request: Request, exc: InvalidProviderResponseError):
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": "Model provider returned an invalid response."},
    )


@app.exception_handler(MissingConfigurationError)
async def provider_missing_config_handler(request: Request, exc: MissingConfigurationError):
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": str(exc)},
    )


@app.middleware("http")
async def rate_limit_and_size_middleware(request: Request, call_next):
    # 1. Request size limit
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BYTES:
                return JSONResponse(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    content={"detail": f"Payload too large. Maximum allowed size is {MAX_REQUEST_BYTES} bytes."},
                )
        except ValueError:
            pass

    # 2. Rate limiting (skip health/ready)
    path = request.url.path
    if path not in ("/health", "/ready"):
        client_ip = request.client.host if request.client else "127.0.0.1"
        auth_header = request.headers.get("authorization", "")
        bucket_key = auth_header[-16:] if len(auth_header) > 16 else client_ip

        now = time.time()
        max_requests = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", str(RATE_LIMIT_MAX_REQUESTS)))
        window = float(os.getenv("RATE_LIMIT_WINDOW", str(RATE_LIMIT_WINDOW)))

        with _rate_limit_lock:
            timestamps = _rate_limit_records[bucket_key]
            while timestamps and now - timestamps[0] > window:
                timestamps.popleft()

            if len(timestamps) >= max_requests:
                retry_after = max(1, int(window - (now - timestamps[0])))
                logger.warning("Rate limit exceeded for %s", bucket_key)
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": f"Rate limit exceeded. Maximum {max_requests} requests per minute."},
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(max_requests),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(timestamps[0] + window)),
                    },
                )
            timestamps.append(now)
            remaining = max(0, max_requests - len(timestamps))

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_MAX_REQUESTS)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response

    return await call_next(request)


# Module-level singletons (lazy loaded)
memory_manager: Optional[MemoryManager] = None
preference_engine: Optional[PreferenceEngine] = None


def get_extractor():
    from providers import create_extractor
    return create_extractor()


def get_embedder():
    from providers import create_embedder
    return create_embedder()


def get_validator():
    from providers import create_validator
    return create_validator()


def get_mem_manager() -> MemoryManager:
    global memory_manager
    if memory_manager is None:
        from providers import create_extractor, create_embedder, create_validator
        memory_manager = MemoryManager(
            extractor=create_extractor(),
            embedder=create_embedder(),
            validator=create_validator(),
        )
    return memory_manager


def get_pref_engine() -> PreferenceEngine:
    global preference_engine
    if preference_engine is None:
        preference_engine = PreferenceEngine()
    return preference_engine


def get_active_llm_provider() -> str:
    explicit = os.getenv("LLM_PROVIDER")
    if explicit:
        return explicit.lower()
    if os.getenv("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    return "openrouter"


def require_paid_tier_confirmation() -> None:
    """Fail closed before sending conversation text or a draft to models."""
    if os.getenv("APP_ENV", "production").lower() == "test":
        return

    provider = get_active_llm_provider()
    if provider == "gemini":
        if os.getenv("GEMINI_PAID_TIER_CONFIRMED", "false").lower() != "true":
            raise HTTPException(
                status_code=503,
                detail="Gemini paid-tier confirmation is required before capture or recall.",
            )
    elif provider == "openrouter":
        if not os.getenv("OPENROUTER_API_KEY"):
            raise HTTPException(
                status_code=503,
                detail="OPENROUTER_API_KEY is required before capture or recall.",
            )
        from providers import global_usage_tracker
        try:
            global_usage_tracker.check_cap()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))


require_provider_ready = require_paid_tier_confirmation


# Request / Response Schemas
class MessageIngestRequest(BaseModel):
    conversation_id: str = Field(..., description="Unique conversation ID on the client site")
    text: str = Field(..., max_length=16000, description="Completed user message text (max 16KB)")
    role: str = Field(default="user", description="Message author role: must be 'user'")
    is_extension_context: bool = Field(default=False, description="True if text is extension-inserted memory")
    event_id: Optional[str] = Field(default=None, description="Optional idempotency key / event ID")
    source_site: Optional[Literal["chatgpt.com", "claude.ai", "gemini.google.com", "manual"]] = None


class MemoryQueryRequest(BaseModel):
    query: str = Field(..., max_length=16000, description="Current draft or prompt text")
    max_general: int = Field(default=3, ge=1, le=10, description="Max general memories to return")


class PreferenceUpdateRequest(BaseModel):
    preference_text: str = Field(..., max_length=1000, description="User-corrected preference wording")


class MemoryUpdateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=16000, description="User-corrected memory wording")


class AuthTokenRequest(BaseModel):
    email: str
    password: str


class MemoryImportRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    age: Optional[int] = Field(default=None, ge=13, le=120)
    role: Optional[str] = Field(default=None, max_length=100)
    tools: List[Literal["chatgpt", "claude", "gemini"]] = Field(default_factory=list, max_length=3)
    memories: List[str] = Field(default_factory=list)


# Endpoints
@app.get("/download/pelican-v3.zip")
async def download_extension() -> StreamingResponse:
    archive = Path(__file__).resolve().parent.parent / "release" / "pelican-v3.zip"
    if not archive.is_file():
        raise HTTPException(status_code=503, detail="Extension download is not packaged on this deployment.")
    base_url = os.getenv("PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL")
    if not base_url:
        if os.getenv("PUBLIC_HOSTING", "false").lower() == "true":
            raise HTTPException(status_code=503, detail="Set PUBLIC_BASE_URL to the HTTPS site URL before offering the extension.")
        base_url = "http://127.0.0.1:8000"
    base_url = base_url.rstrip("/")
    if not (base_url.startswith("https://") or base_url == "http://127.0.0.1:8000"):
        raise HTTPException(status_code=503, detail="PUBLIC_BASE_URL must be an HTTPS origin.")
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    if parsed.path or parsed.query or parsed.fragment or not parsed.netloc:
        raise HTTPException(status_code=503, detail="PUBLIC_BASE_URL must be an origin without a path.")
    output = io.BytesIO()
    with zipfile.ZipFile(archive) as original, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as packaged:
        for entry in original.infolist():
            if entry.is_dir():
                continue
            data = original.read(entry.filename)
            if entry.filename == "manifest.json":
                manifest = json.loads(data)
                manifest["homepage_url"] = base_url
                origin_permission = base_url + "/*"
                permissions = manifest.setdefault("host_permissions", [])
                if origin_permission not in permissions:
                    permissions.append(origin_permission)
                data = (json.dumps(manifest, indent=2) + "\n").encode()
            elif entry.filename == "pelican-config.js":
                data = ("window.PELICAN_BACKEND_URL = " + json.dumps(base_url) + ";\n").encode()
            packaged.writestr(entry.filename, data)
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="pelican-v3.zip"', "Cache-Control": "no-store"},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "context-passport",
        "version": "3.0.0",
        "build": BUILD_MARKER,
        "commit": RELEASE_COMMIT,
        "mem0": version("mem0ai"),
        "fastapi": version("fastapi"),
    }


@app.get("/ready")
async def ready(request: Request) -> JSONResponse:
    provider = get_active_llm_provider()
    required = [
        "MONGODB_URI",
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
    ]
    if provider == "gemini":
        required.insert(0, "GEMINI_API_KEY")
    elif provider == "openrouter":
        required.insert(0, "OPENROUTER_API_KEY")

    missing = [name for name in required if not os.getenv(name)]
    if provider == "gemini":
        if os.getenv("APP_ENV", "production").lower() != "test" and os.getenv(
            "GEMINI_PAID_TIER_CONFIRMED", "false"
        ).lower() != "true":
            missing.append("GEMINI_PAID_TIER_CONFIRMED")

    if os.getenv("ENABLE_JEV_VALIDATION", "false").lower() == "true":
        if "OPENROUTER_API_KEY" not in required and not os.getenv("OPENROUTER_API_KEY"):
            missing.append("OPENROUTER_API_KEY")

    if missing:
        return JSONResponse(
            {"status": "configuration_required", "missing": missing},
            status_code=503,
        )

    # In test environment, skip live network checks unless explicitly requested via query param
    check_connectivity = (
        request.query_params.get("check_connectivity", "").lower() == "true"
        or os.getenv("APP_ENV", "production").lower() != "test"
    )
    if not check_connectivity:
        return JSONResponse(
            {"status": "ready", "missing": []},
            status_code=200,
        )

    # External dependency connectivity checks
    disconnected: list[str] = []

    # 1. MongoDB Atlas connectivity check
    try:
        engine = get_pref_engine()
        if hasattr(engine, "client") and engine.client is not None:
            engine.client.admin.command("ping")
        else:
            from pymongo import MongoClient
            c = MongoClient(os.getenv("MONGODB_URI"), serverSelectionTimeoutMS=2000)
            c.admin.command("ping")
            c.close()
    except Exception as exc:
        logger.warning("MongoDB Atlas ping failed in /ready: %s", exc)
        disconnected.append("mongodb")

    # 2. Supabase Auth connectivity check
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon_key = os.getenv("SUPABASE_ANON_KEY", "")
    if supabase_url and anon_key:
        try:
            s_resp = httpx.get(
                f"{supabase_url}/auth/v1/settings",
                headers={"apikey": anon_key},
                timeout=8.0,
            )
            if not (s_resp.is_success or s_resp.status_code in (200, 401, 403)):
                disconnected.append("supabase")
        except Exception as exc:
            logger.warning("Supabase ping failed in /ready: %s", exc)
            disconnected.append("supabase")

    if disconnected:
        return JSONResponse(
            {
                "status": "service_unavailable",
                "disconnected": disconnected,
                "detail": f"Service(s) unreachable: {', '.join(disconnected)}",
            },
            status_code=503,
        )

    return JSONResponse(
        {
            "status": "ready",
            "mongodb": "connected",
            "supabase": "connected",
            "provider": provider,
        },
        status_code=200,
    )


@app.post("/api/v1/auth/token")
async def login_for_token(req: AuthTokenRequest) -> dict[str, Any]:
    """Sign in through Supabase Auth and return an access token."""
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon_key = os.getenv("SUPABASE_ANON_KEY", "")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not supabase_url or not anon_key:
        raise HTTPException(status_code=503, detail="Authentication is not configured")

    def _do_login() -> httpx.Response:
        return httpx.post(
            f"{supabase_url}/auth/v1/token?grant_type=password",
            headers={"apikey": anon_key, "Content-Type": "application/json"},
            json={"email": req.email, "password": req.password},
            timeout=10,
        )

    try:
        response = _do_login()
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="Authentication service unreachable") from exc

    if response.status_code != 200:
        code = ""
        if "json" in response.headers.get("content-type", ""):
            code = response.json().get("code", "")

        # Auto-confirm unconfirmed users using the service role key, then retry
        if code == "email_not_confirmed" and service_key:
            try:
                # Look up the user by email to get their ID
                lookup = httpx.get(
                    f"{supabase_url}/auth/v1/admin/users",
                    headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
                    params={"email": req.email},
                    timeout=10,
                )
                users = lookup.json().get("users", []) if lookup.status_code == 200 else []
                user_id = next((u["id"] for u in users if u.get("email", "").lower() == req.email.lower()), None)
                if user_id:
                    httpx.put(
                        f"{supabase_url}/auth/v1/admin/users/{user_id}",
                        headers={"apikey": service_key, "Authorization": f"Bearer {service_key}",
                                 "Content-Type": "application/json"},
                        json={"email_confirm": True},
                        timeout=10,
                    )
                    # Retry login after confirmation
                    try:
                        response = _do_login()
                    except httpx.RequestError as exc:
                        raise HTTPException(status_code=503, detail="Authentication service unreachable") from exc
            except Exception as exc:
                logger.warning("Auto-confirm failed: %s", exc)

    if response.status_code != 200:
        code = response.json().get("code", "") if "json" in response.headers.get("content-type", "") else ""
        if code == "email_not_confirmed":
            raise HTTPException(status_code=403, detail="Confirm your email before signing in. Check your inbox or spam folder.")
        if code == "invalid_credentials":
            raise HTTPException(status_code=401, detail="Email or password is incorrect.")
        raise HTTPException(status_code=401, detail="Sign-in failed. Check your credentials and try again.")

    return session_response(response.json())


class RefreshTokenRequest(BaseModel):
    refresh_token: str
    access_token: Optional[str] = None


def session_response(data: dict[str, Any]) -> dict[str, Any]:
    """Return the same session shape for every successful auth route."""
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        raise HTTPException(status_code=502, detail="Authentication did not return a session")
    return {
        "access_token": token,
        "refresh_token": data.get("refresh_token"),
        "expires_in": data.get("expires_in", 3600),
        "token_type": "bearer",
        "user": data.get("user"),
    }


@app.post("/api/v1/auth/refresh")
async def refresh_access_token(req: RefreshTokenRequest) -> dict[str, Any]:
    """Exchange a Supabase refresh token for a fresh access token."""
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon_key = os.getenv("SUPABASE_ANON_KEY", "")
    if not supabase_url or not anon_key:
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    try:
        resp = httpx.post(
            f"{supabase_url}/auth/v1/token?grant_type=refresh_token",
            headers={"apikey": anon_key, "Content-Type": "application/json"},
            json={"refresh_token": req.refresh_token},
            timeout=10,
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="Authentication service unreachable") from exc
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Refresh token expired or invalid")
    return session_response(resp.json())


@app.post("/api/v1/auth/logout")
async def logout_session(req: RefreshTokenRequest) -> dict[str, bool]:
    """Best-effort Supabase revocation; callers clear local credentials regardless."""
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon_key = os.getenv("SUPABASE_ANON_KEY", "")
    if supabase_url and anon_key and req.access_token:
        try:
            httpx.post(
                f"{supabase_url}/auth/v1/logout",
                headers={"apikey": anon_key, "Authorization": f"Bearer {req.access_token}"},
                timeout=3,
            )
        except httpx.RequestError:
            pass
    return {"ok": True}


@app.post("/api/v1/auth/signup")
async def signup(req: AuthTokenRequest) -> dict[str, Any]:
    """Create a Supabase Auth account and auto-confirm it so login works immediately."""
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon_key = os.getenv("SUPABASE_ANON_KEY", "")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not supabase_url or not anon_key:
        raise HTTPException(status_code=503, detail="Authentication is not configured")

    session: dict[str, Any] = {}
    token = None
    created = False

    # Prefer admin API user creation with email_confirm: True if service_key is available
    # to avoid Supabase free tier email rate limits (over_email_send_rate_limit) in production.
    if service_key and "example.test" not in supabase_url:
        try:
            admin_resp = httpx.post(
                f"{supabase_url}/auth/v1/admin/users",
                headers={
                    "apikey": service_key,
                    "Authorization": f"Bearer {service_key}",
                    "Content-Type": "application/json",
                },
                json={"email": req.email, "password": req.password, "email_confirm": True},
                timeout=10,
            )
            if admin_resp.status_code in (200, 201):
                created = True
                try:
                    login_resp = httpx.post(
                        f"{supabase_url}/auth/v1/token?grant_type=password",
                        headers={"apikey": anon_key, "Content-Type": "application/json"},
                        json={"email": req.email, "password": req.password},
                        timeout=10,
                    )
                    if login_resp.status_code == 200:
                        session = login_resp.json()
                        token = session.get("access_token")
                except Exception as exc:
                    logger.warning("Post-signup login failed: %s", exc)
            else:
                admin_data = admin_resp.json() if "json" in admin_resp.headers.get("content-type", "") else {}
                code = admin_data.get("code") or admin_data.get("error_code", "")
                msg = str(admin_data.get("msg") or admin_data.get("message", "")).lower()
                if code in {"user_already_exists", "email_exists"} or "already" in msg:
                    # User already exists - attempt automatic login with their password
                    try:
                        login_resp = httpx.post(
                            f"{supabase_url}/auth/v1/token?grant_type=password",
                            headers={"apikey": anon_key, "Content-Type": "application/json"},
                            json={"email": req.email, "password": req.password},
                            timeout=10,
                        )
                        if login_resp.status_code == 200:
                            session = login_resp.json()
                            return {"status": "ready", **session_response(session)}
                    except Exception:
                        pass
                    raise HTTPException(status_code=400, detail="Account already exists. Sign in instead.")
                elif code == "weak_password" or "password" in msg:
                    raise HTTPException(status_code=400, detail="Choose a stronger password (8+ characters with letters and numbers).")
                elif code == "over_email_send_rate_limit":
                    raise HTTPException(status_code=400, detail="Too many confirmation emails sent. Please wait a moment and try again.")
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("Admin user creation failed, falling back to signup endpoint: %s", exc)

    if not created and not token:
        try:
            response = httpx.post(
                f"{supabase_url}/auth/v1/signup",
                headers={"apikey": anon_key, "Content-Type": "application/json"},
                json={"email": req.email, "password": req.password},
                timeout=10,
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=503, detail="Authentication service unreachable") from exc

        if response.status_code not in (200, 201):
            code = response.json().get("code", "") if "json" in response.headers.get("content-type", "") else ""
            if code in {"user_already_exists", "email_exists"}:
                detail = "Account already exists. Sign in instead."
            elif code == "over_email_send_rate_limit":
                detail = "Too many confirmation emails sent. Please wait a moment and try again."
            elif code == "weak_password":
                detail = "Choose a stronger password (8+ characters with letters and numbers)."
            else:
                detail = "Sign-up failed. Check the email and password or try signing in."
            raise HTTPException(status_code=400, detail=detail)

        data = response.json()
        session = data.get("session") or data
        token = session.get("access_token") if isinstance(session, dict) else None
        user_id = (data.get("user") or data).get("id") if isinstance(data, dict) else None

        # Auto-confirm the new user via the admin API so no email step is needed
        if service_key and user_id and not token:
            try:
                confirm_resp = httpx.put(
                    f"{supabase_url}/auth/v1/admin/users/{user_id}",
                    headers={
                        "apikey": service_key,
                        "Authorization": f"Bearer {service_key}",
                        "Content-Type": "application/json",
                    },
                    json={"email_confirm": True},
                    timeout=10,
                )
                if confirm_resp.status_code == 200:
                    logger.info("Auto-confirmed user %s via admin API", user_id)
                    login_resp = httpx.post(
                        f"{supabase_url}/auth/v1/token?grant_type=password",
                        headers={"apikey": anon_key, "Content-Type": "application/json"},
                        json={"email": req.email, "password": req.password},
                        timeout=10,
                    )
                    if login_resp.status_code == 200:
                        session = login_resp.json()
                        token = session.get("access_token")
            except Exception as exc:
                logger.warning("Auto-confirm step failed for %s: %s", user_id, exc)

    if token:
        return {"status": "ready", **session_response(session)}
    return {"status": "confirmation_required", "access_token": None, "refresh_token": None,
            "expires_in": 0, "token_type": "bearer", "user": None}


class OAuthExchangeRequest(BaseModel):
    code: str
    code_verifier: Optional[str] = None
    redirect_to: Optional[str] = None


@app.get("/api/v1/auth/google")
async def get_google_auth_url(request: Request, redirect_to: Optional[str] = None):
    """Return Supabase Google OAuth authorization URL or redirect to it."""
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon_key = os.getenv("SUPABASE_ANON_KEY", "")
    if not supabase_url or not anon_key:
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    from urllib.parse import urlencode, urlsplit, urlunsplit
    target_redirect = redirect_to or "http://127.0.0.1:8000/dashboard.html"
    parsed = urlsplit(target_redirect)
    clean_redirect = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
    query = urlencode({"provider": "google", "redirect_to": clean_redirect})
    auth_url = f"{supabase_url}/auth/v1/authorize?{query}"
    accept = request.headers.get("accept", "")
    if "text/html" in accept and "application/json" not in accept:
        return RedirectResponse(auth_url, status_code=307)
    return {"url": auth_url}


@app.post("/api/v1/auth/oauth-callback")
async def oauth_callback(req: OAuthExchangeRequest) -> dict[str, Any]:
    """Exchange OAuth PKCE code for access token."""
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon_key = os.getenv("SUPABASE_ANON_KEY", "")
    if not supabase_url or not anon_key:
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    payload: dict[str, Any] = {"auth_code": req.code}
    if req.code_verifier:
        payload["code_verifier"] = req.code_verifier
    try:
        resp = httpx.post(
            f"{supabase_url}/auth/v1/token?grant_type=pkce",
            headers={"apikey": anon_key, "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return session_response(data)
        logger.warning("OAuth exchange responded %d", resp.status_code)
    except Exception as exc:
        logger.warning("OAuth exchange failed: %s", exc)
    raise HTTPException(status_code=400, detail="Could not exchange authorization code for session")


@app.post("/api/v1/memories/import")
async def import_memories(
    req: MemoryImportRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    mem_mgr: MemoryManager = Depends(get_mem_manager),
) -> dict[str, Any]:
    """Persist user-confirmed facts so Vault and recall can use them immediately."""
    require_paid_tier_confirmation()
    proposed: list[tuple[str, bool]] = []
    if req.name and req.name.strip():
        proposed.append((f"User's name is {req.name.strip()}.", True))
    if req.age is not None:
        proposed.append((f"User is {req.age} years old.", True))
    if req.role and req.role.strip():
        proposed.append((f"User's profession or role is {req.role.strip()}.", False))
    if req.tools:
        tools = ", ".join(dict.fromkeys(req.tools))
        proposed.append((f"User uses {tools} as AI assistants.", False))
    proposed.extend((memory, False) for memory in req.memories)
    if not proposed:
        return {"saved": [], "saved_count": 0, "duplicate_count": 0, "skipped_count": 0}

    saved: list[dict[str, Any]] = []
    duplicate_count = 0
    skipped_count = 0
    for text, sensitive in proposed:
        clean = " ".join(text.split()).strip()
        if not clean:
            continue
        result = mem_mgr.add_explicit_fact(clean, user.user_id, sensitive=sensitive)
        if result["status"] == "saved":
            saved.append(result)
        elif result["status"] == "duplicate":
            duplicate_count += 1
        else:
            skipped_count += 1
    return {"saved": saved, "saved_count": len(saved), "duplicate_count": duplicate_count, "skipped_count": skipped_count}


@app.post("/api/v1/messages/ingest")
async def ingest_message(
    req: MessageIngestRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    engine: PreferenceEngine = Depends(get_pref_engine),
    mem_mgr: MemoryManager = Depends(get_mem_manager),
) -> dict[str, Any]:
    """
    Ingests a user message:
    - Verifies user identity via Supabase JWT (strict test gate)
    - Rejects non-user messages and extension injected memory blocks BEFORE saving
    - Enforces safe retry state machine with event_id (never permanently blocks failed saves)
    - Screens recognizable credentials (secrets skipped completely)
    - Records explanation style observations & evaluates promotion (>=3 obs across >=2 chats)
    - Extracts durable facts and saves to scoped memory store
    """
    if req.role != "user":
        return {"status": "skipped", "reason": "assistant_reply_ignored"}

    clean_text = req.text.strip()
    if not clean_text:
        return {"status": "skipped", "reason": "empty_text"}

    # Defense: Reject injected memory blocks before saving or processing
    if req.is_extension_context or is_extension_injected_context(clean_text):
        logger.info("Rejected message containing extension injected memory context")
        return {
            "status": "skipped",
            "reason": "extension_context_ignored",
            "message": "Injected memory blocks cannot be captured as fresh evidence",
        }

    # Secret credential screening
    if contains_secret(clean_text):
        logger.info("User %s message contained recognizable secrets; screened completely", user.user_id)
        return {
            "status": "skipped",
            "reason": "secret_credential_screened",
            "message": "Recognizable credentials or secrets detected and skipped completely",
        }

    # API connectivity cannot prove Google's billing/data-handling tier. The
    # operator must verify billing before production conversation ingestion.
    require_paid_tier_confirmation()

    message_hash = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()

    # Process explanation preference evidence with safe retry state machine
    pref_result = engine.process_message(
        user_id=user.user_id,
        conversation_id=req.conversation_id,
        text=clean_text,
        role=req.role,
        is_extension_context=req.is_extension_context,
        event_id=req.event_id,
    )

    if pref_result.get("status") == "duplicate_skipped":
        return {
            "status": "duplicate_skipped",
            "message_hash": message_hash,
            "event_id": req.event_id,
            "reason": pref_result.get("reason"),
        }

    # A retry after memory_saved must not call Mem0 a second time.
    if pref_result.get("status") == "memory_saved":
        facts = pref_result.get("facts_extracted", [])
    else:
        try:
            fact_result = mem_mgr.add(
                text=clean_text,
                user_id=user.user_id,
                metadata={
                    "conversation_id": req.conversation_id,
                    "source_event_key": engine._event_key(user.user_id, message_hash, req.event_id),
                    "source_site": req.source_site,
                },
            )
        except ProviderTimeoutError as exc:
            logger.error("Provider timeout during memory save for user %s", user.user_id)
            engine.mark_event_failed(user.user_id, message_hash, "ProviderTimeoutError", event_id=req.event_id)
            raise HTTPException(status_code=504, detail="Model provider request timed out. Please retry.") from exc
        except Exception as exc:
            logger.error("Memory save failed for user %s (%s)", user.user_id, type(exc).__name__)
            engine.mark_event_failed(user.user_id, message_hash, type(exc).__name__, event_id=req.event_id)
            raise HTTPException(status_code=500, detail="Memory save failed; retry this event.") from exc
        if fact_result.get("reason") == "forgotten_source":
            engine.mark_memory_saved(user.user_id, message_hash, [], event_id=req.event_id)
            engine.mark_event_completed(user.user_id, message_hash, event_id=req.event_id)
            return {"status": "skipped", "reason": "forgotten_source"}
        facts = fact_result.get("results", [])
        engine.mark_memory_saved(user.user_id, message_hash, facts, event_id=req.event_id)

    try:
        pref_result = engine.complete_message(
            user.user_id, req.conversation_id, clean_text, message_hash, event_id=req.event_id,
        )
    except Exception as exc:
        logger.error("Learning evidence commit failed for user %s (%s)", user.user_id, type(exc).__name__)
        raise HTTPException(status_code=500, detail="Memory saved; learning evidence pending retry.") from exc

    return {
        "status": "processed",
        "facts_extracted": facts,
        "observations_recorded": pref_result.get("observations_recorded", []),
        "preference_promoted": pref_result.get("preference_promoted", False),
        "promoted_preferences": pref_result.get("promoted_preferences", []),
    }


@app.post("/api/v1/memories/query")
async def query_memories(
    req: MemoryQueryRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    engine: PreferenceEngine = Depends(get_pref_engine),
    mem_mgr: MemoryManager = Depends(get_mem_manager),
) -> dict[str, Any]:
    """
    Queries memories for prompt preparation:
    - Scoped strictly to authenticated user
    - Automatically returns up to max_general relevant general memories
    - Returns sensitive candidates separately (requiring explicit 'Allow once' approval)
    - Returns active promoted explanation preferences
    """
    require_paid_tier_confirmation()
    search_res = mem_mgr.search(
        query=req.query,
        user_id=user.user_id,
        max_general=req.max_general,
    )

    # Active preferences
    preferences = engine.get_user_preferences(user_id=user.user_id, status_filter="active")

    return {
        "general_memories": search_res.get("general_memories", []),
        "sensitive_memories": search_res.get("sensitive_memories", []),
        "preferences": preferences,
    }


@app.get("/api/v1/preferences")
async def get_preferences(
    user: AuthenticatedUser = Depends(get_current_user),
    engine: PreferenceEngine = Depends(get_pref_engine),
) -> list[dict[str, Any]]:
    return engine.get_user_preferences(user_id=user.user_id, status_filter="")


@app.put("/api/v1/preferences/{preference_id}")
async def update_preference(
    preference_id: str,
    req: PreferenceUpdateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    engine: PreferenceEngine = Depends(get_pref_engine),
) -> dict[str, Any]:
    try:
        return engine.update_preference_text(
            user_id=user.user_id,
            preference_id=preference_id,
            new_text=req.preference_text,
            lock=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400 if "wording" in str(exc) else 404, detail=str(exc))


@app.delete("/api/v1/preferences/{preference_id}/observations/{observation_id}")
async def remove_preference_evidence(
    preference_id: str,
    observation_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    engine: PreferenceEngine = Depends(get_pref_engine),
) -> dict[str, bool]:
    pref = engine.preferences_col.find_one({"_id": preference_id, "user_id": user.user_id})
    if not pref:
        raise HTTPException(status_code=404, detail="Preference not found")
    observation = engine.observations_col.find_one({"_id": observation_id, "user_id": user.user_id})
    if not observation or observation.get("preference_key") != pref.get("preference_key"):
        raise HTTPException(status_code=404, detail="Evidence not found")
    return {"ok": engine.remove_observation(user.user_id, observation_id)}


@app.post("/api/v1/preferences/{preference_id}/block")
async def block_preference(
    preference_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    engine: PreferenceEngine = Depends(get_pref_engine),
) -> dict[str, bool]:
    if not engine.block_preference(user.user_id, preference_id):
        raise HTTPException(status_code=404, detail="Preference not found or already blocked")
    return {"ok": True}


@app.delete("/api/v1/preferences/{preference_id}")
async def forget_preference(
    preference_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    engine: PreferenceEngine = Depends(get_pref_engine),
) -> dict[str, bool]:
    if not engine.forget_preference(user.user_id, preference_id):
        raise HTTPException(status_code=404, detail="Preference not found")
    return {"ok": True}


@app.get("/api/v1/memories")
async def get_all_memories(
    user: AuthenticatedUser = Depends(get_current_user),
    mem_mgr: MemoryManager = Depends(get_mem_manager),
) -> list[dict[str, Any]]:
    return mem_mgr.get_all(user_id=user.user_id)


@app.put("/api/v1/memories/{memory_id}")
async def update_memory(
    memory_id: str,
    req: MemoryUpdateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    mem_mgr: MemoryManager = Depends(get_mem_manager),
) -> dict[str, Any]:
    try:
        return mem_mgr.update(memory_id=memory_id, user_id=user.user_id, text=req.text)
    except PermissionError:
        raise HTTPException(status_code=404, detail="Memory not found for user")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/v1/memories/{memory_id}/block")
async def block_memory(
    memory_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    mem_mgr: MemoryManager = Depends(get_mem_manager),
) -> dict[str, bool]:
    try:
        ok = mem_mgr.block(memory_id=memory_id, user_id=user.user_id)
        return {"ok": ok}
    except PermissionError:
        raise HTTPException(status_code=404, detail="Memory not found for user")


@app.delete("/api/v1/memories/{memory_id}")
async def delete_memory(
    memory_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    mem_mgr: MemoryManager = Depends(get_mem_manager),
) -> dict[str, bool]:
    try:
        ok = mem_mgr.delete(memory_id=memory_id, user_id=user.user_id)
        return {"ok": ok}
    except PermissionError:
        raise HTTPException(status_code=404, detail="Memory not found for user")


# The dashboard and onboarding share the API origin. Keep this
# mount after API routes so static paths cannot shadow authenticated endpoints.
app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent.parent / "website", html=True), name="local_frontend")


def _start_port_3000_oauth_forwarder(target_port: int = 8000) -> None:
    """If port 3000 is unassigned locally, catch OAuth redirects sent to default Supabase Site URL (localhost:3000) and forward to target port."""
    import threading
    from http.server import HTTPServer, BaseHTTPRequestHandler

    class OAuthForwarderHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            target_path = "/onboarding.html" if ("onboarding" in self.path or "code=" in self.path or "error" in self.path) else "/dashboard.html"
            query_part = f"?{self.path.split('?', 1)[1]}" if "?" in self.path else ""
            redirect_target = f"http://127.0.0.1:{target_port}{target_path}{query_part}"
            self.send_response(307)
            self.send_header("Location", redirect_target)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    def run_forwarder():
        try:
            server = HTTPServer(("127.0.0.1", 3000), OAuthForwarderHandler)
            logger.info("Port 3000 OAuth helper active: forwarding to 127.0.0.1:%d", target_port)
            server.serve_forever()
        except OSError:
            pass

    t = threading.Thread(target=run_forwarder, daemon=True)
    t.start()


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1").strip()
    if host in ("0.0.0.0", "", "::", "0:0:0:0:0:0:0:0") and os.getenv("PUBLIC_HOSTING", "false").lower() != "true":
        logger.warning("Public interface binding rejected for security; binding to 127.0.0.1")
        host = "127.0.0.1"
    port = int(os.getenv("PORT", "8000"))
    if host == "127.0.0.1" and port != 3000:
        _start_port_3000_oauth_forwarder(target_port=port)
    logger.info("Starting Context Passport API on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
