<!-- Copy of data/build/site/jezero/SOURCES_site.md (that folder is git-ignored); regenerate with sitebuild and re-copy. -->
# Site build notes (written by sitebuild/build_site.py maintainers; data/SOURCES.md is untouched)

Change: the site frame now comes from pipeline/site_cfg.py (LAT0 18.452, LON0 77.418, SIZE 8192 m, CRS string), replacing the
earlier private centre 18.46 N / 77.41 E. site_frame.verify_frame() cross-checks site_cfg.CRS against an independent
statement of the contract-1 frame (max disagreement 0.3 mm, from the 3-decimal y_0 rounding in site_cfg).

Raw data: HiRISE window data/raw/hirise/window_18.452_77.418_4096.tif (same remote DTM, same source grid). 89.1% was copied from the
older cached window window_18.46_77.41_4096.tif (kept, not modified); the missing south and east strips (10.9%) were read
remotely (about 50 s). CTX window data/raw/ctx/window_18.452_77.418_4096.tif cut from the local CTX file.

New quality class 3 "hirise_noisy" (sitebuild/noise.py): contiguous zones of high cell-to-cell elevation energy (median |Laplacian| per 16 m
block, robust z against the site-wide distribution, smoothed, hysteresis, >= 4 ha). Flag only; elevations unchanged. Precedence:
ctx_fill > hirise_noisy > hirise_seam_suspect. Check image: docs/noise_check.png.

## Science layers (INTERIM, built by sitebuild/science.py; retrieved 2026-11-14)

All entries below are interim layers for the query engine, to be replaced by the data pipeline's official layers.

| Item | Source | Licence / credit | Notes |
|---|---|---|---|
| traverse.geojson, waypoints | NASA/JPL-Caltech Mars 2020 MMGIS: https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_traverse.json ; https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_waypoints.json (ETags fafb42a0..., 6e29cc7b...; last modified 2026-10-28) | No licence text on the endpoints; NASA content generally not copyrighted, credit NASA/JPL-Caltech | Lat is planetocentric, lon east. Clipped to the site + 100 m. First waypoint (sol 13, RMC 3_0, 'Site increment, no motion') equals the PDS PLACES SITE 1 position. |
| sample_site POIs (23) | NASA Mars Rock Samples list https://science.nasa.gov/mission/mars-2020-perseverance/mars-rock-samples/; positions derived from the MMGIS waypoint at or before the sol sealed | NASA/JPL-Caltech | Positions are APPROXIMATE and derived, not official. Samples 24, 25, 26, 27, 28 fall outside the site or have no derived position inside it. |
| named_region POIs (4) | Centroids of derived sample positions per NASA 'Sampling Location' | derived | Mark where the rover sampled, not the region's extent. No gazetteer coordinates used. |
| geologic_unit | USGS SIM 3464 database https://pubs.usgs.gov/sim/3464/sim3464_database.zip (GeoUnits_v1) | USGS; public-domain status not verified | 100-200 m registration offset to CTX per the readme. Measured: cross-correlating the SIM 3464 base CTX mosaic (Caltech Feb 2018) with a hillshade of our elevation (best sun azimuth 255 deg, best whole-site correlation peak 0.60) gives a whole-site shift of the mosaic of +26 m east, -96 m north (correlation peak 0.60); per-tile (2 km) shifts of 16 tiles have median magnitude 102 m (range 88.4-111.6 m). Spread of the tile shift vectors about their median: max 25 m, rms 10 m. Positive east/north = features in the mosaic (and so the unit polygons) lie that far east/north of the same features in our elevation frame. This is an image-to-hillshade correlation (illumination and albedo differ), accurate to roughly one 8 m pixel at best. Polygon shift applied (east, north m): (-25.8, 98.1). |
| carbonate, olivine, fe_mg_smectite, high_ca_pyroxene, low_ca_pyroxene, kaolinite, mg_smectite_2 | Parente et al. 2021, Zenodo https://zenodo.org/records/5575824 (doi:10.5281/zenodo.5575824), jezeroFinal.tif + Jezero_readme.pdf | CC BY 4.0 | 36 m native, nearest to 2 m. Black = no_coverage. Tier mapping per docs/data-inventory-minerals.md section 1e with our decode rules (see layer 'quality'). |
| confirmed_in_situ marks | Farley et al. 2022 (Seitah olivine, Mg-Fe carbonate; Europe PMC abstract); Williams et al. 2023 LPSC (olivine, pyroxene at Cape Nukshak; Amalik serpentine); Hurowitz et al. 2023 Goldschmidt (olivine-dominated source, Amalik) | papers / abstracts | Only at sample POIs [4, 5, 6, 7, 14, 15]; unit/section-level statements applied at derived positions. Delta-front Fe-bearing carbonate (Hurowitz) is NOT marked: it is stated per section, not per sample. |

Cached raw files: data/raw/science/ (mmgis, nasa, parente, usgs_sim3464, papers). Install note: pyogrio and shapely were pip-installed into .venv to read the FileGDB.
