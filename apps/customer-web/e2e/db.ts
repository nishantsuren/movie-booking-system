// Direct DB access for time manipulation only -- the same technique
// tests/integration/test_phase2.py/test_phase5.py use (backdate a
// timestamp directly in Postgres) rather than a test-only clock-mocking
// hook in production code. No application logic lives here.
import { Client } from "pg";

const BOOKING_DB_URL =
  process.env.BOOKING_DATABASE_URL ?? "postgresql://movieticket:movieticket_dev_password@localhost:5433/booking_db";

export async function backdateBookingExpiry(bookingId: string, secondsAgo: number): Promise<void> {
  const client = new Client({ connectionString: BOOKING_DB_URL });
  await client.connect();
  try {
    await client.query("UPDATE booking SET expires_at = now() - interval '1 second' * $1 WHERE id = $2", [
      secondsAgo,
      bookingId,
    ]);
    await client.query(
      "UPDATE showtime_seat SET lock_expires_at = now() - interval '1 second' * $1 WHERE locked_by_booking_id = $2",
      [secondsAgo, bookingId],
    );
  } finally {
    await client.end();
  }
}

export async function getBookingStatus(bookingId: string): Promise<string> {
  const client = new Client({ connectionString: BOOKING_DB_URL });
  await client.connect();
  try {
    const result = await client.query("SELECT status FROM booking WHERE id = $1", [bookingId]);
    return result.rows[0]?.status;
  } finally {
    await client.end();
  }
}
