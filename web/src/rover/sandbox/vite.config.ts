import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { defineConfig } from 'vite';

const here = path.dirname(fileURLToPath(import.meta.url));
const roverDir = path.resolve(here, '..');
// Contract fixtures (manifest.json, tiles/, data/) served at the site root.
const fixtures = path.resolve(here, '../../../../contracts/fixtures');

export default defineConfig({
  root: here,
  publicDir: fixtures,
  resolve: {
    // Rover modules live outside this package; resolve their `three` import here.
    dedupe: ['three'],
    alias: { three: path.resolve(here, 'node_modules/three') },
  },
  server: {
    port: 5174,
    fs: { allow: [roverDir, fixtures] },
  },
  build: {
    outDir: path.resolve(here, 'dist'),
    emptyOutDir: true,
  },
});
