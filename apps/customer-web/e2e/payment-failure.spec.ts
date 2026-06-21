import { expect, test } from "@playwright/test";
import { buildBookableShowtime, makeSeat } from "./helpers";

// Phase 8 verification: "a payment failure -- confirm the UI surfaces
// this clearly rather than failing silently."
//
// Payment is mocked to always succeed (Appendix A) -- there is no real
// "card declined" path to provoke from the backend. tests/integration/
// test_phase5.py already proves the *backend's* failure handling for
// real (stopping the actual payment service process, confirming the
// circuit breaker trips and the booking stays PENDING). This test's job
// is different: it's testing the *frontend's* rendering of a failure
// response, which is most reliably and deterministically done via
// Playwright's route interception rather than re-orchestrating a real
// process outage from a browser test -- standard practice for E2E
// frontend error-state coverage, not a shortcut around the real check
// (which already exists, in Python, against the real process).
test("payment failure surfaces a clear error and leaves the booking untouched", async ({ page }) => {
  const fixture = await buildBookableShowtime([makeSeat("A1", 0, 0)], 100);

  await page.route("**/payment/payments", (route) =>
    route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "simulated payment processor failure" }) }),
  );

  await page.goto(`/showtimes/${fixture.showtimeId}/seatmap`);
  await page.getByTestId("seat-A1").click();
  await page.getByTestId("proceed-button").click();
  await expect(page).toHaveURL(/\/checkout$/);

  const bookingId = page.url().match(/\/bookings\/([0-9a-f-]+)\/checkout/)?.[1];
  expect(bookingId).toBeTruthy();

  await page.getByTestId("pay-button").click();

  const errorBanner = page.getByTestId("checkout-error");
  await expect(errorBanner).toBeVisible();
  await expect(errorBanner).toContainText(/could not be completed/i);

  // Still on checkout, not silently redirected anywhere, and the pay
  // button is usable again (not stuck disabled).
  await expect(page).toHaveURL(/\/checkout$/);
  await expect(page.getByTestId("pay-button")).toBeEnabled();

  // The booking itself must be untouched -- still PENDING, not
  // confirmed and not corrupted by the failed attempt.
  const bookingResp = await page.request.get(`http://localhost:8000/booking/bookings/${bookingId}`);
  expect(bookingResp.ok()).toBe(true);
  const booking = await bookingResp.json();
  expect(booking.status).toBe("PENDING");
});
