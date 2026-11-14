import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { defineConfig } from 'vite';

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

export default defineConfig({
  root: here,
  publicDir: siteDir,
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
