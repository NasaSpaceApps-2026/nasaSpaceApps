# sitebuild

Builds `data/build/site/jezero/` (elevation, slope_2m, rough_5x5, quality_flag, catalogue, profiles, POIs), the site directory read by `atlas_core.load_site` for the MCP query engine. Also writes `docs/site_overview.png`, `docs/seam_check.png`, `docs/noise_check.png`.

Run from the repo root:

    .venv/bin/python -m sitebuild.build_site      # or: .venv/bin/python sitebuild/build_site.py

The shared site frame (LAT0, LON0, SIZE, CRS) comes from `pipeline/site_cfg.py`, owned by the tile pipeline; it is imported read-only by path and never redefined here (`site_frame.verify_frame()` cross-checks it). Raw HiRISE/CTX windows are cached under `data/raw/`; a new window reuses overlapping cached pixels and fetches only the remainder.

## Interim science layers (`science.py`)

`build_site.py` calls `science.run()` after the terrain layers. `.venv/bin/python sitebuild/science.py` reruns it alone (it reads the existing `site.yaml`, removes its own entries and rewrites them, so it is idempotent). It adds, all INTERIM for the query engine and to be replaced by the data pipeline's official layers (Caden's `pipeline/`, `contracts/fixtures/` and `data/SOURCES.md` are not touched):

- `traverse.geojson`: Perseverance drive segments (NASA MMGIS) as one LineString with site-frame `x`/`y` in its properties, plus end-of-drive waypoints with `sol` (read by `devapi/validation.py`).
- 23 `sample_site` POIs: NASA sample list, positions DERIVED from the MMGIS waypoint at or before the sol sealed (descriptions say so and give an uncertainty), plus `named_region` POIs at sample centroids where defensible.
- `geologic_unit` (categorical): USGS SIM 3464 `GeoUnits_v1`, polygons rasterised at cell centres. About 100 m registration offset to our frame, measured by `measure_geo_offset.py` (not corrected).
- Seven evidence layers from the Parente et al. CRISM map (`carbonate`, `olivine`, `fe_mg_smectite`, `high_ca_pyroxene`, `low_ca_pyroxene`, `kaolinite`, `mg_smectite_2`): 36 m native, nearest to 2 m. Black or undecodable pixels are `no_coverage` (-1), never `not_detected`. `confirmed_in_situ` is set only within the position uncertainty of samples where a read source states the mineral was measured.
- Writes `docs/science_layers_check.png`, `SOURCES_site.md` entries and `site.yaml` catalogue entries.

Raw downloads are cached under `data/raw/science/` (the 349 MB SIM zip is fetched with curl resume; only `Nili.gdb` is extracted). Extra packages installed into `.venv`: `pyogrio`, `shapely`. Findings and caveats: `docs/data-inventory-minerals.md` section 8.

Files: `science.py` (interim science step), `measure_geo_offset.py` (registration offset of the SIM mosaic), `build_site.py` (main), `site_frame.py` (frame/transform), `fetch_hirise.py` (windowed cached reads), `seams.py` (seam detector), `noise.py` (noisy-DTM detector), `compare_ctx_hirise.py` (CTX vs HiRISE comparison).
