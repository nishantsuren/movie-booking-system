"""Local Cdn Mock service — Phase 0 stub.

Real domain logic lands in later implementation-plan phases. This stub
exists so the full stack can be brought up, health-checked, and routed
through end to end before any business logic is written.
"""
import os

from fastapi import FastAPI

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"

app = FastAPI(title="Local Cdn Mock service")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "local-cdn-mock", "auth_enabled": AUTH_ENABLED}
