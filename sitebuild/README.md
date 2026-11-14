# sitebuild

Builds `data/build/site/jezero/` (elevation, slope_2m, rough_5x5, quality_flag, catalogue, profiles, POIs), the site directory read by `atlas_core.load_site` for the MCP query engine. Also writes `docs/site_overview.png`, `docs/seam_check.png`, `docs/noise_check.png`.

Run from the repo root:

    .venv/bin/python -m sitebuild.build_site      # or: .venv/bin/python sitebuild/build_site.py

The shared site frame (LAT0, LON0, SIZE, CRS) comes from `pipeline/site_cfg.py`, owned by the tile pipeline; it is imported read-only by path and never redefined here (`site_frame.verify_frame()` cross-checks it). Raw HiRISE/CTX windows are cached under `data/raw/`; a new window reuses overlapping cached pixels and fetches only the remainder.

Files: `build_site.py` (main), `site_frame.py` (frame/transform), `fetch_hirise.py` (windowed cached reads), `seams.py` (seam detector), `noise.py` (noisy-DTM detector), `compare_ctx_hirise.py` (CTX vs HiRISE comparison).
