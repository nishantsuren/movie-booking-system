// Design §3.2: the routing-service base URL must come from environment
// config, not a hardcoded localhost port, so swapping to a production
// API gateway is a deploy-time env change, never a source edit. Vite
// resolves VITE_* vars at build time from .env files -- the frontend's
// equivalent of how every backend service reads its peer URLs from
// process env at startup (e.g. theatre's BOOKING_SERVICE_URL).
const apiBaseUrl = import.meta.env.VITE_API_BASE_URL;

if (!apiBaseUrl) {
  throw new Error("VITE_API_BASE_URL is not set -- copy .env.example to .env or set it at build time");
}

export const API_BASE_URL: string = apiBaseUrl;
