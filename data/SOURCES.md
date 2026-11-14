# Data sources

All access dates 2026-11-14. Raw data lives in `data/raw/` (gitignored) and is never modified.
Licence note for all USGS Astrogeology products below: public domain / CC0 as stated on the USGS product pages; credit USGS Astrogeology Science Center. Verify the exact licence text on each product page before redistribution.

| Input | URL | Size | Citation | Used for |
|---|---|---|---|---|
| Mars 2020 TRN HiRISE DTM mosaic, 1 m, `JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif` (float32, eqc lat_ts=0, R=3396190) | https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/HiRISE/JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif ; product page https://astrogeology.usgs.gov/search/map/mars_2020_terrain_relative_navigation_hirise_dtm_mosaic | file 1.84 GB; only a ~8700 x 8250 px window (about 285 MB) is read remotely and cached in `data/raw/hirise/` | Fergason et al. 2020, USGS Astrogeology, doi:10.5066/P9REJ9JN | elevation, slope_2m, rough_5x5, quality_flag (main terrain) |
| Mars 2020 TRN CTX DTM mosaic, 20 m, `JEZ_ctx_B_soc_008_DTM_MOLAtopography_DeltaGeoid_20m_Eqc_latTs0_lon0.tif` | https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/ (CTX folder); product page https://astrogeology.usgs.gov/search/map/mars_2020_terrain_relative_navigation_ctx_dtm_mosaic | 9.7 MB (`data/raw/ctx/`) | Fergason et al. 2020, USGS Astrogeology (CTX DTM DOI not yet verified; P9REJ9JN is the HiRISE DTM DOI) | fills cells without HiRISE data (flagged `ctx_fill`) |
| Mars 2020 TRN CTX orthomosaic, 6 m, `JEZ_ctx_B_soc_008_orthoMosaic_6m_Eqc_latTs0_lon0.tif` | same bucket as above | 36 MB (`data/raw/ctx/`) | Fergason et al. 2020 (DOI not yet verified) | downloaded for the CTX-vs-HiRISE comparison only; not used by build_site.py |
| Perseverance landing coordinates 18.4447 N, 77.4508 E (Octavia E. Butler Landing) | https://science.nasa.gov/mission/mars-2020-perseverance/ | n/a | NASA/JPL Mars 2020 mission; value as supplied in the task brief, not independently re-measured | landing_site POI (position is a published coordinate, not extracted from imagery) |

Notes
- HiRISE and CTX DTMs are both referenced to MOLA (delta-geoid corrected); heights are metres above the MOLA areoid.
- The mission base POI is an assumption (placed at the landing site) and is labelled so in `pois.geojson`.
- Profile thresholds in `profiles.yaml` are all marked `assumed`; none is a verified citation.

## Tile pipeline inputs (pipeline/00_fetch.py, build_tiles.py)

## JEZero HiRISE DTM 1 m (DOI 10.5066/P9REJ9JN)
- URL: https://planetarymaps.usgs.gov/mosaic/mars2020_trn/HiRISE/JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif
- Fetched: 2026-11-14 (windowed, 9058x8592 px around lat 18.452, lon 77.418)
- Window file: dtm_window.tif, 104 MB, valid fraction 1.000
- Citation: USGS Astrogeology Science Center / NASA MRO HiRISE; licence CC0 per USGS data release

## Jezero HiRISE orthomosaic 25 cm (DOI 10.5066/P9QJDP48)
- URL: https://planetarymaps.usgs.gov/mosaic/mars2020_trn/HiRISE/JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first.tif
- Fetched: 2026-11-14 (windowed, 9057x8592 px around lat 18.452, lon 77.418)
- Window file: ortho_window.tif, 54 MB, valid fraction 1.000
- Citation: USGS Astrogeology Science Center / NASA MRO HiRISE; licence CC0 per USGS data release

Note: ortho read at 1 m (25 cm source averaged 4x horizontally, every 4th row) via parallel HTTP range requests, because GDAL /vsicurl/ fails at row 39367 of this file.
