# Usage

One-line commands from the repo root. Install once: `npm install` in `web/` and `web/src/rover/sandbox`; for the API, `source activatevenv311` then `pip install -r api/requirements.txt` (`python3.11` on PATH).

## Activate Python 3.11 venv

```sh
source activatevenv311
```

## Jezero Cesium viewer (port 5173)

```sh
cd web && VITE_SITE_DIR=../contracts/fixtures npm run dev
```

## Rover sandbox (port 5174)

Default site (playground if present, else the 1 km fixture). Top-right links also switch worlds.

| Link / `?world=` | What you get |
|---|---|
| *(omit / default)* | Playground or 1 km fixture |
| `projection` | 80 m Jezero sol 9 Navcam drape on frozen heights |
| `vision` (alias `llm`) | 80 m sol 9 vision-loop refine |
| `crater` | 1.8 km Jezero fan front (HiRISE + vision-loop crest bumps) |
| `olympus` | ~2.2 km Olympus Mons scarp (HiRISE, Mars orange, vision-loop layer bumps) |

```sh
cd web/src/rover/sandbox && npm run dev
```

Photo projection:

```sh
cd web/src/rover/sandbox && npm run dev -- --open '/?world=projection'
```

Vision loop:

```sh
cd web/src/rover/sandbox && npm run dev -- --open '/?world=vision'
```

Crater / Jezero fan front:

```sh
cd web/src/rover/sandbox && npm run dev -- --open '/?world=crater'
```

Olympus Mons scarp (explore this one):

```sh
cd web/src/rover/sandbox && npm run dev -- --open '/?world=olympus'
```

Controls: W/S drive, A/D steer, Shift/Space boost, R reset, wheel zoom.

## API (port 8000)

```sh
api/.venv/bin/uvicorn ares_api.main:app --app-dir api --port 8000
```

Playground site:

```sh
ARES_DATA_DIR=api/fixtures/playground api/.venv/bin/uvicorn ares_api.main:app --app-dir api --port 8000
```
