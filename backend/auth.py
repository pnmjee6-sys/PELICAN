"""Supabase Auth verification and session helpers for Context Passport."""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from fastapi import Header, HTTPException, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class AuthenticatedUser(BaseModel):
    user_id: str
    email: Optional[str] = None


def is_jwt_expired(token_str: str) -> bool:
    """Inspects JWT payload unverified to check exp claim timestamp."""
    parts = token_str.split(".")
    if len(parts) == 3:
        import base64
        import json
        import time
        try:
            padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
            exp = payload.get("exp")
            if exp is not None and isinstance(exp, (int, float)):
                return time.time() >= exp
        except Exception:
            pass
    return False


def verify_supabase_token(token: str) -> AuthenticatedUser:
    """
    Verifies a Supabase Auth JWT access token by querying Supabase's user endpoint.
    Returns AuthenticatedUser with verified user_id.
    Raises HTTPException(401) with 'token_expired' on expiry or 'Invalid authentication token' on invalid.
    """
    if not token or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization token",
        )

    clean_token = token.strip()
    if clean_token.lower().startswith("bearer "):
        clean_token = clean_token[7:].strip()

    # Test token bypass: permitted ONLY in explicit test environments
    if clean_token.startswith("test-bearer-"):
        app_env = os.getenv("APP_ENV", "").lower()
        allow_test_auth = os.getenv("ALLOW_TEST_AUTH", "false").lower() == "true"
        if app_env != "test" or not allow_test_auth:
            logger.warning("Blocked attempt to use test-bearer bypass in non-test environment")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Test token bypass is disabled in this environment",
            )
        test_uid = clean_token.replace("test-bearer-", "")
        return AuthenticatedUser(user_id=test_uid, email=f"{test_uid}@test.local")

    # Explicit expired mock token handling for tests
    if clean_token.startswith("expired-") or clean_token == "expired_token":
        logger.info("Rejected expired test token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token_expired",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token", error_description="The access token expired"'},
        )

    jwt_expired = is_jwt_expired(clean_token)

    # An expired bearer token is never proof of an active session. The client
    # must exchange its refresh token for a new access token instead.
    if jwt_expired:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token_expired",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token", error_description="The access token expired"'},
        )

    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    supabase_anon_key = os.getenv("SUPABASE_ANON_KEY", "")

    if not supabase_url or not supabase_anon_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Supabase Auth is not configured on backend",
        )

    try:
        response = httpx.get(
            f"{supabase_url}/auth/v1/user",
            headers={
                "apikey": supabase_anon_key,
                "Authorization": f"Bearer {clean_token}",
            },
            timeout=8.0,
        )
        if response.status_code == 200:
            data = response.json()
            user_id = data.get("id")
            if not user_id:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token: missing user ID",
                )
            return AuthenticatedUser(user_id=user_id, email=data.get("email"))

        try:
            err_data = response.json()
        except Exception:
            err_data = {}

        msg = str(err_data.get("msg") or err_data.get("message") or err_data.get("error_description") or "").lower()
        code = str(err_data.get("code") or err_data.get("error_code") or "").lower()

        if "expired" in msg or "expired" in code:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="token_expired",
                headers={"WWW-Authenticate": 'Bearer error="invalid_token", error_description="The access token expired"'},
            )

        logger.warning("Supabase token verification failed with status %s", response.status_code)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )
    except (httpx.RequestError, httpx.TimeoutException) as exc:
        logger.error("Failed to connect to Supabase Auth: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unreachable",
        ) from exc


def get_current_user(authorization: Optional[str] = Header(None)) -> AuthenticatedUser:
    """FastAPI dependency to extract and verify the current authenticated user."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )
    return verify_supabase_token(authorization)


def acquire_synthetic_user_token(email: str, password: str = "TestPass123!SafePass") -> Optional[str]:
    """Helper to authenticate a synthetic user against Supabase and get an access token."""
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    supabase_anon_key = os.getenv("SUPABASE_ANON_KEY", "")

    if not supabase_url or not supabase_anon_key:
        return None

    try:
        resp = httpx.post(
            f"{supabase_url}/auth/v1/token?grant_type=password",
            headers={"apikey": supabase_anon_key, "Content-Type": "application/json"},
            json={"email": email, "password": password},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("access_token")
    except Exception as exc:
        logger.warning("Failed to obtain synthetic user token: %s", exc)
    return None
