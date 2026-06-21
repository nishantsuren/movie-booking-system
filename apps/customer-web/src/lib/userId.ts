// AUTH_ENABLED is false everywhere (Phase 10's job, design §3.2) -- there
// is no login flow yet, and POST /bookings already takes user_id
// directly in the request body (Appendix A), not derived from a token.
// A persisted client-side UUID is a faithful stand-in for "a logged-in
// user" until Phase 10 wires up the real thing.
const STORAGE_KEY = "movieticket.userId";

export function getUserId(): string {
  let id = localStorage.getItem(STORAGE_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(STORAGE_KEY, id);
  }
  return id;
}
