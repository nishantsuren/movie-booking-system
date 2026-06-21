import { defineConfig, devices } from "@playwright/test";

// Tests run against the bundle as actually served -- through local-cdn-mock
// (§3.1) under the /admin/ prefix, not the Vite dev server -- and call
// the real backend exclusively through the routing service (§3), exactly
// like the deployed app would. Run `npm run build:deploy` before the
// suite to refresh the served bundle.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  timeout: 60_000,
  use: {
    // Trailing slash matters: page.goto(url) resolves via `new URL(url,
    // baseURL)` -- a path WITHOUT a leading "/" (e.g. "movies/x") then
    // resolves relative to this base; a leading "/" would discard the
    // "/admin" sub-path entirely and silently hit customer-web's bundle
    // at the domain root instead (found exactly this while verifying
    // the infra gaps this phase was told to check, not assume).
    baseURL: "http://localhost:8006/admin/",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
