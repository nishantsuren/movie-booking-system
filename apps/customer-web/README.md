# customer-web

Customer SPA (Phase 8, design `docs/design.md` §3/§16): browse movies →
showtimes → seatmap → seat selection → booking → mocked payment →
confirmation. All backend calls go through the routing service (never
directly to a backend service); the API base URL is env-configured
(`VITE_API_BASE_URL`, see `.env`), never hardcoded.

## Develop

```bash
npm install
npm run dev          # Vite dev server, fast HMR -- fine for active development
```

The dev server still talks to the real backend (`scripts/dev.sh` from
the repo root) -- there's no mock layer here.

## Build and deploy

The app is actually served from the local CDN mock (`local-cdn-mock`),
not the Vite dev server (§3.1) -- the SPA's bundle and the routing
service are different origins, exactly like production.

```bash
npm run build:deploy   # builds, then copies dist/ into ../../local-cdn-mock/static/customer/
```

## E2E tests

Playwright, run against the bundle as actually served (`http://localhost:8006`,
see `playwright.config.ts`), not the dev server:

```bash
npm run build:deploy    # refresh the served bundle first
npm run test:e2e
```

Requires the full backend stack up (`scripts/dev.sh`) and a real
Postgres reachable at `BOOKING_DATABASE_URL` (`e2e/db.ts` backdates
timestamps directly for the countdown/grace-window tests, the same
technique the Python integration tests use). One test
(`countdown-grace-window.spec.ts`'s "sweep has actually reclaimed"
case) waits ~29 real seconds for the live `reconciliation-sweep-*`
workers to run -- not a flake, it's actually waiting for the thing.
