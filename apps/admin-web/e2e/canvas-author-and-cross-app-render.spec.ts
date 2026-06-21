import { expect, test } from "@playwright/test";

// Phase 9's strongest available integration check: author a complete,
// realistic seat layout (150+ seats, mixing all four placement tools --
// grid, line, single, curve) through the admin canvas end-to-end,
// publish it, schedule and activate a real showtime against it, then
// load that showtime's seatmap in the *other* app (customer-web,
// Phase 8, a different origin/port entirely) and confirm every seat the
// canvas placed is actually there.
test("author a 150+ seat layout via grid/line/single/curve, publish, schedule a showtime, and verify it renders in customer-web's seatmap", async ({ page }) => {
  const suffix = Date.now();

  await page.goto("theatres");
  await page.getByTestId("theatre-name-input").fill(`Cross-App Theatre ${suffix}`);
  await page.getByTestId("create-theatre-button").click();
  await page.getByText(`Cross-App Theatre ${suffix}`, { exact: true }).click();

  await page.getByTestId("screen-name-input").fill("Main Screen");
  await page.getByTestId("create-screen-button").click();
  await page.getByText("Main Screen", { exact: true }).click();
  await expect(page).toHaveURL(/\/screens\//);
  const screenUrl = page.url();

  // --- author the layout: grid (main block) + line (front row) +
  // single (two accessible seats) + curve (back row) = 152 seats ---
  await page.getByTestId("new-draft-button").click();
  await expect(page).toHaveURL(/\/seat-layouts\/new$/);
  await page.getByTestId("draft-name-input").fill(`Cross-App Layout ${suffix}`);

  // Grid: 10 rows (A-J) x 13 cols = 130 seats.
  await page.getByTestId("tool-grid").click();
  await page.getByTestId("grid-x").fill("0");
  await page.getByTestId("grid-y").fill("0");
  await page.getByTestId("grid-rows").fill("10");
  await page.getByTestId("grid-cols").fill("13");
  await page.getByTestId("grid-row-spacing").fill("1");
  await page.getByTestId("grid-col-spacing").fill("1");
  await page.getByTestId("place-grid-button").click();
  await expect(page.getByTestId("seat-count")).toHaveText("130 seats placed");

  // Line: a front row of 10 seats, ahead of the grid. Prefix "FR" is
  // deliberately not a single letter A-J, so it can't coincidentally
  // collide with one of the grid's own row labels.
  await page.getByTestId("tool-line").click();
  await page.getByTestId("label-prefix-input").fill("FR");
  await page.getByTestId("line-x1").fill("0");
  await page.getByTestId("line-y1").fill("-2");
  await page.getByTestId("line-x2").fill("9");
  await page.getByTestId("line-y2").fill("-2");
  await page.getByTestId("line-count").fill("10");
  await page.getByTestId("place-line-button").click();
  await expect(page.getByTestId("seat-count")).toHaveText("140 seats placed");

  // Single: two wheelchair-accessible seats off to the sides, not
  // aligned to any row/column (§4.5's "single-seat tool conceptually").
  await page.getByTestId("tool-single").click();
  await page.getByTestId("single-x").fill("-2");
  await page.getByTestId("single-y").fill("5");
  await page.getByTestId("single-label").fill("W1");
  await page.getByTestId("place-single-button").click();
  await expect(page.getByTestId("seat-count")).toHaveText("141 seats placed");

  await page.getByTestId("single-x").fill("15");
  await page.getByTestId("single-y").fill("5");
  await page.getByTestId("single-label").fill("W2");
  await page.getByTestId("place-single-button").click();
  await expect(page.getByTestId("seat-count")).toHaveText("142 seats placed");

  // Curve: a curved back row of 10 seats, the non-rectangular section a
  // real theatre might have behind the main block.
  await page.getByTestId("tool-curve").click();
  await page.getByTestId("label-prefix-input").fill("BK");
  await page.getByTestId("curve-x1").fill("0");
  await page.getByTestId("curve-y1").fill("11");
  await page.getByTestId("curve-cx").fill("6");
  await page.getByTestId("curve-cy").fill("13");
  await page.getByTestId("curve-x2").fill("12");
  await page.getByTestId("curve-y2").fill("11");
  await page.getByTestId("curve-count").fill("10");
  await page.getByTestId("place-curve-button").click();
  await expect(page.getByTestId("seat-count")).toHaveText("152 seats placed");

  await page.getByTestId("save-draft-button").click();
  await expect(page).toHaveURL(/\/seat-layouts\/[0-9a-f-]+\/edit$/, { timeout: 10_000 });
  await expect(page.getByTestId("lock-banner-held")).toBeVisible();

  // Spot-check one seat from each tool actually exists on the canvas in
  // edit mode before publishing.
  for (const label of ["A1", "FR1", "W1", "W2", "BK1"]) {
    await expect(page.getByTestId(`canvas-seat-${label}`)).toBeVisible();
  }

  await page.getByTestId("publish-button").click();
  await expect(page.getByText("ACTIVE")).toBeVisible();

  // --- schedule and activate a real showtime against the published layout ---
  await page.goto("movies");
  await page.getByTestId("movie-title-input").fill(`Cross-App Movie ${suffix}`);
  await page.getByTestId("create-movie-button").click();
  await expect(page.getByText(`Cross-App Movie ${suffix}`, { exact: true })).toBeVisible();

  await page.goto(screenUrl);
  await expect(page).toHaveURL(/\/screens\//);

  const [createShowtimeResponse] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/theatre/admin/showtimes") && r.request().method() === "POST"),
    (async () => {
      await page.getByTestId("showtime-movie-select").selectOption({ label: `Cross-App Movie ${suffix}` });
      const future = new Date(Date.now() + 14 * 24 * 60 * 60 * 1000);
      const localDateTime = future.toISOString().slice(0, 16);
      await page.getByTestId("showtime-start-input").fill(localDateTime);
      await page.getByTestId("showtime-price-input").fill("250");
      await page.getByTestId("create-showtime-button").click();
    })(),
  ]);
  const showtime = await createShowtimeResponse.json();

  await page.getByRole("button", { name: "Activate" }).click();
  await expect(page.locator(".badge.active")).toBeVisible();

  // --- the actual cross-app check: load the SAME showtime's seatmap in
  // customer-web (Phase 8, a completely different app/origin) and
  // confirm every seat the canvas placed is really there. ---
  await page.goto(`http://localhost:8006/showtimes/${showtime.id}/seatmap`);
  await expect(page.locator(".booking-summary")).toBeVisible({ timeout: 10_000 });

  const seatButtons = page.locator(".seat");
  await expect(seatButtons).toHaveCount(152);

  for (const label of ["A1", "A13", "J13", "FR1", "FR10", "W1", "W2", "BK1", "BK10"]) {
    await expect(page.locator(`[data-testid="seat-${label}"]`)).toBeVisible();
  }
});
