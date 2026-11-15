import { spawn, type ChildProcess } from 'node:child_process'
import { createReadStream, existsSync, statSync } from 'node:fs'
import { createConnection } from 'node:net'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { defineConfig, type Plugin, type ViteDevServer } from 'vite'
import react from '@vitejs/plugin-react'
import cesiumPlugin from 'vite-plugin-cesium'
import type { Connect, PreviewServer } from 'vite'

const here = path.dirname(fileURLToPath(import.meta.url))
const repo = path.resolve(here, '..')
// Ports: 5173 (viewer) and 5174 (street-view sandbox) by default. stack/up.sh sets VIEWER_PORT / SANDBOX_PORT when another
// project holds them (see web/INTEGRATION.md).
const VIEWER_PORT = Number(process.env.VIEWER_PORT ?? 5173)
const SANDBOX_PORT = Number(process.env.SANDBOX_PORT ?? 5174)

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

function portOpen(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = createConnection({ host: '127.0.0.1', port }, () => {
      socket.end()
      resolve(true)
    })
    socket.on('error', () => resolve(false))
  })
}

async function waitForPort(port: number, ms: number): Promise<boolean> {
  const start = Date.now()
  while (Date.now() - start < ms) {
    if (await portOpen(port)) return true
    await new Promise((r) => setTimeout(r, 200))
  }
  return false
}

/** Street view is the sandbox on 5174. Start it with the planet server so one command is enough. */
function sandboxDev(): Plugin {
  let child: ChildProcess | undefined
  return {
    name: 'sandbox-dev',
    async configureServer(server) {
      if (await portOpen(SANDBOX_PORT)) return
      const sandboxDir = path.resolve(here, 'src/rover/sandbox')
      const viteBin = path.join(sandboxDir, 'node_modules/vite/bin/vite.js')
      child = spawn(process.execPath, [viteBin, '--host', '127.0.0.1', '--port', String(SANDBOX_PORT), '--strictPort'], {
        cwd: sandboxDir,
        stdio: 'inherit',
        env: { ...process.env, BROWSER: 'none' },
      })
      const stop = () => {
        if (child && !child.killed) child.kill()
      }
      server.httpServer?.once('close', stop)
      if (!(await waitForPort(SANDBOX_PORT, 20000))) {
        server.config.logger.warn(`Rover sandbox did not start on port ${SANDBOX_PORT}`)
      }
    },
  }
}

// Serve the built site (tiles, manifest, data) from the repo's data/build at the web root,
// matching the contract paths (/tiles, /data). Use VITE_SITE_DIR to point at the fixtures
// instead: VITE_SITE_DIR=../contracts/fixtures npm run dev
// envDir '..' reads VITE_CESIUM_ION_TOKEN from the repo-root .env.local (gitignored).
export default defineConfig(() => {
  return {
    plugins: [react(), cesium(), roverWorlds(), sandboxDev()],
    envDir: '..',
    publicDir: process.env.VITE_SITE_DIR ?? '../data/build',
    server: {
      port: VIEWER_PORT,
      fs: { allow: [repo] },
      proxy: {
        '/sandbox': { target: `http://127.0.0.1:${SANDBOX_PORT}`, changeOrigin: true, ws: true },
      },
    },
  }
})