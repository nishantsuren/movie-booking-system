// Fixture creation against the real backend, through the routing
// service -- the TypeScript equivalent of tests/integration/*.py's
// helpers (make_screen, publish_layout, create_showtime, etc.), since
// these E2E tests need their own real movies/theatres/showtimes and
// can't reuse the Python suite's fixtures across processes/languages.
import { randomUUID } from "node:crypto";

const ROUTING_BASE = "http://localhost:8000";

async function call(method: string, path: string, body?: unknown, headers?: Record<string, string>) {
  const resp = await fetch(`${ROUTING_BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...headers },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await resp.text();
  const data = text ? JSON.parse(text) : null;
  if (!resp.ok) {
    throw new Error(`${method} ${path} -> ${resp.status}: ${text}`);
  }
  return data;
}

export function unique(label: string): string {
  return `${label} ${randomUUID().slice(0, 8)}`;
}

export async function getFirstCity(): Promise<{ id: string; name: string }> {
  const cities = await call("GET", "/theatre/cities");
  return cities[0];
}

export async function createScreenInCity(cityId: string): Promise<{ theatreId: string; screenId: string }> {
  const theatre = await call("POST", "/theatre/admin/theatres", { city_id: cityId, name: unique("E2E Theatre") });
  const screen = await call("POST", `/theatre/admin/theatres/${theatre.id}/screens`, { name: "Screen 1" });
  return { theatreId: theatre.id, screenId: screen.id };
}

export function makeSeat(label: string, x: number, y: number, priceMultiplier = 1.0) {
  return { id: randomUUID(), label, x, y, seat_type: "STANDARD", price_multiplier: priceMultiplier };
}

export async function publishLayout(screenId: string, seats: ReturnType<typeof makeSeat>[]): Promise<void> {
  const draft = await call("POST", "/theatre/admin/seat-layouts/draft", {
    screen_id: screenId,
    name: unique("Layout"),
    seats,
  });
  const adminId = randomUUID();
  await call("POST", `/theatre/admin/seat-layouts/draft/${draft.id}/lock`, undefined, { "X-Admin-User-Id": adminId });
  await call("POST", `/theatre/admin/seat-layouts/draft/${draft.id}/publish`, undefined, {
    "X-Admin-User-Id": adminId,
  });
}

export async function createMovieWithRelease(cityId: string): Promise<{ movieId: string; title: string }> {
  const title = unique("E2E Movie");
  const movie = await call("POST", "/catalog/admin/movies", { title, duration_minutes: 120, language: "en" });
  await call("POST", `/catalog/admin/movies/${movie.id}/releases`, {
    city_id: cityId,
    release_date: "2020-01-01",
    planned_end_date: "2030-01-01",
  });
  return { movieId: movie.id, title };
}

export async function createAndActivateShowtime(
  movieId: string,
  movieTitle: string,
  screenId: string,
  basePrice: number,
  startTime: string,
): Promise<string> {
  const showtime = await call("POST", "/theatre/admin/showtimes", {
    movie_id: movieId,
    movie_title: movieTitle,
    screen_id: screenId,
    start_time: startTime,
    base_price: basePrice,
  });
  await call("POST", `/theatre/admin/showtimes/${showtime.id}/activate`);
  return showtime.id;
}

export interface Fixture {
  cityId: string;
  movieId: string;
  movieTitle: string;
  showtimeId: string;
  /** ISO date (YYYY-MM-DD) the showtime falls on, in UTC -- ShowtimesPage
   * filters by date, so tests navigating straight to the seatmap don't
   * need this, but anything going through the showtimes list page does. */
  dateOnly: string;
}

/** One movie, one theatre/screen with the given seats, one active showtime
 * far enough in the future that nothing else collides with it. */
export async function buildBookableShowtime(
  seats: ReturnType<typeof makeSeat>[],
  basePrice = 100,
  startTime?: string,
): Promise<Fixture> {
  const city = await getFirstCity();
  const { screenId } = await createScreenInCity(city.id);
  await publishLayout(screenId, seats);
  const { movieId, title } = await createMovieWithRelease(city.id);
  const resolvedStartTime = startTime ?? new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();
  const showtimeId = await createAndActivateShowtime(movieId, title, screenId, basePrice, resolvedStartTime);
  return { cityId: city.id, movieId, movieTitle: title, showtimeId, dateOnly: resolvedStartTime.slice(0, 10) };
}
