import { expect, test } from "@playwright/test";
import { backdateLockHeartbeat } from "./db";

// Phase 9's other required check: two simulated admin sessions against
// the same draft. Two genuinely independent browser contexts (separate
// storage -- distinct admin identities, via lib/adminId.ts's
// localStorage-persisted UUID, exactly the way two different admins
// would be distinct) -- not just two tabs sharing one session.
//
// The backend's own lock correctness (acquire/heartbeat/staleness/
// ownership-not-just-existence) is already proven in
// tests/integration/test_phase2.py. This is about the *frontend*:
// does the blocked session see a clear message naming the holder, and
// does it correctly recover once the lock frees up either way.

async function createAndOpenDraft(page: import("@playwright/test").Page, suffix: string): Promise<string> {
  await page.goto("theatres");
  await page.getByTestId("theatre-name-input").fill(`Concurrent Theatre ${suffix}`);
  await page.getByTestId("create-theatre-button").click();
  await page.getByText(`Concurrent Theatre ${suffix}`, { exact: true }).click();

  await page.getByTestId("screen-name-input").fill("Screen 1");
  await page.getByTestId("create-screen-button").click();
  await page.getByText("Screen 1", { exact: true }).click();

  await page.getByTestId("new-draft-button").click();
  await page.getByTestId("draft-name-input").fill(`Concurrent Layout ${suffix}`);
  await page.getByTestId("tool-single").click();
  await page.getByTestId("single-x").fill("0");
  await page.getByTestId("single-y").fill("0");
  await page.getByTestId("single-label").fill("A1");
  await page.getByTestId("place-single-button").click();
  await page.getByTestId("save-draft-button").click();
  await expect(page).toHaveURL(/\/seat-layouts\/[0-9a-f-]+\/edit$/, { timeout: 10_000 });
  await expect(page.getByTestId("lock-banner-held")).toBeVisible();

  return page.url();
}

test("second admin session is blocked with the holder's identity, and can proceed once the first explicitly releases", async ({ browser }) => {
  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const pageA = await contextA.newPage();
  const pageB = await contextB.newPage();

  try {
    const draftUrl = await createAndOpenDraft(pageA, `${Date.now()}-release`);

    await pageB.goto(draftUrl);
    await expect(pageB.getByTestId("lock-banner-blocked")).toBeVisible();
    await expect(pageB.getByTestId("lock-banner-blocked")).toContainText("Locked by admin");
    // The canvas is read-only while blocked -- no select/edit possible.
    await expect(pageB.getByTestId("canvas-seat-A1")).toBeDisabled();
    await expect(pageB.getByTestId("publish-button")).toBeDisabled();

    await pageA.getByTestId("release-lock-button").click();
    await expect(pageA.getByTestId("lock-banner-released")).toBeVisible();

    await pageB.getByTestId("retry-lock-button").click();
    await expect(pageB.getByTestId("lock-banner-held")).toBeVisible();
    await expect(pageB.getByTestId("canvas-seat-A1")).toBeEnabled();
  } finally {
    await contextA.close();
    await contextB.close();
  }
});

test("second admin session is blocked, and can proceed once the first goes stale", async ({ browser }) => {
  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const pageA = await contextA.newPage();
  const pageB = await contextB.newPage();

  try {
    const draftUrl = await createAndOpenDraft(pageA, `${Date.now()}-stale`);
    const layoutId = draftUrl.match(/seat-layouts\/([0-9a-f-]+)\/edit/)![1];

    await pageB.goto(draftUrl);
    await expect(pageB.getByTestId("lock-banner-blocked")).toBeVisible();

    // pageA never releases -- simulate it going quiet for longer than
    // the ~2 minute staleness threshold via direct DB backdating, same
    // technique test_phase2.py uses (no test-only clock-mocking hook in
    // production code).
    await backdateLockHeartbeat(layoutId, 3);

    await pageB.getByTestId("retry-lock-button").click();
    await expect(pageB.getByTestId("lock-banner-held")).toBeVisible();

    // pageA, unaware its lock went stale, must not still be able to push
    // an edit through (§4.6: "not merely that a lock exists" -- ownership
    // is re-checked on every mutating call). Its own heartbeat is paused
    // here (no interval has fired since the page never re-rendered into
    // a state requiring one beyond mount), so its next action surfaces
    // the rejection.
    await pageA.getByTestId("publish-button").click();
    await expect(pageA.getByTestId("lock-banner-stale")).toBeVisible();
  } finally {
    await contextA.close();
    await contextB.close();
  }
});
