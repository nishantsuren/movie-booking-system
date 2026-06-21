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
  is_active: boolean;
}

export interface MovieRelease {
  id: string;
  movie_id: string;
  city_id: string;
  release_date: string;
  planned_end_date: string | null;
  actual_end_date: string | null;
}

export interface Theatre {
  id: string;
  city_id: string;
  name: string;
  address: string | null;
}

export interface Screen {
  id: string;
  theatre_id: string;
  name: string;
}

export type SeatLayoutStatus = "DRAFT" | "ACTIVE";

export interface SeatTemplate {
  id: string;
  seat_layout_id: string;
  label: string;
  position_x: number;
  position_y: number;
  seat_type: string;
  price_multiplier: number;
  is_active: boolean;
}

export interface SeatLayout {
  id: string;
  screen_id: string;
  name: string;
  status: SeatLayoutStatus;
  locked_by_user_id: string | null;
  lock_acquired_at: string | null;
  lock_heartbeat_at: string | null;
  seats: SeatTemplate[];
}

export interface Showtime {
  id: string;
  movie_id: string;
  movie_title: string;
  screen_id: string;
  start_time: string;
  is_high_demand: boolean;
  base_price: number;
  is_active: boolean;
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
