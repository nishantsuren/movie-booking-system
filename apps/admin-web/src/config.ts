// Design §3.2: the routing-service base URL must come from environment
// config, not a hardcoded localhost port -- same convention as
// customer-web (Phase 8).
const apiBaseUrl = import.meta.env.VITE_API_BASE_URL;

if (!apiBaseUrl) {
  throw new Error("VITE_API_BASE_URL is not set -- copy .env.example to .env or set it at build time");
}

export const API_BASE_URL: string = apiBaseUrl;
