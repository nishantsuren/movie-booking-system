export interface City {
  id: string;
  name: string;
  state: string | null;
}

export interface Movie {
  id: string;
  title: string;
  description: string | null;
  duration_minutes: number | null;
  language: string | null;
  poster_asset_id: string | null;
}

export interface ShowtimeListItem {
  id: string;
  movie_id: string;
  screen_id: string;
  start_time: string;
  is_high_demand: boolean;
  base_price: number;
  movie_title: string;
  screen_name: string;
  theatre_name: string;
}

export interface MovieShowtimesResponse {
  movie: Movie;
  showtimes: ShowtimeListItem[];
}

export type SeatStatus = "AVAILABLE" | "LOCKED" | "BOOKED";

export interface Seat {
  id: string;
  label: string;
  x: number;
  y: number;
  seat_type: string;
  price: number;
  status: SeatStatus;
}

export interface Seatmap {
  showtime_id: string;
  movie_title: string;
  theatre_name: string;
  screen_name: string;
  start_time: string;
  base_price: number;
  seats: Seat[];
}

export type BookingStatus = "PENDING" | "CONFIRMED" | "EXPIRED" | "CANCELLED";

export interface Booking {
  id: string;
  idempotency_key: string;
  user_id: string;
  showtime_id: string;
  movie_title: string;
  seat_labels: string;
  price_paid: number;
  status: BookingStatus;
  expires_at: string;
}

export interface Payment {
  id: string;
  booking_id: string;
  amount: number;
  status: string;
}

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, body: unknown) {
    super(typeof body === "object" && body !== null && "detail" in body ? JSON.stringify((body as { detail: unknown }).detail) : `request failed with status ${status}`);
    this.status = status;
    this.body = body;
  }
}
