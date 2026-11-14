# Data inventory: mineral evidence, geology, and Perseverance traverse (Jezero)

Prepared 2026-11-14 for Caden (data pipeline). Scope: sources for evidence layers (contracts/mcp-tools.md section 3.3 tiers), geologic units, rover traverse, and sample sites, for the 8192 m site centred 18.452 N, 77.418 E.

Verification convention used throughout:
- **VERIFIED** = I opened the URL (HTTP 200 or page text read) and saw the stated fact in this session.
- **DERIVED** = computed by me from a verified file (method stated).
- **UNVERIFIED** = from a search-result summary, memory, or a page I could not open. Do not build on these without checking.

Site bounding box used for all coverage checks (equirectangular approximation on R = 3396190 m, half-width 4096 m): lon 77.3452 to 77.4908 E, lat 18.3829 to 18.5211 N. Check against the real eqc site CRS in `pipeline/site_cfg.py` before final cuts (edge cases only).

---

## 0. Recommended minimal set for the demo (build in this order)

| # | Layer | Source | Why first |
|---|---|---|---|
| 1 | Rover traverse and waypoints (sol-stamped) | NASA MMGIS JSON, section 3 | Trivial GeoJSON, verified, covers the whole site, no GIS tooling |
| 2 | Sample sites (23 inside the site) | NASA sample list (section 4) + positions derived from the waypoints file | Needed for "where has carbonate been confirmed" (eval question 9); positions are DERIVED, not official |
| 3 | Geologic units | USGS SIM 3464 database, section 2 | Verified, public USGS, supports tier 1 (`inferred_from_unit`) explicitly in its own text; needs FileGDB reading |
| 4 | One CRISM mineral map | Parente et al. Jezero mineral map (Zenodo 5575824), section 1 | 1.4 MB GeoTIFF, CC BY 4.0, verified to cover the site, has two confidence levels per mineral that map naturally to `strong_orbital` / `weak_orbital` |
| 5 (stretch) | CRISM MTRDR summary parameters (CINDEX2, D2300, BD2500_2, OLINDEX3, BD1300) | PDS Geosciences MROCR_4001, section 1 | Verified readable remotely and covers the site; thresholds are ours to choose (no verified published cut-off), so do after 1-4 |

Skip for the demo: Mars Trek WMTS (could not verify any CRISM layer), Zastrow and Glotch unmixing (6.7 GB models), Horgan Mendeley data (spectra and images, no vector detections).

---

## 1. CRISM mineral products

### 1a. Table

| Product | URL (opened) | Format | Resolution | Coverage of our site | Licence | Detection or parameter? |
|---|---|---|---|---|---|---|
| Parente et al. "Map of the dominant mineralogy over and around the Mars2020 landing ellipse" (`jezeroFinal.tif`, 1.39 MB; also `Jezero_readme.pdf`, `mineralSpectra_mapped_v2.sli`) | https://zenodo.org/records/5575824 ; file https://zenodo.org/api/records/5575824/files/jezeroFinal.tif/content | GeoTIFF, 3-band uint8 RGB (colour-coded classes), 614 x 751 px, equirectangular sphere R=3396190, std parallel 0 | 36 m/px (VERIFIED from transform) | VERIFIED full coverage: file bounds lon about 77.26 to 77.64 E, lat about 18.28 to 18.74 N (DERIVED from projected bounds x 4579898..4602002, y 1083544..1110580). About 25% of site pixels are black (0,0,0) = "not identified as any prototype" | CC BY 4.0 (Zenodo record metadata, VERIFIED) | Class detections (model output), not parameter values. Built from CRISM HRL000040FF, FRT000047A3, FRT0005C5E, FRS00031442; GAN-based classifier (Saranathan and Parente 2021) after atmospheric correction (Itoh and Parente 2021); coregistered to USGS controlled CTX and HiRISE DEM (record description, VERIFIED) |
| CRISM MTRDR (targeted, map-projected) scenes: FRT00005C5E, FRT000047A3, HRL000040FF, FRT00005850, FRT000066A4, FRT0001FB74 | ODE query (JSON) that listed them: `https://oderest.rsl.wustl.edu/live2/?query=product&results=m&target=mars&ihid=MRO&iid=CRISM&pt=MTRDR&westernlon=77.35&easternlon=77.49&minlat=18.38&maxlat=18.52&loc=f&output=JSON&limit=20` (returned 6 products); PDS volume https://pds-geosciences.wustl.edu/mro/mro-m-crism-5-rdr-mptargeted-v1/mrocr_4001/ ; example scene dir https://pds-geosciences.wustl.edu/mro/mro-m-crism-5-rdr-mptargeted-v1/mrocr_4001/mtrdr/2007/2007_139/frt00005c5e/ | ENVI (`.img` + `.hdr`), 32-bit float, BSQ, little-endian, 60 bands; PDS4 `.xml` labels. SU = summary parameters, SR = refined (noise-remediated) summary parameters, BR browse, IN processing info | FRT 18 m/px, HRL 36 m/px (ODE `Map_scale` field, VERIFIED) | FRT00005C5E footprint lon 77.279..77.559, lat 18.365..18.635 covers the whole site (VERIFIED from ODE bounds). Valid-data fraction inside the site for its SR cube: 164258 of 211584 px = 78% (DERIVED; strip-edge nodata). The other five scenes cover parts of the site (bounds in ODE record) | PDS archive; no licence or DOI stated on the CRISM portal page I opened (https://pds-geosciences.wustl.edu/missions/mro/crism.htm). Treat as public NASA data, credit NASA/JHUAPL/MRO CRISM | Parameter values (continuous band depths and indices), not detections |
| Horgan et al. 2020 data package "Data for: The mineral diversity of Jezero crater" | https://data.mendeley.com/datasets/b39x2kfbj4/1 | Excel spectra worksheet and CRISM RGB mosaics over CTX (description text only; file list not retrieved, UNVERIFIED) | n/a | n/a | CC BY-NC 3.0 (VERIFIED on page). Non-commercial: check against our use | RGB composites and spectra, no vector or class detections found |
| Zastrow and Glotch, "Distinct Lithologies in Jezero Crater, Mars" (GRL 2021) | https://zenodo.org/records/4673066 | `data_roi.zip` (60 KB, ROI shapefile), `data_input.zip` (1.0 GB), `data_models.zip` (6.8 GB, NetCDF unmixing outputs) | CRISM native | Not checked | CC BY 4.0 (VERIFIED) | Spectral unmixing abundances (record says carbonate abundances modelled up to about 35%). Too large for the hackathon; only the ROI shapefile is cheap |
| Brown, Viviano and Goudge, "Olivine-Carbonate Mineralogy of the Jezero Crater Region" | arXiv preprint https://arxiv.org/pdf/1801.09841 (opened, 13 pp) | Paper only; figures of BD1300 maps. No data release link found in the text I read | n/a | n/a | n/a | Uses BD1300 as olivine proxy; no numeric detection threshold found in the text |

