# Usage

One-line commands from the repo root. Install once: `npm install` in `web/` and `web/src/rover/sandbox`; for the API, `source activatevenv311` then `pip install -r api/requirements.txt` (`python3.11` on PATH).

Optional Cesium ion token for the full Mars tileset: put `VITE_CESIUM_ION_TOKEN=…` in a repo-root `.env.local` (gitignored). Without it the globe still shows as a rust sphere and the Jezero / Olympus bubbles still drape.

## Planet + street view (two terminals)

Street view iframes the rover sandbox at `http://localhost:5174`, so both servers must be running.

**Terminal 1 — Mars globe (port 5173):**

```sh
cd web && VITE_SITE_DIR=../contracts/fixtures npm run dev
```

Open `http://localhost:5173/?view=planet`. Click **planet**, then drag the pegman onto the Jezero fan or Olympus Mons scarp bubble. Drops outside a bubble are refused. **Map** leaves street view.

**Terminal 2 — rover sandbox (port 5174):**

```sh
cd web/src/rover/sandbox && npm run dev
```

A pegman drop loads `/?world=crater|olympus&x=&y=&heading=0` in an iframe. Drive with W/S, steer A/D, boost Shift/Space, reset R.

| Bubble on the globe | Street-view `?world=` | Size |
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
| `crater` | 1.8 km Jezero fan front (HiRISE + vision-loop crest bumps) |
| `olympus` | ~2.2 km Olympus Mons scarp (HiRISE + vision-loop layer bumps) |

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

Site / orbit / ground / rover cameras without the planet pegman flow (still useful with fixtures or `data/build`):

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
