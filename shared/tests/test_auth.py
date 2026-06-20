"""Phase 0 verification: 'Shared auth middleware has unit tests for both
AUTH_ENABLED=true and =false paths' (implementation plan, Phase 0).
"""
import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from auth.auth import get_auth_context, require_role, AuthContext, JWT_ALGORITHM

TEST_SECRET = "dev-secret-change-in-production"  # matches auth.py's default


def make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/whoami")
    def whoami(ctx: AuthContext = Depends(get_auth_context)):
        return {"user_id": ctx.user_id, "role": ctx.role}

    @app.get("/admin-only")
    def admin_only(ctx: AuthContext = Depends(require_role("ADMIN"))):
        return {"ok": True, "user_id": ctx.user_id}

    return app


def make_token(role: str = "CUSTOMER", user_id: str = "user-123") -> str:
    return jwt.encode({"sub": user_id, "role": role}, TEST_SECRET, algorithm=JWT_ALGORITHM)


# --- AUTH_ENABLED=false: nothing should ever be rejected ---

def test_auth_disabled_allows_request_with_no_token(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    client = TestClient(make_app())

    response = client.get("/whoami")

    assert response.status_code == 200
    assert response.json() == {"user_id": None, "role": None}


def test_auth_disabled_allows_admin_endpoint_with_no_token(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    client = TestClient(make_app())

    response = client.get("/admin-only")

    assert response.status_code == 200


# --- AUTH_ENABLED=true: real enforcement ---

def test_auth_enabled_rejects_missing_token(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client = TestClient(make_app())

    response = client.get("/whoami")

    assert response.status_code == 401


def test_auth_enabled_rejects_invalid_token(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client = TestClient(make_app())

    response = client.get("/whoami", headers={"Authorization": "Bearer not-a-real-token"})

    assert response.status_code == 401


def test_auth_enabled_accepts_valid_token_and_extracts_claims(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client = TestClient(make_app())
    token = make_token(role="CUSTOMER", user_id="user-42")

    response = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {"user_id": "user-42", "role": "CUSTOMER"}


def test_auth_enabled_rejects_wrong_role_on_admin_endpoint(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client = TestClient(make_app())
    token = make_token(role="CUSTOMER")

    response = client.get("/admin-only", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403


def test_auth_enabled_accepts_correct_role_on_admin_endpoint(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client = TestClient(make_app())
    token = make_token(role="ADMIN", user_id="admin-1")

    response = client.get("/admin-only", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "user_id": "admin-1"}
