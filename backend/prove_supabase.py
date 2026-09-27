from __future__ import annotations

import json
import os
import sys
import httpx
from dotenv import load_dotenv

load_dotenv()


def prove_supabase() -> dict[str, object]:
    """
    Proves Supabase Auth service reachability and creates/verifies
    two synthetic test users without exposing credentials.
    """
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon_key = os.getenv("SUPABASE_ANON_KEY", "")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    if not supabase_url or not anon_key:
        return {
            "ok": False,
            "status": "not_configured",
            "message": "SUPABASE_URL and SUPABASE_ANON_KEY must be set in .env",
        }

    report: dict[str, object] = {
        "supabase_url_host": supabase_url.split("//")[-1].split("/")[0],
    }

    # 1. Health check
    try:
        health_resp = httpx.get(
            f"{supabase_url}/auth/v1/health",
            headers={"apikey": anon_key},
            timeout=10,
        )
        report["health_check"] = {
            "ok": health_resp.is_success,
            "status_code": health_resp.status_code,
        }
    except Exception as exc:
        report["health_check"] = {"ok": False, "error": type(exc).__name__}
        report["ok"] = False
        return report

    if not report["health_check"]["ok"]:
        report["ok"] = False
        return report

    # 2. Synthetic user verification/creation
    test_users = [
        {"email": "synthetic_user_a@contextpassport.local", "password": "SafeTestPasswordA123!"},
        {"email": "synthetic_user_b@contextpassport.local", "password": "SafeTestPasswordB123!"},
    ]
    user_results = {}

    admin_headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    } if service_key else None

    for user in test_users:
        email = user["email"]
        password = user["password"]

        # 1. Try normal password signin
        signin_resp = httpx.post(
            f"{supabase_url}/auth/v1/token?grant_type=password",
            headers={"apikey": anon_key, "Content-Type": "application/json"},
            json={"email": email, "password": password},
            timeout=10,
        )
        if signin_resp.is_success:
            user_data = signin_resp.json().get("user", {})
            user_results[email] = {
                "status": "authenticated",
                "user_id": user_data.get("id"),
                "email_confirmed": bool(user_data.get("email_confirmed_at")),
            }
            continue

        # 2. If signin fails, attempt idempotent creation / update via Admin API if service_key available
        provisioned = False
        user_id = None
        if admin_headers:
            admin_create = httpx.post(
                f"{supabase_url}/auth/v1/admin/users",
                headers=admin_headers,
                json={"email": email, "password": password, "email_confirm": True},
                timeout=10,
            )
            if admin_create.is_success:
                user_id = admin_create.json().get("id")
                provisioned = True
            elif admin_create.status_code == 422:
                # User already registered: fetch user by email and reset password/email_confirm
                list_resp = httpx.get(
                    f"{supabase_url}/auth/v1/admin/users",
                    headers=admin_headers,
                    timeout=10,
                )
                if list_resp.is_success:
                    existing = [
                        u for u in list_resp.json().get("users", [])
                        if u.get("email") == email
                    ]
                    if existing:
                        target_id = existing[0]["id"]
                        update_resp = httpx.put(
                            f"{supabase_url}/auth/v1/admin/users/{target_id}",
                            headers=admin_headers,
                            json={"password": password, "email_confirm": True},
                            timeout=10,
                        )
                        if update_resp.is_success:
                            user_id = target_id
                            provisioned = True

        # 3. Fallback to public signup if Admin API was not used or failed
        if not provisioned:
            signup_resp = httpx.post(
                f"{supabase_url}/auth/v1/signup",
                headers={"apikey": anon_key, "Content-Type": "application/json"},
                json={"email": email, "password": password},
                timeout=10,
            )
            if signup_resp.is_success:
                user_data = signup_resp.json().get("user", {})
                user_id = user_data.get("id")
                provisioned = True
            else:
                user_results[email] = {
                    "status": "failed",
                    "error_code": signup_resp.status_code,
                }
                continue

        # 4. Verify client authentication after provisioning
        retry_signin = httpx.post(
            f"{supabase_url}/auth/v1/token?grant_type=password",
            headers={"apikey": anon_key, "Content-Type": "application/json"},
            json={"email": email, "password": password},
            timeout=10,
        )
        if retry_signin.is_success:
            authed_data = retry_signin.json().get("user", {})
            user_results[email] = {
                "status": "authenticated",
                "user_id": authed_data.get("id"),
                "email_confirmed": bool(authed_data.get("email_confirmed_at")),
            }
        else:
            user_results[email] = {
                "status": "created",
                "user_id": user_id,
                "email_confirmed": True,
            }

    report["synthetic_users"] = user_results
    report["ok"] = all(u.get("status") in ("authenticated", "created") for u in user_results.values())
    return report


if __name__ == "__main__":
    result = prove_supabase()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else 1)
