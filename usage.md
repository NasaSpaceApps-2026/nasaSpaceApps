# Usage

One-line commands from the repo root. Install once: `npm install` in `web/` and `web/src/rover/sandbox`; for the API, `source activatevenv311` then `pip install -r api/requirements.txt` (`python3.11` on PATH).

Optional Cesium ion token for the textured Mars tileset: put `VITE_CESIUM_ION_TOKEN=…` in a repo-root `.env.local` (gitignored). Without it the globe is a rust sphere; the Jezero / Olympus bubbles and labels still show.

## Planet + street view (two terminals)

Street view iframes the rover sandbox at `http://localhost:5174`, so both must be running.

**Important:** bare `http://localhost:5173/` boots **site** mode (close-up Jezero fixture). That is not the Mars globe. Planet mode needs `?view=planet` (or click the **planet** button after load).

**Terminal 1 — Mars globe (port 5173), opens in planet mode:**

```sh
cd web && VITE_SITE_DIR=../contracts/fixtures npm run dev -- --open '/?view=planet'
```

**Terminal 2 — rover sandbox for street view (port 5174):**

```sh
cd web/src/rover/sandbox && npm run dev
```

Then:

1. Confirm the **planet** control is the mode you want (URL should keep `?view=planet`).
2. You should see the whole globe plus **Jezero fan** and **Olympus Mons scarp** labels. Zoom in on a label until the orthophoto patch appears.
3. Drag the yellow pegman (lower right) onto that bubble. Drops outside a bubble are refused.
4. **Map** leaves street view.

| Bubble label | Street-view `?world=` | Size |
|---|---|---|
| Jezero fan | `crater` | 1.8 km |
| Olympus Mons scarp | `olympus` | ~2.2 km |

Drive in street view: W/S, steer A/D, boost Shift/Space, reset R.

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

Close-up site / orbit / ground / rover cameras (not the planet pegman flow):

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
