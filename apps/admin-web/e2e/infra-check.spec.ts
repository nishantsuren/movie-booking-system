import { expect, test } from "@playwright/test";

// Phase 9 explicitly required verifying these three -- not assuming
// Phase 8's customer-web fixes automatically cover admin-web's own
// origin/port and path prefix. CORS specifically needs a real browser:
// curl never enforces it, so a curl 200 (already checked manually)
// proves nothing about whether the browser will actually accept the
// response.
//
// Note: paths below are relative (no leading "/") deliberately -- a
// leading "/" resolves against baseURL's *origin*, discarding the
// "/admin" sub-path entirely (this is exactly how this suite first
// caught itself loading customer-web's bundle from the root instead of
// admin-web's, while writing this very check).
test("CORS allows admin-web's origin to call the routing service", async ({ page }) => {
  await page.goto("");
  const result = await page.evaluate(async () => {
    const resp = await fetch("http://localhost:8000/theatre/cities");
    return { ok: resp.ok, status: resp.status };
  });
  expect(result.ok).toBe(true);
  expect(result.status).toBe(200);
});

test("SPA fallback serves index.html for a direct navigation to a client-side /admin route", async ({ page }) => {
  const resp = await page.goto("movies/some-fake-id-not-a-real-file");
  expect(resp?.status()).toBe(200);
  // Confirms React actually mounted and rendered something, not just
  // that *a* 200 HTML response came back.
  await expect(page.locator(".app-header")).toBeVisible();
});

test("built JS/CSS asset URLs resolve under app-assets/, not the colliding assets/ path", async ({ page }) => {
  await page.goto("");
  const scriptSrc = await page.locator('script[type="module"]').getAttribute("src");
  expect(scriptSrc).toContain("/admin/app-assets/");
  const scriptResp = await page.request.get(`http://localhost:8006${scriptSrc}`);
  expect(scriptResp.status()).toBe(200);
  expect(scriptResp.headers()["content-type"]).toContain("javascript");
});
