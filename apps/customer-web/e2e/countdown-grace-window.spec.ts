import { expect, test } from "@playwright/test";
import { backdateBookingExpiry } from "./db";
import { buildBookableShowtime, makeSeat } from "./helpers";

// Phase 8 verification: the countdown reaching zero does NOT mean
// confirm is now guaranteed to fail (design v14) -- confirm only fails
// once the sweep worker has actually reclaimed the seat (every 15-30s),
// so a payment landing just past the nominal deadline can still
// legitimately succeed. Both real outcomes are tested here, not just
// "expired -> fails": (1) past nominal but unswept -> confirm succeeds;
// (2) past nominal AND the real sweep worker has actually run (waited
// for here, not mocked -- the same live reconciliation-sweep-1/2
// processes scripts/dev.sh starts) -> confirm fails, clearly.
//
// Per the product decision: the UI communicates the grace window
// (data-testid="grace-window-message") rather than asserting a hard
// deadline it can't actually guarantee.

async function createBookingViaUi(page: import("@playwright/test").Page, showtimeId: string): Promise<string> {
  await page.goto(`/showtimes/${showtimeId}/seatmap`);
  await page.getByTestId("seat-A1").click();
  await page.getByTestId("proceed-button").click();
  await expect(page).toHaveURL(/\/checkout$/);
  return page.url().match(/\/bookings\/([0-9a-f-]+)\/checkout/)![1];
}

test("confirm can still succeed just past the displayed countdown, before the sweep runs", async ({ page }) => {
  const fixture = await buildBookableShowtime([makeSeat("A1", 0, 0)], 80);
  const bookingId = await createBookingViaUi(page, fixture.showtimeId);

  // 2 seconds past nominal -- nowhere near the sweep's 15-30s cadence,
  // so the sweep cannot plausibly have run yet.
  await backdateBookingExpiry(bookingId, 2);
  await page.reload();

  const countdown = page.getByTestId("countdown");
  await expect(countdown).toHaveAttribute("data-past-nominal", "true");
  await expect(page.getByTestId("grace-window-message")).toBeVisible();

  await page.getByTestId("pay-button").click();
  await expect(page).toHaveURL(/\/confirmation$/, { timeout: 10_000 });
  await expect(page.getByTestId("confirmation-heading")).toHaveText("Booking confirmed!");
});

test("confirm fails once the sweep has actually reclaimed the seat", async ({ page }) => {
  test.setTimeout(60_000);

  const fixture = await buildBookableShowtime([makeSeat("A1", 0, 0)], 80);
  const bookingId = await createBookingViaUi(page, fixture.showtimeId);

  // Comfortably in the past so it's a valid sweep candidate the moment
  // any pass runs. Then actually wait for the live sweep (poll interval
  // 20s, scripts/dev.sh runs 2 real replicas) to do its real work --
  // no shortcuts, no test-only hook calling the sweep directly.
  await backdateBookingExpiry(bookingId, 90);
  await page.waitForTimeout(28_000);

  await page.reload();
  await page.getByTestId("pay-button").click();

  const errorBanner = page.getByTestId("checkout-error");
  await expect(errorBanner).toBeVisible();
  await expect(errorBanner).toContainText(/expired/i);
  await expect(errorBanner).toContainText(/select your seats again/i);
});
