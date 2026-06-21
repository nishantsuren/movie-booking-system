"""Phase 7 verification criteria (implementation-plan.md):

- Register/login round-trip returns a valid JWT with the correct role
  claim.
- Regression test across every endpoint built in Phases 1-6: confirm
  AUTH_ENABLED=false still bypasses cleanly and nothing built so far has
  accidentally started requiring a token. That regression is just the
  existing test_phase1.py..test_phase6.py suite run as-is (no new code
  needed to express it) -- see the session's regression run, not a new
  test in this file.

JWT_SECRET / JWT_ALGORITHM here match shared/auth/auth.py's defaults
exactly (same constant shared/tests/test_auth.py already uses) --
deliberately not importing auth.py's private _jwt_secret() helper from a
test in a different service's directory; the point of this test is to
verify the token decodes correctly using the *same public contract*
every other service already relies on.
"""
import uuid

import httpx
import jwt
import pytest

ROUTING_BASE = "http://localhost:8000"
JWT_SECRET = "dev-secret-change-in-production"
JWT_ALGORITHM = "HS256"


@pytest.fixture
def routing():
    with httpx.Client(base_url=ROUTING_BASE, timeout=10.0) as client:
        yield client


def unique_email() -> str:
    return f"phase7-{uuid.uuid4().hex[:10]}@example.com"


def register(routing, email: str, password: str = "supersecret123", role: str = "CUSTOMER") -> httpx.Response:
    return routing.post("/user/auth/register", json={"email": email, "password": password, "role": role})


def login(routing, email: str, password: str = "supersecret123") -> httpx.Response:
    return routing.post("/user/auth/login", json={"email": email, "password": password})


# --- register/login round-trip: valid JWT with the correct role claim ---

def test_register_then_login_returns_jwt_with_correct_role_claim(routing):
    email = unique_email()
    register_resp = register(routing, email, role="ADMIN")
    assert register_resp.status_code == 201, register_resp.text
    user = register_resp.json()
    assert user["email"] == email
    assert user["role"] == "ADMIN"

    login_resp = login(routing, email)
    assert login_resp.status_code == 200, login_resp.text
    body = login_resp.json()
    assert body["user_id"] == user["id"]
    assert body["role"] == "ADMIN"

    decoded = jwt.decode(body["access_token"], JWT_SECRET, algorithms=[JWT_ALGORITHM])
    assert decoded["sub"] == user["id"]
    assert decoded["role"] == "ADMIN"
    assert "exp" in decoded, "token must carry an expiry"


def test_register_then_login_customer_role_claim_matches(routing):
    email = unique_email()
    register_resp = register(routing, email, role="CUSTOMER")
    assert register_resp.status_code == 201, register_resp.text

    login_resp = login(routing, email)
    assert login_resp.status_code == 200, login_resp.text
    decoded = jwt.decode(login_resp.json()["access_token"], JWT_SECRET, algorithms=[JWT_ALGORITHM])
    assert decoded["role"] == "CUSTOMER"


# --- basic register/login/get_user correctness ---

def test_duplicate_email_registration_is_rejected(routing):
    email = unique_email()
    first = register(routing, email)
    assert first.status_code == 201, first.text

    second = register(routing, email)
    assert second.status_code == 409, second.text


def test_login_with_wrong_password_is_rejected(routing):
    email = unique_email()
    assert register(routing, email, password="correct-password-123").status_code == 201

    resp = login(routing, email, password="wrong-password")
    assert resp.status_code == 401, resp.text


def test_login_with_unknown_email_is_rejected(routing):
    resp = login(routing, unique_email())
    assert resp.status_code == 401, resp.text


def test_get_user_round_trip(routing):
    email = unique_email()
    register_resp = register(routing, email, role="ADMIN")
    user_id = register_resp.json()["id"]

    get_resp = routing.get(f"/user/users/{user_id}")
    assert get_resp.status_code == 200, get_resp.text
    body = get_resp.json()
    assert body["id"] == user_id
    assert body["email"] == email
    assert body["role"] == "ADMIN"
    assert "password_hash" not in body, "password hash must never be exposed via the API"


def test_get_unknown_user_404s(routing):
    resp = routing.get(f"/user/users/{uuid.uuid4()}")
    assert resp.status_code == 404, resp.text
