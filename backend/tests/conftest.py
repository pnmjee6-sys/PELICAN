"""Pytest configuration and isolation harness for Context Passport.

Enforces:
1. Pure offline execution for normal automated tests with zero external provider calls.
2. Isolated in-memory MongoDB storage (MockMongoClient) preventing calls to production Atlas.
3. Network guards monitoring and blocking any outgoing requests to Gemini, OpenRouter, Jev, or Atlas.
4. Clean separation of integration tests via @pytest.mark.integration.
"""

from __future__ import annotations

import os
import ipaddress
import socket
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

# Ensure backend root is on sys.path
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

LIVE_INTEGRATION = os.getenv("RUN_INTEGRATION_TESTS", "false").lower() == "true"

# Normal pytest is entirely offline, even when .env contains real keys.
if not LIVE_INTEGRATION:
    os.environ["APP_ENV"] = "test"
    os.environ["ALLOW_TEST_AUTH"] = "true"
    os.environ["ALLOW_OFFLINE_EMBEDDINGS"] = "true"
    os.environ["USE_MOCK_EXTRACTOR"] = "true"
    os.environ["USE_MOCK_EMBEDDER"] = "true"
    os.environ["ENABLE_JEV_VALIDATION"] = "false"

import pymongo
from mock_mongo import MockMongoClient, get_shared_mock_client, reset_mock_database

# ---------------------------------------------------------------------------
# External Provider Call Tracker & Network Guard
# ---------------------------------------------------------------------------
EXTERNAL_CALL_COUNTERS: Dict[str, int] = {
    "gemini": 0,
    "openrouter": 0,
    "jev_typesafe": 0,
    "production_atlas": 0,
}

BLOCKED_HOST_PATTERNS = {
    "generativelanguage.googleapis.com": "gemini",
    "openrouter.ai": "openrouter",
    "api.typesafe.ai": "jev_typesafe",
    "mongodb.net": "production_atlas",
}


_real_socket_connect = socket.socket.connect
_real_mongo_client_cls = pymongo.MongoClient


def _guarded_socket_connect(self, address):
    # Skip UDP sockets (e.g. 8.8.8.8 IP detection in test_lan_unreachability)
    if self.type == socket.SOCK_DGRAM:
        return _real_socket_connect(self, address)

    host = str(address[0]) if isinstance(address, (tuple, list)) and len(address) > 0 else str(address)
    port = address[1] if isinstance(address, (tuple, list)) and len(address) > 1 else None

    # Check for blocked external provider hosts
    for pattern, provider in BLOCKED_HOST_PATTERNS.items():
        if pattern in host:
            EXTERNAL_CALL_COUNTERS[provider] += 1
            raise RuntimeError(
                f"BLOCKED: Attempted external network call to {provider} ({host}:{port}) during offline test suite"
            )

    # Allow loopback connections (127.0.0.1, localhost)
    if host in ("127.0.0.1", "localhost", "::1"):
        return _real_socket_connect(self, address)

    return _real_socket_connect(self, address)


# Install guards only for the offline suite. Block all non-loopback TCP, including
# resolved provider IP addresses; matching only hostname strings is insufficient.
if not LIVE_INTEGRATION:
    def _offline_socket_connect(self, address):
        if self.type == socket.SOCK_DGRAM:
            return _real_socket_connect(self, address)
        host = str(address[0]) if isinstance(address, (tuple, list)) and address else str(address)
        try:
            is_private = ipaddress.ip_address(host).is_private
        except ValueError:
            is_private = host == "localhost"
        if not is_private:
            EXTERNAL_CALL_COUNTERS["production_atlas"] += 1
            raise RuntimeError("BLOCKED: outbound network call during offline tests")
        return _real_socket_connect(self, address)

    socket.socket.connect = _offline_socket_connect


# ---------------------------------------------------------------------------
# MongoDB Storage Isolation
# ---------------------------------------------------------------------------
def _mock_mongo_client_constructor(*args, **kwargs):
    # Always return isolated in-memory client for offline testing
    return get_shared_mock_client()


if not LIVE_INTEGRATION:
    pymongo.MongoClient = _mock_mongo_client_constructor


# ---------------------------------------------------------------------------
# Autouse Test Isolation Fixture
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def isolate_test_environment(monkeypatch):
    """Resets mock database, singletons, and tracks external calls per test."""
    if LIVE_INTEGRATION:
        yield
        return
    reset_mock_database()

    # Reset server singletons if server is imported
    if "server" in sys.modules:
        import server
        server.memory_manager = None
        server.preference_engine = None
        server.reset_rate_limits()

    # Reset usage tracker
    if "providers" in sys.modules:
        from providers import global_usage_tracker
        global_usage_tracker.reset()

    yield

    # Clean up singletons after test
    if "server" in sys.modules:
        import server
        server.memory_manager = None
        server.preference_engine = None
        server.reset_rate_limits()


@pytest.fixture(scope="session", autouse=True)
def verify_zero_external_provider_calls():
    """Session-level verification confirming zero external calls occurred."""
    yield
    if LIVE_INTEGRATION:
        return
    total_external_calls = sum(EXTERNAL_CALL_COUNTERS.values())
    if total_external_calls > 0:
        raise AssertionError(
            f"Offline test suite leaked external provider calls: {EXTERNAL_CALL_COUNTERS}"
        )
