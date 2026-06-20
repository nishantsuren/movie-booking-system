"""Routing service — Phase 0.

Forwards requests by path prefix to backend services (design doc §3, §3.2).
No authentication or rate limiting here by design — that's the slot a real
API gateway fills in production. This is intentionally "dumb": it only
proves the network path between containers works, nothing more.
"""
import os

import httpx
from fastapi import FastAPI, Request, Response

SERVICE_MAP = {
    "catalog": os.getenv("CATALOG_SERVICE_URL", "http://catalog:8000"),
    "theatre": os.getenv("THEATRE_SERVICE_URL", "http://theatre:8000"),
    "booking": os.getenv("BOOKING_SERVICE_URL", "http://booking:8000"),
    "payment": os.getenv("PAYMENT_SERVICE_URL", "http://payment:8000"),
    "user": os.getenv("USER_SERVICE_URL", "http://user:8000"),
}
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"

app = FastAPI(title="Routing service")
client = httpx.AsyncClient(timeout=10.0)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "routing", "auth_enabled": AUTH_ENABLED}


@app.api_route("/{prefix}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def forward(prefix: str, path: str, request: Request) -> Response:
    target_base = SERVICE_MAP.get(prefix)
    if target_base is None:
        return Response(content=f'{{"detail":"no service registered for prefix \\"{prefix}\\""}}',
                         status_code=404, media_type="application/json")

    upstream = await client.request(
        method=request.method,
        url=f"{target_base}/{path}",
        params=request.query_params,
        content=await request.body(),
        headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
    )
    return Response(content=upstream.content, status_code=upstream.status_code,
                     media_type=upstream.headers.get("content-type"))
