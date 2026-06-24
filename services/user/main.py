"""User service — Phase 7.

USER (`app_user` table -- "user" is a reserved word in Postgres) with
role (CUSTOMER|ADMIN, §3); register/login issuing a JWT with the role
claim, reusing shared/auth/auth.py's JWT_SECRET/JWT_ALGORITHM exactly --
no second auth scheme. `AUTH_ENABLED` stays false everywhere else until
Phase 10 deliberately flips it (this phase only establishes the
capability).
"""
from datetime import datetime, timezone
from uuid import UUID

import bcrypt
import jwt
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from config import AUTH_ENABLED, TOKEN_TTL, VALID_ROLES
from db import get_db
from shared.auth.auth import JWT_ALGORITHM, _jwt_secret
from shared.idempotency.idempotency import IdempotentWriter

app = FastAPI(title="User service")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "user", "auth_enabled": AUTH_ENABLED}


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)
    role: str = "CUSTOMER"


class LoginRequest(BaseModel):
    email: str
    password: str


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def _issue_token(user_row: dict) -> str:
    """Same secret/algorithm/claim shape shared/auth/auth.py's
    get_auth_context already expects (`sub` = user id, `role`) -- this is
    the only place in the system that mints a token, everything else
    only ever verifies one."""
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_row["id"]), "role": user_row["role"], "iat": now, "exp": now + TOKEN_TTL}
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def _user_public(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "role": row["role"],
        "created_at": row["created_at"].isoformat(),
    }


@app.post("/auth/register", status_code=201)
def register(body: RegisterRequest, conn=Depends(get_db)) -> dict:
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {VALID_ROLES}")

    email = body.email.strip().lower()
    writer = IdempotentWriter(conn)
    row, created = writer.insert_or_get(
        "app_user",
        {"email": email, "password_hash": _hash_password(body.password), "role": body.role},
        idempotency_key_column="email",
    )
    if not created:
        # Deliberately NOT the generic §11.1 "return the existing row"
        # behavior: email is unique-but-not-secret, and a retry can't be
        # distinguished from a different person targeting a taken email
        # (password isn't part of the dedup key) -- silently handing back
        # the first registrant's record would be a real account-confusion
        # bug, not a harmless idempotent replay.
        raise HTTPException(status_code=409, detail="email already registered")
    return _user_public(row)


@app.post("/auth/login")
def login(body: LoginRequest, conn=Depends(get_db)) -> dict:
    email = body.email.strip().lower()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM app_user WHERE email = %s", (email,))
        row = cur.fetchone()
    if row is None or not _verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid email or password")
    return {
        "access_token": _issue_token(row),
        "token_type": "bearer",
        "user_id": str(row["id"]),
        "role": row["role"],
    }


@app.get("/users/{user_id}")
def get_user(user_id: UUID, conn=Depends(get_db)) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM app_user WHERE id = %s", (str(user_id),))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")
    return _user_public(row)
