import { defineConfig, devices } from "@playwright/test";

// Tests run against the bundle as actually served -- through local-cdn-mock
// (§3.1), not the Vite dev server -- and call the real backend exclusively
// through the routing service (§3), exactly like the deployed app would.
// Run `npm run build:deploy` before the suite to refresh the served bundle.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  timeout: 60_000,
  use: {
    baseURL: "http://localhost:8006",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
