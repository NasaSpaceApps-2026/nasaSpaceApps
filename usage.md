# Usage

This page is the viewer-only path: planet view, street view and the rover sandbox, no backend and no AI keys needed. For the full copilot (chat, MCP, agent, APIs) use `stack/up.sh` (see the root `README.md` and `stack/README.md`). The planet viewer and sandbox worlds use different site frames from the copilot's real 8 km site (`docs/frames.md`).

One-line commands from the repo root. Install once: `npm install` in `web/` and `web/src/rover/sandbox`; for the API, `source activatevenv311` then `pip install -r api/requirements.txt` (`python3.11` on PATH).

Optional Cesium ion token for the higher-resolution Mars tileset: put `VITE_CESIUM_ION_TOKEN=…` in a repo-root `.env.local` (gitignored). Without it the globe is the NASA/USGS Viking color mosaic from Mars Trek. The Jezero and Olympus patches are sharper than that mosaic.

## Planet and street view (one window)

One command. Stop any dev server already running on port 5173 or 5174 first. An old process keeps the previous page, and the new one never takes the port. Do not start `web/src/rover/sandbox` yourself, and do not open `localhost:5174`. The planet server starts that sandbox, and street view is the page inside the globe. Bare `http://localhost:5173/` is the close-up Jezero site, not the planet. Use `?view=planet`.

```sh
cd web && VITE_SITE_DIR=../contracts/fixtures npm run dev -- --open '/?view=planet'
```

In that window:

1. The globe is Mars, with **Jezero fan** and **Olympus Mons scarp** labels.
2. Press **Drive** next to a name. That enters the rover on that patch in the same window. Or drag the yellow pegman onto the patch. A drop outside a patch stays on the planet.
3. Drive with the arrow keys or W/S. Steer with the left and right arrows or A/D. Boost is Shift, brake is Space, reset is R.
4. **Map** returns to the globe.

| Place | Street-view world | Size |
|---|---|---|
| Jezero fan | `crater` | 1.8 km |
| Olympus Mons scarp | `olympus` | ~2.2 km |

## Rover sandbox alone (port 5174)

Same sandbox without the globe. Top-right links also switch worlds.

| Link / `?world=` | What you get |
|---|---|
| *(omit / default)* | Playground or 1 km fixture |
| `projection` | 80 m Jezero sol 9 Navcam drape on frozen heights |
| `vision` (alias `llm`) | 80 m sol 9 vision-loop refine |
| `crater` | 1.8 km Jezero fan front |
| `olympus` | ~2.2 km Olympus Mons scarp |

```sh
cd web/src/rover/sandbox && npm run dev
```

```sh
cd web/src/rover/sandbox && npm run dev -- --open '/?world=projection'
```

```sh
cd web/src/rover/sandbox && npm run dev -- --open '/?world=vision'
```

```sh
cd web/src/rover/sandbox && npm run dev -- --open '/?world=crater'
```

```sh
cd web/src/rover/sandbox && npm run dev -- --open '/?world=olympus'
```

## Jezero Cesium site viewer (port 5173)

Close-up Jezero view (the viewer now opens on Mars; use the Jezero tab, key 2, or this URL):

```sh
cd web && VITE_SITE_DIR=../contracts/fixtures npm run dev
```

## API (port 8000, optional third terminal)

Sandbox and street view drive offline; the API adds `/site` profiles and the WebSocket link.

```sh
api/.venv/bin/uvicorn ares_api.main:app --app-dir api --port 8000
```

Playground site:

```sh
ARES_DATA_DIR=api/fixtures/playground api/.venv/bin/uvicorn ares_api.main:app --app-dir api --port 8000
```

## Activate Python 3.11 venv

Needed to regenerate fixtures / API deps, not to drive:

```sh
source activatevenv311
```
