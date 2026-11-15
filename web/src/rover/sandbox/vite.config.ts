import { createReadStream, existsSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { defineConfig, type Plugin, type ViteDevServer } from 'vite';
import type { Connect, PreviewServer } from 'vite';

const here = path.dirname(fileURLToPath(import.meta.url));
const roverDir = path.resolve(here, '..');
const repo = path.resolve(here, '../../../..');
// Site folder (manifest.json, tiles/, data/) served at the site root.
// ARES_SITE_DIR picks it (relative paths are from the repo root); default is the
// synthetic playground when generated (api/scripts/make_playground.py), else the
// contract fixtures.
const playground = path.join(repo, 'api/fixtures/playground');
const siteDir = process.env.ARES_SITE_DIR
  ? path.resolve(repo, process.env.ARES_SITE_DIR)
  : existsSync(path.join(playground, 'manifest.json'))
    ? playground
    : path.join(repo, 'contracts/fixtures');

const WORLD_TYPES: Record<string, string> = {
  '.json': 'application/json',
  '.png': 'image/png',
  '.webp': 'image/webp',
  '.geojson': 'application/geo+json',
};

/** Optional refined worlds. Absent `?world=` still serves siteDir at the site root. */
function roverWorlds(): Plugin {
  const roots: Record<string, string> = {
    projection: path.join(repo, 'contracts/fixtures/rover_worlds/projection'),
    vision: path.join(repo, 'contracts/fixtures/rover_worlds/vision'),
  };
  const serve: Connect.NextHandleFunction = (req, res, next) => {
    const raw = decodeURIComponent((req.url ?? '').split('?')[0]);
    const match = raw.match(/^\/worlds\/(projection|vision)(\/.*)?$/);
    if (!match) return next();
    const root = roots[match[1]];
    const rel = match[2] && match[2] !== '/' ? match[2] : '';
    const file = path.resolve(root, '.' + rel);
    const fromRoot = path.relative(root, file);
    if (fromRoot.startsWith('..') || path.isAbsolute(fromRoot) || !existsSync(file) || !statSync(file).isFile()) {
      res.statusCode = 404;
      res.end('not found');
      return;
    }
    res.setHeader('Content-Type', WORLD_TYPES[path.extname(file)] ?? 'application/octet-stream');
    res.setHeader('Cache-Control', 'no-cache');
    createReadStream(file).pipe(res);
  };
  const attach = (server: ViteDevServer | PreviewServer) => {
    server.middlewares.use(serve);
  };
  return { name: 'rover-worlds', configureServer: attach, configurePreviewServer: attach };
}

export default defineConfig({
  root: here,
  publicDir: siteDir,
  plugins: [roverWorlds()],
  resolve: {
    // Rover modules live outside this package; resolve their `three` import here.
    dedupe: ['three'],
    alias: { three: path.resolve(here, 'node_modules/three') },
  },
  server: {
    port: 5174,
    fs: { allow: [roverDir, siteDir] },
  },
  build: {
    outDir: path.resolve(here, 'dist'),
    emptyOutDir: true,
  },
});