### 1b. MTRDR band names (VERIFIED from `frt00005c5e_07_sr166j_mtr3.hdr`)

890 samples x 891 lines x 60 bands. Band order: R770, RBR, BD530_2, SH600_2, SH770, BD640_2, BD860_2, BD920_2, RPEAK1, BDI1000VIS, R440, IRR1, BDI1000IR, OLINDEX3, R1330, BD1300, LCPINDEX2, HCPINDEX2, VAR, ISLOPE1, BD1400, BD1435, BD1500_2, ICER1_2, BD1750_2, BD1900_2, BD1900R2, BDI2000, BD2100_2, BD2165, BD2190, MIN2200, BD2210_2, D2200, BD2230, BD2250, MIN2250, BD2265, BD2290, D2300, BD2355, SINDEX2, ICER2_2, MIN2295_2480, MIN2345_2537, BD2500_2, BD3000, BD3100, BD3200, BD3400_2, CINDEX2, BD2600, IRR2, IRR3, R530, R600, R1080, R1506, R2529, R3920.

Notes for Caden:
- There is **no plain "BD2300" band**; the carbonate and phyllosilicate-relevant bands are D2300, BD2290, BD2500_2, CINDEX2, MIN2295_2480, MIN2345_2537, BD1900_2. Olivine: OLINDEX3, BD1300. (The PRD and our task text say BD2300; the actual MTRDR name is D2300.)
- Map info (VERIFIED from header): "MRO Mars Equirectangular [IAU 2000] [15.00N; 0.00E]", sphere radius 3394839.813 m (not 3396190), 18 m pixel, upper-left tie point x=4422849.2, y=1104156.6. GDAL's ENVI driver failed to open the remote `.img` through /vsicurl/ in the repo venv (rasterio: "not recognized as a supported file format"). I read the band by HTTP Range request instead (offset = (band_index * lines + row0) * samples * 4 bytes). Downloading the local file and opening with rasterio may work; untested.
- Typical values inside our site for this scene (SR cube, DERIVED, 1st/50th/99th percentile): OLINDEX3 0.059/0.081/0.106; BD1300 0.006/0.014/0.028; D2300 -0.001/0.004/0.013; BD2500_2 -0.016/-0.010/0.001; BD1900_2 -0.013/-0.006/0.004; CINDEX2 0.081/0.115/0.149. CINDEX2 has a large positive background here, so it is not usable as a raw detection without a local baseline.
- File sizes: SU and SR cubes are 190 MB each for FRT00005C5E (82 MB for HRL000040FF). Over the 50 MB guide, so read windowed only.

### 1c. What "detection" means in the sources (thresholds)

