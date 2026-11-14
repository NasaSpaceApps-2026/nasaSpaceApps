
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

## Viking MDIM 2.1 colourised mosaic (colour source only)
- Layer: Mars_Viking_MDIM21_ClrMosaic_global_232m via NASA Mars Trek WMTS, level 7 (about 330 m/px)
- Fetched: 2026-11-14, 3x3 tiles around lat 18.452, lon 77.418
- Use: low-frequency colour for the HiRISE ortho. The result is FALSE COLOUR (HiRISE is greyscale).
- Credit: NASA/JPL/USGS

## Jezero HiRISE orthomosaic 25 cm, core block at full resolution (DOI 10.5066/P9QJDP48)
- URL: https://planetarymaps.usgs.gov/mosaic/mars2020_trn/HiRISE/JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first.tif
- Fetched: 2026-11-14 (windowed, 9311x8832 px around lat 18.452, lon 77.418)
- Window file: ortho_core_window.tif, 41 MB, valid fraction 1.000
- Citation: USGS Astrogeology Science Center / NASA MRO HiRISE; licence CC0 per USGS data release

## Perseverance traverse and waypoints (NASA/JPL, Mars 2020)
- URLs: https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_traverse.json and M20_waypoints.json (fetched 2026-11-14 with curl into data/raw/m20_*.json)
- Official viewer: https://science.nasa.gov/mission/mars-2020-perseverance/location-map/ . The JSON paths are NASA's own map-layer files, not a documented download API, so cache a copy.
- Used by pipeline/05_vectors.py. 353 waypoints (sols 13-1019) and 279 traverse segments fall in the site. Validation results live in docs/science-validation.md (regenerate with pipeline/09_validate.py). Elevation check is a frame/alignment check, NOT an independent accuracy test (rover elevations are tied to this HiRISE DTM).
- Waypoint records also carry real Mastcam-Z/Navcam panorama links under mars.nasa.gov/mmgis-maps/M20/ ; those image URLs returned HTTP 403 to direct requests on 2026-11-14 and are not used.

## IAU/USGS place names (Gazetteer of Planetary Nomenclature)
- URL: https://asc-planetarynames-data.s3.us-west-2.amazonaws.com/MARS_nomenclature_center_pts.zip (public domain, fetched by pipeline/05b_names.py)
- 14 official names near the site. The bulk file does NOT contain rover-era names such as Octavia E. Butler Landing, Three Forks or Cheyava Falls.

## 25 cm HiRISE core block
- Same HiRISE orthomosaic as above, read at full 25 cm for a 2176 m block (level-6 tiles, 17x17) around the early traverse, via pipeline/00_fetch.py ortho_core.

## Colour: what is real and what is assumed
- Brightness and all fine detail: real HiRISE. Relative colour variation: Viking MDIM 2.1 (NASA/JPL/USGS), one redness axis. Overall hue: ASSUMED typical Mars tone (pipeline/colorize.py ANCHOR). A true-colour source was not found: the verified HiRISE colour product (PSP_004299_1985_COLOR, false colour) does not overlap the site, and the Navcam colour panoramas are not fetchable.

## CTX context block (MRO CTX, NASA/JPL/MSSS/USGS)
- DTM 20 m: https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/CTX/JEZ_ctx_B_soc_008_DTM_MOLAtopography_DeltaGeoid_20m_Eqc_latTs0_lon0.tif (9.7 MB)
- Ortho 5 m (JPL Mars 2020 science mosaic): https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/CTX/ScienceInvestigationMaps_JPL/M20_JezeroCrater_CTXortho_mosaic_5m.tif (360 MB; read as a 10 m window)
- Used by pipeline/10_context.py for a 28.7 km block centred on the site. Ortho brightness linearly matched to HiRISE; terrain/brightness comparison in docs/science-validation.md.
