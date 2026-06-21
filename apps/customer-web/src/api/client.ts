import { API_BASE_URL } from "../config";
import { ApiError } from "../types";
import type { Booking, City, Movie, MovieShowtimesResponse, Payment, Seatmap } from "../types";

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  // Every call goes through the routing service (§3) -- never directly
  // to a backend service, so the same env-configured base URL is the
  // only thing that ever changes between environments.
  const resp = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  const text = await resp.text();
  const data = text ? JSON.parse(text) : null;

  if (!resp.ok) {
    throw new ApiError(resp.status, data);
  }
  return data as T;
}

export const api = {
  listCities: () => request<City[]>("GET", "/theatre/cities"),

  listMovies: (cityId: string) => request<Movie[]>("GET", `/catalog/movies?city=${cityId}`),

  getMovieShowtimes: (movieId: string, cityId: string, date: string) =>
    request<MovieShowtimesResponse>("GET", `/theatre/movies/${movieId}/showtimes?city=${cityId}&date=${date}`),

  getSeatmap: (showtimeId: string) => request<Seatmap>("GET", `/booking/showtimes/${showtimeId}/seatmap`),

  createBooking: (showtimeId: string, seatIds: string[], userId: string) =>
    request<Booking>("POST", "/booking/bookings", { showtime_id: showtimeId, seat_ids: seatIds, user_id: userId }),

  getBooking: (bookingId: string) => request<Booking>("GET", `/booking/bookings/${bookingId}`),

  cancelBooking: (bookingId: string) => request<Booking>("DELETE", `/booking/bookings/${bookingId}`),

  createPayment: (bookingId: string, amount: number) =>
    request<Payment>("POST", "/payment/payments", { booking_id: bookingId, amount }),

  confirmBooking: (bookingId: string, paymentId: string) =>
    request<Booking>("POST", `/booking/bookings/${bookingId}/confirm`, { payment_id: paymentId }),
};
