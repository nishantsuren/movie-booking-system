import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Served under /admin/ on the local CDN mock (§3.1), not at the
  // domain root like customer-web -- without this, built asset URLs in
  // index.html would be absolute from "/" and 404 under the /admin
  // mount.
  base: '/admin/',
  build: {
    // Same fix as customer-web (design v16): Vite's default build
    // output dir ("assets") collides with local-cdn-mock's existing
    // GET /assets/{asset_id} route.
    assetsDir: 'app-assets',
  },
})
