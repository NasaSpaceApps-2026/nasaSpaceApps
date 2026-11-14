
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
