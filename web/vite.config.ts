import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import cesiumPlugin from 'vite-plugin-cesium'

// vite-plugin-cesium is CommonJS; unwrap its default export under ESM.
const cesium = ((cesiumPlugin as unknown as { default?: () => unknown }).default ?? cesiumPlugin) as unknown as () => never

// Serve the built site (tiles, manifest, data) from the repo's data/build at the web root,
// matching the contract paths (/tiles, /data). Use VITE_SITE_DIR to point at the fixtures
// instead: VITE_SITE_DIR=../contracts/fixtures npm run dev
// envDir '..' reads VITE_CESIUM_ION_TOKEN from the repo-root .env.local (gitignored).
export default defineConfig(() => {
  return {
    plugins: [react(), cesium()],
    envDir: '..',
    publicDir: process.env.VITE_SITE_DIR ?? '../data/build',
    server: { port: 5173 },
  }
})
