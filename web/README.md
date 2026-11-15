# Ares Atlas viewer (`web/`)

The 3D viewer for Ares Atlas: a CesiumJS Mars globe that zooms to a Jezero site view, with orbit, ground and rover cameras, a street view you drop into from the planet (the rover sandbox in `web/src/rover/sandbox`, shown as a page inside the globe), and the AI copilot's chat dock on the right. Stack: React, TypeScript, Vite, CesiumJS, three.js.

## Run it

- **Whole copilot (recommended):** from the repo root, `stack/up.sh --with-viewer`, then open `http://localhost:5173/?view=planet`. See the root [README.md](../README.md) and [stack/README.md](../stack/README.md). The chat dock needs the agent on `:8766`, which `stack/up.sh` starts; it needs an LLM key in `.env` (copy `.env.example`).
- **Viewer only (no backend, no keys):** commands for the planet view, street view and each sandbox world are in [usage.md](../usage.md). The short version:

```sh
cd web && npm install
VITE_SITE_DIR=../contracts/fixtures npm run dev -- --open '/?view=planet'
```

Optional: put `VITE_CESIUM_ION_TOKEN=...` in a repo-root `.env.local` for the higher-resolution Mars tileset. Without it the globe uses the NASA/USGS Viking colour mosaic from Mars Trek.

Other scripts (`web/package.json`): `npm run build` (typecheck and production build), `npm run lint`, `npm run shots` (screenshots).

## Site data and frames

`VITE_SITE_DIR=../contracts/fixtures` serves the fixture site, which is synthetic ("not Mars data"). Without it the viewer serves `data/build` (the real build, not in git). The copilot's query engine uses the real 8 km Jezero site, a different frame from the fixture and from the sandbox worlds; they line up only with `stack/up.sh --frame crater --with-viewer` and `?world=crater` (details in [docs/frames.md](../docs/frames.md)).

## Chat dock

`src/panels/chat/` holds the copilot chat panel (see its [README](src/panels/chat/README.md)) and `AtlasDock.tsx`, which mounts it in the viewer: tool results such as searches and routes are drawn on the globe, the camera follows the agent, and the viewer reports its camera, rover pose and selected feature to the agent. Integration notes: [INTEGRATION.md](INTEGRATION.md) (written by another builder; if the link is dead, the file has not landed yet and `AtlasDock.tsx`'s header comment is the reference). The standalone page for the same panel is `chat-dev/` ([README](../chat-dev/README.md), port 5199).

## Layout

| Path | What |
|---|---|
| `src/App.tsx` | Viewer app: planet, site, orbit, ground and rover modes |
| `src/rover/` | Browser rover driving and physics, sandbox (Yousef) |
| `src/panels/chat/` | Chat panel and dock (Sameer) |
| `src/bubbles.ts`, `src/site.ts`, `src/providers.ts` | Imagery patches, site manifest loading, terrain/imagery providers |
