import { expect, test } from "@playwright/test";
import { buildBookableShowtime, makeSeat } from "./helpers";

// Phase 8 verification: "E2E browser test suite covering the full happy
// path against the real backend built in Phases 1-7." Browse -> pick a
// showtime -> select seats -> create a PENDING booking -> mocked payment
// -> confirm -> CONFIRMED, against the real stack the whole way, loaded
// from the local CDN mock (not the Vite dev server) and calling
// exclusively through the routing service.
test("browse to confirmation, full happy path against the real backend", async ({ page }) => {
  const fixture = await buildBookableShowtime(
    [makeSeat("A1", 0, 0), makeSeat("A2", 1, 0, 1.5), makeSeat("A3", 2, 0)],
    100,
  );

  await page.goto(`/movies/${fixture.movieId}/showtimes?city=${fixture.cityId}&date=${fixture.dateOnly}`);
  await expect(page.getByRole("heading", { name: fixture.movieTitle })).toBeVisible();

  await page.locator(".showtime-row").first().click();
  await expect(page).toHaveURL(/\/seatmap$/);

  // A1 (price 100) + A2 (price 150, multiplier 1.5) selected; A3 left alone.
  await page.getByTestId("seat-A1").click();
  await page.getByTestId("seat-A2").click();
  await expect(page.locator(".booking-summary")).toContainText("2 seats");
  await expect(page.locator(".booking-summary")).toContainText("250.00");

  await page.getByTestId("proceed-button").click();
  await expect(page).toHaveURL(/\/bookings\/[0-9a-f-]+\/checkout$/);
  await expect(page.locator(".booking-summary")).toContainText(fixture.movieTitle);
  await expect(page.locator(".booking-summary")).toContainText("A1,A2");
  await expect(page.getByTestId("countdown")).toBeVisible();
  await expect(page.getByTestId("countdown")).not.toHaveAttribute("data-past-nominal", "true");

  await page.getByTestId("pay-button").click();
  await expect(page).toHaveURL(/\/bookings\/[0-9a-f-]+\/confirmation$/, { timeout: 10_000 });
  await expect(page.getByTestId("confirmation-heading")).toHaveText("Booking confirmed!");
  await expect(page.locator(".booking-summary")).toContainText("250.00");

  // Seats are genuinely BOOKED now, not just a UI-only state -- reload the
  // seatmap and confirm the backend agrees.
  await page.goto(`/showtimes/${fixture.showtimeId}/seatmap`);
  await expect(page.getByTestId("seat-A1")).toHaveAttribute("data-status", "BOOKED");
  await expect(page.getByTestId("seat-A2")).toHaveAttribute("data-status", "BOOKED");
  await expect(page.getByTestId("seat-A3")).toHaveAttribute("data-status", "AVAILABLE");
});
