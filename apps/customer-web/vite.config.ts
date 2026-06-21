import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    // Vite's default build output dir is "assets" -- which collides with
    // local-cdn-mock's existing /assets/{asset_id} API route (Appendix A:
    // uploaded-image blobs). FastAPI matches that route before the SPA's
    // static mount and tries to parse the bundle's filenames as a UUID,
    // 422ing on every JS/CSS request. Renamed here rather than touching
    // the already-documented /assets contract.
    assetsDir: 'app-assets',
  },
})
