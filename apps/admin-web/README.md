# admin-web

Admin SPA (Phase 9, design `docs/design.md` §3/§4.5/§4.6): movie/
theatre/screen/showtime CRUD, plus the seat-layout canvas editor (line/
grid/curve/single-seat placement tools, multi-select bulk edit) with
draft-lock UX (held/blocked-with-holder-identity/stale banners, 25s
heartbeat, explicit release). All backend calls go through the routing
service (never directly to a backend service); the API base URL is
env-configured (`VITE_API_BASE_URL`, see `.env`), never hardcoded.

Served under the `/admin/` path prefix (not the domain root, unlike
customer-web) — see `vite.config.ts`'s `base` option.

## Develop

```bash
npm install
npm run dev          # Vite dev server, fast HMR -- fine for active development
```

The dev server still talks to the real backend (`scripts/dev.sh` from
the repo root) -- there's no mock layer here.

## Build and deploy

The app is actually served from the local CDN mock (`local-cdn-mock`)
under `/admin/`, not the Vite dev server (§3.1) -- the SPA's bundle and
the routing service are different origins, exactly like production.

```bash
npm run build:deploy   # builds, then copies dist/ into ../../local-cdn-mock/static/admin/
```

## E2E tests

Playwright, run against the bundle as actually served
(`http://localhost:8006/admin/`, see `playwright.config.ts`), not the
dev server:

```bash
npm run build:deploy    # refresh the served bundle first
npm run test:e2e
```

Requires the full backend stack up (`scripts/dev.sh`) and a real
Postgres reachable at `THEATRE_DATABASE_URL` (`e2e/db.ts` backdates the
seat-layout lock's heartbeat timestamp directly, for the staleness test
in `concurrent-edit.spec.ts` -- same technique the Python integration
tests and customer-web's E2E suite use) and the customer-web bundle
also deployed and served at `http://localhost:8006/` (the cross-app
test in `canvas-author-and-cross-app-render.spec.ts` navigates there
directly to confirm a layout authored here actually renders in the
other app's seatmap).
