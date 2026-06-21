import { API_BASE_URL } from "../config";
import { getAdminId } from "../lib/adminId";
import { ApiError } from "../types";
import type { City, Movie, MovieRelease, Screen, SeatLayout, SeatTemplate, Showtime, Theatre } from "../types";

async function request<T>(method: string, path: string, body?: unknown, extraHeaders?: Record<string, string>): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      "X-Admin-User-Id": getAdminId(),
      ...extraHeaders,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  const text = await resp.text();
  const data = text ? JSON.parse(text) : null;

  if (!resp.ok) {
    throw new ApiError(resp.status, data);
  }
  return data as T;
}

export interface SeatInput {
  id: string;
  label: string;
  x: number;
  y: number;
  seat_type: string;
  price_multiplier: number;
}

export const api = {
  // --- cities / movies (catalog + theatre) ---
  listCities: () => request<City[]>("GET", "/theatre/cities"),
  listMovies: () => request<Movie[]>("GET", "/catalog/admin/movies"),
  getMovie: (movieId: string) => request<Movie>("GET", `/catalog/movies/${movieId}`),
  createMovie: (body: { title: string; description?: string; duration_minutes?: number; language?: string }) =>
    request<Movie>("POST", "/catalog/admin/movies", body),
  updateMovie: (movieId: string, body: Partial<Pick<Movie, "title" | "description" | "duration_minutes" | "language" | "is_active">>) =>
    request<Movie>("PUT", `/catalog/admin/movies/${movieId}`, body),
  listReleases: (movieId: string) => request<MovieRelease[]>("GET", `/catalog/admin/movies/${movieId}/releases`),
  createRelease: (movieId: string, body: { city_id: string; release_date: string; planned_end_date?: string }) =>
    request<MovieRelease>("POST", `/catalog/admin/movies/${movieId}/releases`, body),

  // --- theatres / screens ---
  listTheatres: () => request<Theatre[]>("GET", "/theatre/theatres"),
  getTheatre: (theatreId: string) => request<Theatre>("GET", `/theatre/theatres/${theatreId}`),
  createTheatre: (body: { city_id: string; name: string; address?: string }) =>
    request<Theatre>("POST", "/theatre/admin/theatres", body),
  listScreens: (theatreId: string) => request<Screen[]>("GET", `/theatre/admin/theatres/${theatreId}/screens`),
  createScreen: (theatreId: string, body: { name: string }) =>
    request<Screen>("POST", `/theatre/admin/theatres/${theatreId}/screens`, body),

  // --- seat layouts + draft lock (§4.5, §4.6) ---
  listSeatLayouts: (screenId: string) => request<SeatLayout[]>("GET", `/theatre/admin/screens/${screenId}/seat-layouts`),
  getSeatLayout: (layoutId: string) => request<SeatLayout>("GET", `/theatre/admin/seat-layouts/${layoutId}`),
  createDraft: (screenId: string, name: string, seats: SeatInput[]) =>
    request<SeatLayout>("POST", "/theatre/admin/seat-layouts/draft", { screen_id: screenId, name, seats }),
  acquireLock: (draftId: string) => request<SeatLayout>("POST", `/theatre/admin/seat-layouts/draft/${draftId}/lock`),
  releaseLock: (draftId: string) => request<void>("DELETE", `/theatre/admin/seat-layouts/draft/${draftId}/lock`),
  updateSeat: (draftId: string, seatId: string, body: Partial<Pick<SeatTemplate, "label" | "seat_type" | "price_multiplier" | "is_active">> & { x?: number; y?: number }) =>
    request<SeatTemplate>("PATCH", `/theatre/admin/seat-layouts/draft/${draftId}/seats/${seatId}`, body),
  bulkUpdateSeats: (draftId: string, seatIds: string[], body: Partial<Pick<SeatTemplate, "seat_type" | "price_multiplier" | "is_active">>) =>
    request<SeatTemplate[]>("PATCH", `/theatre/admin/seat-layouts/draft/${draftId}/seats`, { seat_ids: seatIds, ...body }),
  publishDraft: (draftId: string) => request<SeatLayout>("POST", `/theatre/admin/seat-layouts/draft/${draftId}/publish`),
  cloneLayout: (layoutId: string, targetScreenId: string) =>
    request<SeatLayout>("POST", `/theatre/admin/seat-layouts/${layoutId}/clone`, { target_screen_id: targetScreenId }),

  // --- showtimes ---
  listShowtimes: (screenId: string) => request<Showtime[]>("GET", `/theatre/admin/screens/${screenId}/showtimes`),
  createShowtime: (body: { movie_id: string; movie_title: string; screen_id: string; start_time: string; base_price: number; is_high_demand?: boolean }) =>
    request<Showtime>("POST", "/theatre/admin/showtimes", body),
  activateShowtime: (showtimeId: string) => request<Showtime>("POST", `/theatre/admin/showtimes/${showtimeId}/activate`),
  deactivateShowtime: (showtimeId: string) => request<Showtime>("DELETE", `/theatre/admin/showtimes/${showtimeId}`),
};
