import { expect, test } from "@playwright/test";
import { buildBookableShowtime, makeSeat } from "./helpers";

// Phase 8 verification: "a seat-conflict scenario (two tabs/sessions
// racing for the same seat) ... confirm each error state surfaces
// clearly in the UI rather than failing silently." Two real, independent
// browser contexts (separate cookie jars/storage -- i.e. genuinely
// different sessions, not just two tabs sharing state) race a real
// POST /bookings for the identical seat against the real backend. The
// backend's own correctness (Redis lock + Postgres's PK-scoped
// conditional update) is already proven under much harsher concurrency
// in Phases 4-6; this test is about the UI surfacing the loser's 409
// clearly, not re-proving the backend.
test("two sessions racing for the same seat: one succeeds, the other sees a clear conflict message", async ({
  browser,
}) => {
  const fixture = await buildBookableShowtime([makeSeat("A1", 0, 0)], 100);
  const seatmapUrl = `/showtimes/${fixture.showtimeId}/seatmap`;

  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const pageA = await contextA.newPage();
  const pageB = await contextB.newPage();

  const isBookingPost = (r: import("@playwright/test").Response) =>
    r.url().includes("/booking/bookings") && r.request().method() === "POST";

  try {
    await pageA.goto(seatmapUrl);
    await pageB.goto(seatmapUrl);

    await pageA.getByTestId("seat-A1").click();
    await pageB.getByTestId("seat-A1").click();

    // Wait on the actual POST /bookings response, not a URL change --
    // both pages start on /seatmap, which trivially "matches" a loose
    // URL-based wait before the race even happens, so that's not a valid
    // signal here. The loser never navigates at all; the response status
    // is the only unambiguous signal for either side.
    const [responseA, responseB] = await Promise.all([
      pageA.waitForResponse(isBookingPost),
      pageB.waitForResponse(isBookingPost),
      pageA.getByTestId("proceed-button").click(),
      pageB.getByTestId("proceed-button").click(),
    ]);

    const statusA = responseA.status();
    const statusB = responseB.status();

    // Exactly one 201 (winner) and one 409 (loser) -- never both-win,
    // never both-lose.
    expect([statusA, statusB].sort()).toEqual([201, 409]);

    const winnerPage = statusA === 201 ? pageA : pageB;
    const loserPage = statusA === 201 ? pageB : pageA;

    await expect(winnerPage).toHaveURL(/\/checkout$/);

    // The loser stays on the seatmap and sees a clear, specific message --
    // not a silent failure, not a generic crash.
    await expect(loserPage).toHaveURL(/\/seatmap$/);
    const errorBanner = loserPage.locator(".error-banner");
    await expect(errorBanner).toBeVisible();
    await expect(errorBanner).toContainText(/seat/i);

    // And the seat the loser tried for is no longer selectable -- the
    // seatmap re-fetched after the conflict and reflects reality.
    await expect(loserPage.getByTestId("seat-A1")).toHaveAttribute("data-status", "LOCKED");
  } finally {
    await contextA.close();
    await contextB.close();
  }
});
