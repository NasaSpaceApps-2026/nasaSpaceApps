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

```sh
cd web/src/rover/sandbox && npm run dev
```

Photo projection (`?world=projection` — 80 m, Navcam drape on frozen heights):

```sh
cd web/src/rover/sandbox && npm run dev -- --open '/?world=projection'
```

Vision loop (`?world=vision` — 80 m sol 9 vision refine; `?world=llm` is the same):

```sh
cd web/src/rover/sandbox && npm run dev -- --open '/?world=vision'
```

Crater / Jezero fan front (`?world=crater` — 1.8 km, HiRISE + vision-loop crest bumps):

```sh
cd web/src/rover/sandbox && npm run dev -- --open '/?world=crater'
```

Olympus Mons scarp (`?world=olympus` — ~2.2 km layered scarp, Mars orange + vision-loop bumps):

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
