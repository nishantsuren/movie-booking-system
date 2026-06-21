// Direct DB access for time manipulation only -- same technique
// tests/integration/test_phase2.py and customer-web's e2e/db.ts use
// (backdate a timestamp directly in Postgres) rather than a test-only
// clock-mocking hook in production code. The draft lock lives in
// theatre_db, not booking_db.
import { Client } from "pg";

const THEATRE_DB_URL =
  process.env.THEATRE_DATABASE_URL ?? "postgresql://movieticket:movieticket_dev_password@localhost:5433/theatre_db";

export async function backdateLockHeartbeat(layoutId: string, minutesAgo: number): Promise<void> {
  const client = new Client({ connectionString: THEATRE_DB_URL });
  await client.connect();
  try {
    await client.query("UPDATE seat_layout SET lock_heartbeat_at = now() - interval '1 minute' * $1 WHERE id = $2", [
      minutesAgo,
      layoutId,
    ]);
  } finally {
    await client.end();
  }
}
