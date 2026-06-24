"""Routing service — Phase 0.

Forwards requests by path prefix to backend services (design doc §3, §3.2).
No authentication or rate limiting here by design — that's the slot a real
API gateway fills in production. This is intentionally "dumb": it only
proves the network path between containers works, nothing more.
"""
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from config import AUTH_ENABLED, SERVICE_MAP

app = FastAPI(title="Routing service")
client = httpx.AsyncClient(timeout=10.0)

# Phase 8: the customer SPA (served from the local CDN mock, a different
# origin/port than this service) calls through here for every backend
# request (§3) -- without CORS headers the browser blocks the response
# outright, even though curl/server-to-server calls never hit this at
# all (CORS is a browser-only restriction). Wide open is fine for now:
# no auth exists yet (AUTH_ENABLED=false everywhere, Phase 10), nothing
# here relies on cookies/credentials, and a real API gateway replaces
# this entire service's slot in production (§3.2, §15) with its own
# CORS policy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
