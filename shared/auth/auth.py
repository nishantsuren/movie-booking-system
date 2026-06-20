"""Auth middleware with the AUTH_ENABLED toggle (design doc §3.2).

Every backend service uses this independently — never trusting that a
request already passed through an authenticated gateway (defense in
depth). AUTH_ENABLED is read fresh on every call, not cached at import
time, specifically so it stays testable and so a config change takes
effect without requiring a process restart in environments where that
matters.
"""
import os
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request

JWT_ALGORITHM = "HS256"


def _auth_enabled() -> bool:
    return os.getenv("AUTH_ENABLED", "false").lower() == "true"


def _jwt_secret() -> str:
    return os.getenv("JWT_SECRET", "dev-secret-change-in-production")


class AuthContext:
    def __init__(self, user_id: Optional[str], role: Optional[str]):
        self.user_id = user_id
        self.role = role


def get_auth_context(request: Request) -> AuthContext:
    """FastAPI dependency. With AUTH_ENABLED=false, returns an empty
    context and never rejects a request — local dev needs zero auth setup.
    With AUTH_ENABLED=true, validates the JWT and extracts the role claim.
    """
    if not _auth_enabled():
        return AuthContext(user_id=None, role=None)

    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing or malformed Authorization header")

    token = header.removeprefix("Bearer ")
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"invalid token: {exc}")

    return AuthContext(user_id=payload.get("sub"), role=payload.get("role"))


def require_role(required_role: str):
    """Dependency factory for admin-only endpoints (Appendix C). A no-op
    when AUTH_ENABLED is false, consistent with get_auth_context above.
    """

    def _dependency(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if not _auth_enabled():
            return ctx
        if ctx.role != required_role:
            raise HTTPException(status_code=403, detail=f"requires role: {required_role}")
        return ctx

    return _dependency
