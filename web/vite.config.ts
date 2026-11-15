import { createReadStream, existsSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { defineConfig, type Plugin, type ViteDevServer } from 'vite'
import react from '@vitejs/plugin-react'
import cesiumPlugin from 'vite-plugin-cesium'
import type { Connect, PreviewServer } from 'vite'

const here = path.dirname(fileURLToPath(import.meta.url))
const repo = path.resolve(here, '..')

// vite-plugin-cesium is CommonJS; unwrap its default export under ESM.
const cesium = ((cesiumPlugin as unknown as { default?: () => unknown }).default ?? cesiumPlugin) as unknown as () => never

const WORLD_TYPES: Record<string, string> = {
  '.json': 'application/json',
  '.png': 'image/png',
  '.webp': 'image/webp',
  '.geojson': 'application/geo+json',
}

/** Optional refined worlds under /worlds/{crater,olympus,...}. */
function roverWorlds(): Plugin {
  const roots: Record<string, string> = {
    projection: path.join(repo, 'contracts/fixtures/rover_worlds/projection'),
    vision: path.join(repo, 'contracts/fixtures/rover_worlds/vision'),
    crater: path.join(repo, 'contracts/fixtures/rover_worlds/crater'),
    olympus: path.join(repo, 'contracts/fixtures/rover_worlds/olympus'),
  }
  const serve: Connect.NextHandleFunction = (req, res, next) => {
    const raw = decodeURIComponent((req.url ?? '').split('?')[0])
    const match = raw.match(/^\/worlds\/(projection|vision|crater|olympus)(\/.*)?$/)
    if (!match) return next()
    const root = roots[match[1]]
    const rel = match[2] && match[2] !== '/' ? match[2] : ''
    const file = path.resolve(root, '.' + rel)
    const fromRoot = path.relative(root, file)
    if (fromRoot.startsWith('..') || path.isAbsolute(fromRoot) || !existsSync(file) || !statSync(file).isFile()) {
      res.statusCode = 404
      res.end('not found')
      return
    }
    res.setHeader('Content-Type', WORLD_TYPES[path.extname(file)] ?? 'application/octet-stream')
    res.setHeader('Cache-Control', 'no-cache')
    createReadStream(file).pipe(res)
  }
  const attach = (server: ViteDevServer | PreviewServer) => {
    server.middlewares.use(serve)
  }
  return { name: 'rover-worlds', configureServer: attach, configurePreviewServer: attach }
}

// Serve the built site (tiles, manifest, data) from the repo's data/build at the web root,
// matching the contract paths (/tiles, /data). Use VITE_SITE_DIR to point at the fixtures
// instead: VITE_SITE_DIR=../contracts/fixtures npm run dev
// envDir '..' reads VITE_CESIUM_ION_TOKEN from the repo-root .env.local (gitignored).
export default defineConfig(() => {
  return {
    plugins: [react(), cesium(), roverWorlds()],
    envDir: '..',
    publicDir: process.env.VITE_SITE_DIR ?? '../data/build',
    server: { port: 5173, fs: { allow: [repo] } },
  }
})