// AUTH_ENABLED is false everywhere (Phase 10's job, design §3.2) -- no
// real admin accounts/JWTs exist yet. Lock-gated theatre endpoints
// identify the caller via the X-Admin-User-Id header in this mode
// (services/theatre/main.py's _get_admin_identity); a persisted
// client-side UUID is a faithful stand-in for "a logged-in admin" until
// Phase 10, same pattern as customer-web's userId.ts.
const STORAGE_KEY = "movieticket.adminId";

export function getAdminId(): string {
  let id = localStorage.getItem(STORAGE_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(STORAGE_KEY, id);
  }
  return id;
}