- **Viviano-Beck et al. 2014** (JGR Planets 119, doi:10.1002/2014JE004627; opened via https://authors.library.caltech.edu/records/ng3z2-2yy40/files/jgre20270.pdf): defines the summary parameters and RGB "browse products" (CAR browse = D2300, BD2500H2, BD1900_2; CR2 = MIN2295_2480, MIN2345_2537, CINDEX2). The abstract says "meaningful threshold values for the summary products have also been defined", but the text I read gives **stretch limits** (lower limit 0; upper limit at the 99th percentile of the cumulative histogram of over 100 CRISM images or the 99.9th percentile of the local histogram) and no single numeric detection cut-off per parameter. A numeric table, if any, was not found. **UNVERIFIED: any numeric detection threshold.** Carbonate identification is described as paired about 2.3 and about 2.5 um absorptions (Mg-rich at 2.30 and 2.51 um, Fe/Ca at 2.33 and 2.53 um).
- **Parente map**: confidence is built in. Readme (VERIFIED, `Jezero_readme.pdf`): bold tone = "very high confidence" match to a prototype, subdued tone = "high / intermediate confidence". Black (0,0,0) = not identified as any prototype. Readme caution: "Exact mineral attributions can only be verified after further analysis" and class names are closest-match labels. Colour key (High / Medium RGB): Olivine 255,0,0 / 153,0,0; Mg-Carbonate 0,255,0 / 0,153,0; Fe/Mg-Smectite 0,255,255 / 0,153,153; Olivine+Smectite 255,128,114 / 153,77,68; Olv.+Smect+2.2 um 128,128,0 / 77,77,0; Carb.+2.2 um 255,215,0 / 153,129,0; High-Ca pyroxene 255,128,0 / 153,77,0; Low-Ca pyroxene 218,165,32 / 130,99,19; Kaolinite 139,69,19 / 83,41,11; Mg-Smectite 2 0,0,255 / 0,0,153.
- **Horgan et al. 2020, Brown et al. 2020**: I did not open the full texts; Horgan's thresholds, if any, are **UNVERIFIED**. A search summary says Horgan defined the "Marginal Carbonates" unit within the Mottled Terrain, which is outside our site by position of the rover tracks (UNVERIFIED location).

### 1d. Parente map pixel statistics inside the site (DERIVED)

Window 240 x 228 px (54720 px). Decoding by nearest key colour within 8 per channel matched 65% of pixels; the rest are blended colours (the TIFF appears to have been resampled with interpolation, so class edges are mixed colours and values like (153,76,0) are off-by-one versions of key colours). Of all pixels: black 24.9%; Olivine high 6.1%, medium 3.3%; Mg-Carbonate high 1.5%, medium 1.6%; Carb.+2.2 um 0.4%; Olivine+Smectite high 3.1%, medium 10.9%; High-Ca pyroxene high 1.5%, medium 11.0%; Fe/Mg-Smectite 0.4%. Mg-carbonate plus Carb.+2.2 um is about 1900 px, roughly 2.5 km2 at 36 m/px. Caden should decode with a tolerance (nearest key colour within about 8 to 10), and ignore blended pixels or snap them.

### 1e. Proposed tier mapping (orbital)

| Source | Tier | Justification (from source) |
|---|---|---|
| Parente map, bold (high-confidence) carbonate/olivine classes | `strong_orbital` | Readme: "very high confidence" match to prototype |
| Parente map, subdued (high/intermediate) classes | `weak_orbital` | Readme: "high / intermediate confidence", "some minor variation" |
| Parente map, black pixels | **not** `not_detected` by default | Black means "not identified as any known prototype", which includes noise, dust and areas with no CRISM data; the tif has no separate nodata mask (nodata is None). Use MTRDR footprint or valid mask to separate `not_detected` from `no_coverage` |
| MTRDR parameters | team-chosen (assumed) | No verified published cut-off. Suggest: `strong_orbital` if the parameter exceeds a local-scene robust threshold (for example median + k x MAD) on two independent diagnostic parameters (carbonate: D2300 and BD2500_2; olivine: OLINDEX3 and BD1300), `weak_orbital` if only one. Mark these thresholds `assumed` in `profiles.yaml` style, not cited |

---

## 2. Geologic maps

| Product | URL (opened) | Format | Scale and resolution | Coverage | Licence | Notes |
|---|---|---|---|---|---|---|
| **USGS Scientific Investigations Map 3464**: "Geologic map of Jezero crater and the Nili Planum region, Mars" | https://pubs.usgs.gov/publication/sim3464 (via curl; WebFetch got 403) | Database zip: https://pubs.usgs.gov/sim/3464/sim3464_database.zip (349,336,873 bytes, HTTP 200). Contains an **Esri file geodatabase** `Geodatabase/Nili.gdb` (not shapefiles), an `.mxd`, `.lyr` symbology, and rasters. Readme, metadata: https://pubs.usgs.gov/sim/3464/sim3464_readme.txt , https://pubs.usgs.gov/sim/3464/sim3464_metadata.txt | Map scale 1:75,000. Mapped on a semi-controlled CTX mosaic | Whole site (map covers Jezero and Nili Planum; the readme lists a CTX mosaic raster `Nili_CTX_Mosaic_Beta01_Feb2018_MTM.tif`) | Metadata has only a "no warranty" liability statement and "Fees: None". Public domain is stated on a usgs.gov media page that a search returned but I did not open: **UNVERIFIED**, confirm before redistribution | **Identifier confirmed: SIM 3464** (Sun and Stack 2020, pamphlet 14 p., 1 sheet, DOI 10.3133/sim3464, first posted 2020-12-02). So the PRD's "SIM 3464" is correct. Related data-release DOI 10.5066/P9CZYIO7 listed on the page; it redirects to a USGS ArcGIS web app, not a downloadable item (checked headers only) |
| Stack et al. 2020, "Photogeologic map of the Perseverance rover field site in Jezero crater" (Space Sci Rev 216:127, doi:10.1007/s11214-020-00739-x) | Not opened | Search results only | 1:5000 on HiRISE (search summary) | Field site (UNVERIFIED) | UNVERIFIED | GIS data availability not found. A Caltech record lists a supplementary zip (`11214_2020_739_MOESM3_ESM.zip`), contents unknown. UNVERIFIED |
| Crumpler et al., in situ geologic context map shapefiles | https://zenodo.org/records/7545299 (VERIFIED) and https://data.mendeley.com/datasets/7jf5jdwh3g/1 (preliminary version, first 465 sols) | ESRI shapefiles (`.shp/.shx/.dbf/.prj`), layers include Séítah, Máaz-type members (Roubion, Rochette, Artuby, Chal, Nataani), regolith classes, ripples, `GXM_Level_1..5`, crater rim | Local (rover-scale) | Crater floor traverse, sols to about 465; overlaps the landing site and floor part of our site (extent not checked) | CC BY 4.0 (VERIFIED on both) | Optional: good for a floor geology refinement. The `.prj` content was not inspected |
| Northern fan geologic mapping shapefiles | https://data.mendeley.com/datasets/mph7pj2sjc/1 | shapefiles | n/a | Northern fan (outside the western delta front; extent not checked) | CC BY 4.0 (VERIFIED) | Not a priority |

SIM 3464 database details (VERIFIED from readme):
- Feature dataset `Mars2000Sphere_MTM_Clon77_5E`, projection Mars Transverse Mercator, central meridian 77.5 E, Mars 2000 sphere (R = 3396190 m). Feature classes: `GeoUnits_v1` (polygon), `GeoContacts`, `LinearFeatures`, `SurfaceFeatures`, `LocationFeatures`, `megabreccia_fig2`, `CrossSections`, `LandingEllipse`, others.
- **Positional offset warning (readme, VERIFIED):** the map "will have random offsets of about 100-200 meters from the surface operations CTX mosaic" (the controlled mosaic that our HiRISE and CTX products align to). At 8 m cells this is 12 to 25 cells, so unit boundaries will not line up with our HiRISE rasters; either shift/registration-tolerate it or state it in the layer's `does_not_measure`.
- Reading it: the repo venv has rasterio only (no fiona, pyogrio, geopandas, shapely, pyproj). FileGDB needs GDAL's OpenFileGDB driver via pyogrio or fiona, or Esri tools. I did not test pyogrio. Install into a pipeline env, convert once to GeoJSON or GeoPackage in MTM or eqc, and commit the small output.
- Unit codes seen in the pamphlet text (VERIFIED, https://pubs.usgs.gov/sim/3464/sim3464_pamphlet.pdf, read as text): Njf (Jezero floor), NHjf1 and NHjf2 (Jezero fan units 1 and 2), Nue and Nle (upper and lower etched units), Nnp1 and Nnp2 (Nili Planum units), mNhm, plus Nrb. **The full unit list and descriptions are on the map sheet and in the `GeoUnits_v1` attribute table, which I did not read: UNVERIFIED.**

### Proposed tier mapping (geology)

| Source | Tier | Justification |
|---|---|---|
| SIM 3464 `GeoUnits_v1` units with a documented mineral association | `inferred_from_unit` | Pamphlet, "Significance" section (VERIFIED): "the geologic units mapped throughout this map area serve as proxies for associated mineralogic composition in areas where no orbital spectroscopic data are available... These associations allow mineralogy to be reasonably inferred in regions lacking orbital mineralogic data where the host geologic units have been mapped." Examples given: low-Ca pyroxene and Fe/Mg phyllosilicate with Nnp1; olivine and variable carbonate with Nle and Nue; mafic compositions with Njf and Nnp2 |
| Fan units (NHjf1, NHjf2) | `inferred_from_unit` for Fe/Mg smectite (and variable carbonate and olivine) | Pamphlet text cites Goudge 2015 and Horgan 2020: western fan "Fe/Mg smectite with variable carbonate and olivine"; carbonate and olivine "appear to occur within interpreted point-bar deposits". The pamphlet uses the older Goudge unit names (Fw, Fn) when describing these; the mapping between Fw/Fn and NHjf1/NHjf2 is **UNVERIFIED** |

Rule to keep: a unit-level association must never outrank a direct detection; tier is the maximum over sources, but report the contributing source ids as the contract says.

---

## 3. Perseverance traverse and waypoints

| Product | URL (opened, HTTP 200) | Format | Size | Last-Modified | Coverage |
|---|---|---|---|---|---|
| Waypoints | https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_waypoints.json | GeoJSON FeatureCollection of 708 Point features, name `M20_waypoints_sol2022_all` | 603,126 bytes | 2026-10-28 | Sols 13 to 2022 (VERIFIED first and last records) |
| Traverse | https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_traverse.json | GeoJSON FeatureCollection of 559 LineString features, name `M20_Traverse_Path_tosol2022`, `crs` = `urn:ogc:def:crs:OGC:1.3:CRS84` | 1,723,218 bytes | 2026-10-28 | One line per drive; properties `sol`, `fromRMC`, `toRMC`, `length`, `SCLK_START`, `SCLK_END` |
| Live position (small file) | https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_waypoints_current.json | JSON, 939 bytes (content not read) | | | UNVERIFIED content |
| MMGIS web app | https://mars.nasa.gov/maps/location/ (HTTP 200, page title "MMGIS") | | | | Hosts the same layers |

Guesses that returned HTTP 403 (do not use): `M20_samples.json`, `M20_sample_locations.json`, `M20_planned_route.json`. The directory `.../Layers/json/` lists nothing. I found **no official sample-location layer** among the endpoints I tried.

Waypoint properties (VERIFIED): `RMC` (site_drive), `site`, `drive`, `sol`, `easting`, `northing`, `elev_geoid`, `elev_radii`, `radius`, `lon`, `lat`, `roll`, `pitch`, `yaw`, `tilt`, `dist_m`, `dist_total_m`, `dist_km`, `final` (`y` end-of-drive: 554, `m` mid-drive: 132, `i` interpolated: 22; 'final' absent on 2 records), `Note`, `images` (panorama metadata).

Coordinate convention (VERIFIED):
- GeoJSON coordinates are `[lon_east, lat, elevation_m]`, degrees. The first waypoint (sol 13, site 3 drive 0) is 77.45088572 E, 18.44462715 N, elevation -2569.91 m.
- The PDS PLACES localization CSV (https://pds-geosciences.wustl.edu/m2020/urn-nasa-pds-mars2020_rover_places/data_localizations/best_interp.csv ; column definitions in `best_interp.csv.xml`) gives the same position as `planetocentric_latitude` = 18.444627146 and `longitude` = 77.450885729 for SITE 1, with `planetodetic_latitude` = 18.346169209. So the MMGIS `lat` is **planetocentric** and longitude is east. This matches our site convention. CRS84 in the traverse file just means lon/lat order; it is Mars, not Earth, so do not reproject with an Earth datum.
- `easting`/`northing`: metres on an equirectangular projection; easting/(R x lon_rad) is about 0.9487 = cos(18.44), i.e. standard parallel near 18.4 N on a 3396190 m sphere (DERIVED check). Use lon/lat, not easting/northing.
- `elev_geoid` is relative to the Mars geoid/areoid (matches our MOLA-delta-geoid DTM convention; not cross-checked numerically).

Coverage in our site (DERIVED with the bbox above): 348 waypoints (sols 13 to 1001) and 273 drive segments (sols 14 to 1002) fall in or cross the site. After about sol 1002 the rover is west of the site (sol 1084 is at 77.327 E). The traverse is currently complete to sol 2022 (file name), 45.25 km total (last waypoint `dist_km`).

Licence: no licence statement on the JSON endpoints. NASA's guidelines page (https://www.nasa.gov/nasa-brand-center/images-and-media/, VERIFIED) says NASA content "generally are not subject to copyright in the United States", NASA should be acknowledged as the source, factual use that does not imply endorsement needs no permission. Credit "NASA/JPL-Caltech, Mars 2020 MMGIS". Pull the files once and commit a trimmed copy rather than hotlinking.

Tier mapping: the traverse itself is not evidence. It is the geometry for `confirmed_in_situ` radius logic ("measured at or near this location") and for the routing layers.

---

## 4. Samples and in-situ findings

### 4a. Sources

| Source | URL (opened) | What it gives | Licence/citation |
|---|---|---|---|
| NASA "Mars Rock Samples" | https://science.nasa.gov/mission/mars-2020-perseverance/mars-rock-samples/ | 30 rows: sample name, feature, sol sealed, rock type, sampling-location region. **No coordinates.** One image caption on the page gives sample 23's date as sample 1's (Aug 6, 2021, sol 164): a page error | Images credit NASA/JPL-Caltech; no licence text on the page |
| PDS Sample Dossier bundle (initial reports) | https://pds-geosciences.wustl.edu/m2020/urn-nasa-pds-mars2020_sample_dossier/initial_reports/ ; readme https://pds.nasa.gov/data/pds4/releases/geo/mars2020_sample_dossier-20260213/readme.txt | PDFs, volumes 1 to 5: `initial_reports_volume1.pdf` (113 MB, samples 1-10), volume2 (105 MB, samples 11-21), volume3 (88 MB, 22-24), volume4 (191 MB, 25-27), volume5 (39 MB, 29-33). Readme says they contain "geologic context, workspace observations, and applicable science results from various Perseverance instruments" and cache-point locations. All except volume 5 exceed the 50 MB limit, so **I did not open the PDFs**; whether they list lat/lon per sample is UNVERIFIED. Note PDS numbering (to 33) differs from NASA's 30-row list (it includes witness and extra tubes) | PDS public archive |
| PDS rover localization (PLACES) | https://pds-geosciences.wustl.edu/m2020/urn-nasa-pds-mars2020_rover_places/ (`best_interp.csv`, 8.7 MB, columns incl. site, drive, sol, planetocentric_latitude, longitude, elevation) | Rover position per drive and sol, authoritative version of the MMGIS waypoints | PDS public archive |
| PDS Three Forks sample depot | https://pds-geosciences.wustl.edu/m2020/urn-nasa-pds-mars2020_sample_depot_three_forks/ (directory listing opened; contents not read) | Tube positions of the 10-sample backup depot (per search result; UNVERIFIED) | PDS public archive |
| PDS instrument bundles | https://pds-geosciences.wustl.edu/m2020/ lists `urn-nasa-pds-mars2020_pixl`, `_sherloc`, `_supercam` (names seen in listing, VERIFIED) | Raw and derived instrument data; not opened | PDS |
| Farley et al. 2022, Science, "Aqueously altered igneous rocks sampled on the floor of Jezero crater, Mars", DOI 10.1126/science.abo2196 | https://www.usgs.gov/publications/aqueously-altered-igneous-rocks-sampled-floor-jezero-crater-mars (abstract read) | Séítah: coarse-grained olivine-rich rock; Fe-Mg carbonates along grain boundaries; Máaz above it (lava flows or chemical counterpart); sulfates and perchlorates in voids | Paper |
| Williams et al., LPSC 2023 abstract, "Exploring the Jezero delta front" | https://ntrs.nasa.gov/api/citations/20230000588/downloads/Williams%20et%20al_LPSC%202023%20Delta%20Front%20Overview%20abstract_v5.pdf (read) | Sample sequence by sol and location (Skinner Ridge in lower Rockytop: Swift Run, Skyland; Hogwallow Flats: Hazeltop, Bearwallow; Cape Nukshak/Amalik: Shuyak, Mageik; Hidden Harbor/Yori Pass: Kukaklek; regolith Atmo Mountain and Crosswind Lake from ripple Observation Mountain). At Cape Nukshak, olivine and pyroxene detected by SuperCam LIBS and VISIR; outcrop Amalik VISIR "most consistent with a Mg-rich serpentine"; carbonate and/or sulfate alteration minerals in delta front abrasion targets | Conference abstract (NTRS) |
| Hurowitz et al., Goldschmidt 2023 abstract, PIXL, doi:10.7185/gold2023.18487 | https://goldschmidtabstracts.info/2023/18487.pdf (read) | PIXL: olivine-dominated altered source at the base (Amalik member, Cape Nukshak); likely serpentinized mafic to ultramafic sources at Hawksbill Gap (Rockytop member); "Fe-bearing carbonate at both locations"; Fe-Mg sulfates and phyllosilicates in Yori Pass, Hogwallow Flats, Devils Tanyard | Abstract |
| JPL news, Comet Geyser | https://www.jpl.nasa.gov/news/rock-sampled-by-nasas-perseverance-embodies-why-rover-came-to-mars/ (read) | Bunsen Peak, core taken March 12 = sol 1088, "about 75% carbonate grains cemented together by almost pure silica", SuperCam and PIXL used | NASA/JPL-Caltech credit |

### 4b. Samples inside our site (NASA list; positions DERIVED)

Position method (DERIVED, not official): lon/lat of the last `M20_waypoints.json` record at or before the sol sealed. Actual coring can occur several sols earlier and the rover may have driven between waypoint and coring, so treat as roughly within tens to a few hundred metres. Samples 1 to 23 all fall inside the site bbox by this method; sample 23 is only about 200 m from the west edge. Replace with PDS dossier or PLACES positions at the actual coring sol if available.

| # | Sample | Feature | Sol sealed | NASA rock type | Region | Derived lon E, lat N (waypoint sol) | Mineral statements I actually read (source) |
|---|---|---|---|---|---|---|---|
| 1 | Roubion | Roubion | 164 | Atmospheric | Polygon Valley | 77.45165, 18.42769 (157) | none (atmospheric sample) |
| 2 | Montdenier | Rochette | 194 | Igneous | Artuby Ridge | 77.44437, 18.43074 (180) | none sample-specific; Farley 2022 is unit-level only |
| 3 | Montagnac | Rochette | 196 | Igneous | Artuby Ridge | 77.44437, 18.43074 (180) | same |
| 4 | Salette | Brac | 262 | Igneous | South Séítah | 77.44302, 18.43396 (248) | Séítah is olivine-rich with Fe-Mg carbonate on grain boundaries (Farley 2022 abstract, unit level) |
| 5 | Coulettes | Brac | 271 | Igneous | South Séítah | 77.44302, 18.43396 (248) | same |
| 6 | Robine | Issole | 298 | Igneous | South Séítah | 77.44137, 18.43266 (286) | same |
| 7 | Malay | Issole | 337 | Igneous | South Séítah | 77.44131, 18.43261 (335) | same |
| 8 | Ha'ahóni | Sid | 371 | Igneous | Octavia E. Butler Landing / Ch'ał | 77.45243, 18.44390 (362) | none |
| 9 | Atsá | Sid | 377 | Igneous | same | 77.45243, 18.44390 (362) | none |
| 10 | Swift Run | Skinner Ridge | 490 | Sedimentary | Delta Front | 77.40617, 18.45890 (477) | Delta rocks: Fe-bearing carbonate at Hawksbill Gap and Cape Nukshak (Hurowitz abstract); Skinner Ridge is lower Rockytop (Williams). Sample-specific: none read |
| 11 | Skyland | Skinner Ridge | 495 | Sedimentary | Delta Front | 77.40617, 18.45890 (477) | same |
| 12 | Hazeltop | Wildcat Ridge | 509 | Sedimentary | Delta Front | 77.40592, 18.45865 (502) | Williams places cores Hazeltop and Bearwallow at Hogwallow Flats (sulfate-cemented siltstone, hydrated sulfate); sample-specific carbonate: none read |
| 13 | Bearwallow | Wildcat Ridge | 516 | Sedimentary | Delta Front | 77.40592, 18.45865 (502) | same |
| 14 | Shuyak | Amalik | 575 | Sedimentary | Delta Front | 77.40144, 18.45073 (565) | Amalik: olivine-dominated source (PIXL); VISIR consistent with Mg-rich serpentine; olivine and pyroxene by LIBS and VISIR at Cape Nukshak (Hurowitz; Williams) |
| 15 | Mageik | Amalik | 619 | Sedimentary | Delta Front | 77.39915, 18.45364 (609) | same |
| 16 | Kukaklek | Hidden Harbor | 631 | Sedimentary | Delta Front | 77.40144, 18.45163 (629) | Yori Pass: Fe-Mg sulfates and phyllosilicates (Hurowitz) |
| 17 | Atmo Mountain | Observation Mountain | 634 | Regolith | Delta Front | 77.40144, 18.45146 (632) | none read (a search summary lists olivine, feldspar, pyroxene and Mg-Fe carbonates: UNVERIFIED) |
| 18 | Crosswind Lake | Observation Mountain | 639 | Regolith | Delta Front | 77.40144, 18.45146 (632) | same |
| 19 | Melyn | Berea | 749 | Sedimentary | Upper Fan | 77.38407, 18.47020 (736) | none read |
| 20 | Otis Peak | Emerald Lake | 832 | Sedimentary | Upper Fan | 77.36828, 18.48301 (821) | none read |
| 21 | Pilot Mountain | Dream Lake | 913 | Sedimentary | Upper Fan | 77.35078, 18.48344 (911) | none read |
| 22 | Pelican Point | Hans Amundsen Memorial Workspace | 923 | Sedimentary | Margin Unit | 77.35078, 18.48344 (911) | none read |
| 23 | Lefroy Bay | Turquoise Bay | 949 | Sedimentary | Margin Unit | 77.34704, 18.48871 (933) | none read |

Outside the site (derived position about 77.327 E and further west, so out): 24 Comet Geyser (sol 1088, "silica-cemented carbonate", about 75% carbonate grains, JPL page), 25 Sapphire Canyon (sol 1215, sedimentary), 26 to 30 crater-rim samples (Silver Mountain, Green Gardens "Serpentinite", Main River, Bell Island unsealed, Gallants unsealed). Include them only if the site is ever enlarged. Green Gardens serpentinite is a NASA rock-type label only.

### 4c. Proposed tier mapping (in situ)

| Source | Tier | Justification |
|---|---|---|
| Sample rock-type labels (NASA list) | `confirmed_in_situ` for **rock class only** (igneous, sedimentary, regolith), not for a specific mineral | The list gives no minerals |
| Séítah (samples 4 to 7): olivine and Fe-Mg carbonate | `confirmed_in_situ` at the sample point and its workspace radius, for olivine and carbonate | Farley 2022 abstract names both for Séítah; it is a unit-level statement, so apply it at sample points, not across the whole unit |
| Amalik (14, 15): olivine, serpentine | `confirmed_in_situ` at point for olivine and serpentine | PIXL (olivine-dominated source) and SuperCam VISIR (Mg-rich serpentine, Williams) |
| Delta front sample points (10 to 16): Fe-bearing carbonate | `confirmed_in_situ` at point only if we accept a location-level reading of "at both locations" (Cape Nukshak and Hawksbill Gap) | Hurowitz abstract is section-level. Conservative alternative: tier 3 or 2 nearby, and promote to 4 only after reading the per-sample dossier |

Define "at or near" (contract: "at or near this location") as an explicit radius, for example 25 m from the derived point. That value is an assumption, to be labelled `assumed`.

---

## 5. Mars Trek (optional)

- https://trek.nasa.gov/mars/ opens (HTTP 200, title "Mars Trek"). The API doc https://trek.nasa.gov/tiles/apidoc/trekAPI.html?body=mars (read) gives only the WMTS template `https://{WMTS endpoint}/1.0.0/{Style}/{TileMatrixSet}/{TileMatrix}/{TileRow}/{TileCol}.png` (some layers .jpg; read ResourceURL in each layer's capabilities) and says a missing tile means no coverage. It lists **no CRISM, mineral or Jezero layers**.
- Tested and failed: `https://trek.nasa.gov/tiles/Mars/EQ/WMTSCapabilities.xml` (404), `.../Mars/EQ/Viking_MDIM21_ClrMosaic_global_232m/1.0.0/WMTSCapabilities.xml` (404), `https://trek.nasa.gov/tiles/Mars/EQ/?SERVICE=WMTS&REQUEST=GetCapabilities` (403), `https://api.nasa.gov/mars-wmts/catalog` (connection failed).
- Conclusion: **UNVERIFIED** whether any CRISM mineral layer is exposed; not recommended. Using PDS MTRDR directly gives 18 m data that Trek would not.

---

## 6. Open questions and risks

1. **Contract mismatch on mineral layer form.** `docs/scope-c-d.md` (lines 145 and 152) assumes `layer_min_<name>.tif`, 0 to 1 abundance. `contracts/mcp-tools.md` section 3.3 says evidence layers are ordinal tiers plus source ids. None of the sources above give abundances at our scale (Zastrow and Glotch unmixing does, at 6.7 GB). Recommend the ordinal tier rasters (uint8: 0 to 4, 255 = no coverage) and telling Stream C to drop the 0-1 assumption.
2. **Biggest risk: orbital geometry.** Three sets of offsets stack: SIM 3464 is offset 100 to 200 m from the controlled CTX (readme); CRISM MTRDR geolocation has its own registration error that I did not check; the Parente map was coregistered to the controlled CTX and HiRISE DEM by its authors (description) but I did not measure the residual. At 8 m cells and an 8 km site, tier boundaries can be 10 to 25 cells off relative to rover points. Do not claim cell-level agreement between orbital tiers and sample points; state the registration uncertainty in `does_not_measure`.
3. **`not_detected` vs `no_coverage`.** The Parente tif has no nodata mask (black = unclassified). About 25% of the site is black. Derive coverage from MTRDR footprints (ODE bounds are rectangles of the scene hull, not valid-pixel masks) or from MTRDR validity masks; otherwise `not_detected` will be overclaimed.
4. **Parente map colour blending.** About 35% of pixels in the site window are not within 8 counts of a key colour (interpolated edges). Decode with a tolerance and a nearest-key rule, or request the underlying class raster from the authors.
5. **No verified numeric detection threshold** for Viviano-Beck parameters, nor for Horgan 2020 or Brown 2020. Any MTRDR threshold is a team assumption; label it so.
6. **FileGDB reading.** SIM 3464 ships as an Esri file geodatabase (349 MB zip, of which the vector part is small). The repo venv has rasterio but no vector library. Conversion tooling (pyogrio, or GDAL CLI) must be added. The `GeoUnits_v1` attribute names and unit list are unread: do a first pass before promising unit to mineral tables.
7. **Sample coordinates are derived, not official.** NASA's sample page has no lat/lon; no MMGIS sample endpoint found; the PDS dossier PDFs (some over 100 MB) were not opened. The location error of derived positions is unquantified (rover can move between the previous waypoint and the coring sol, or after the sealing sol). Also the NASA list (30) and PDS dossier (33) number samples differently.
8. **Mineral statements are mostly unit or section level.** Only Séítah (Farley), Amalik (Hurowitz/Williams) and Comet Geyser (JPL, outside site) statements were read. Sample-by-sample mineralogy for samples 2, 3, 8, 9, 17 to 23 is not supported by anything I opened; keep them as rock-class evidence only.
9. **Licences.** Verified: Zenodo Parente CC BY 4.0, Crumpler CC BY 4.0, Zastrow and Glotch CC BY 4.0, Horgan Mendeley CC BY-NC 3.0 (non-commercial; avoid redistributing). NASA and PDS data: no explicit licence on the pages opened; NASA guidelines page says generally not copyrighted, acknowledge NASA. USGS public-domain status of SIM 3464: UNVERIFIED (not seen on a page I opened).
10. **Traverse currency.** The JSON is regenerated by NASA (last modified 2026-10-28, to sol 2022); pin a copy and record the retrieval date and ETag (waypoints `6e29cc7bd422e3da1a1e78e0c4855dc1`, traverse `fafb42a07541225925386bcb7b0935a7`, as returned by CloudFront on 2026-11-14).
11. **Naming.** MTRDR has no BD2300 band; use D2300 (see 1b). OLINDEX3 is the current olivine index (OLINDEX, OLINDEX2 are older).
12. **Not looked at:** CRISM FRS (full-resolution spectral) and MSP products; SuperCam/PIXL/SHERLOC PDS data tables; Perseverance Mastcam-Z-based unit maps; HiRISE-scale geologic maps beyond Crumpler. Stack et al. photogeologic map GIS release status is unknown.

## 7. Retrieval log (what was actually fetched)

- Opened/curl 200: pubs.usgs.gov SIM 3464 page, readme, metadata, pamphlet PDF, database zip headers and central directory (by Range requests); mars.nasa.gov MMGIS JSON (both full downloads, 0.6 and 1.7 MB); oderest.rsl.wustl.edu query; pds-geosciences.wustl.edu MTRDR directory, `.hdr`, and a 456-row x 464-column window of 6 bands by Range request; Zenodo API records 5575824, 4673066, 4408577, 7545299 and the three small Parente files (4.7 MB total in a scratch dir); Mendeley dataset pages b39x2kfbj4, 7jf5jdwh3g, mph7pj2sjc; Caltech Viviano-Beck PDF; PDS CRISM summary parameter reference PDF; arXiv 1801.09841; NASA sample list page; JPL Comet Geyser page; USGS Farley record; NTRS Williams PDF; Goldschmidt Hurowitz PDF; NASA media guidelines page; PDS Sample Dossier, PLACES and Three Forks directory listings; Trek API doc page.
- Failed or unreachable: data.usgs.gov (403), api.nasa.gov (no connection), trek.nasa.gov WMTS capabilities (404/403), MMGIS guessed sample endpoints (403).
