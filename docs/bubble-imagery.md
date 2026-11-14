# Bubble imagery sources

Links only. No GeoTIFF, Navcam frame, or panorama is stored in this repo. Checked 14 Nov 2026: the four GeoTIFFs answered a 1-byte range request, and the two MMGIS JSON files returned HTTP 200. Sizes below are the catalog figures; the range responses match them (HiRISE DTM 1.84 GB, HiRISE ortho 6.85 GB, CTX DTM 9.2 MB, CTX ortho 35 MB).

The refine loop does not read these URLs. It reads a small bundle (`dem.npy`, `ortho.png`, `grid.json`, `viewpoints.json`, a few PNGs). Cutting that bundle is a later step.

## Satellite base (NASA MRO, published by USGS)

These are the Mars 2020 Terrain Relative Navigation mosaics. Mission: Mars Reconnaissance Orbiter. The landing-site maps were made by the USGS Astrogeology Science Center for Mars 2020.

| Use | Product | Catalog | File | Size | Posting |
|---|---|---|---|---|---|
| Bubble height | HiRISE DTM mosaic | [catalog](https://astrogeology.usgs.gov/search/map/mars_2020_terrain_relative_navigation_hirise_dtm_mosaic) | [1 m GeoTIFF](https://planetarymaps.usgs.gov/mosaic/mars2020_trn/HiRISE/JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif) | 1.8 GB | 1 m |
| Bubble nadir texture | HiRISE orthomosaic | [catalog](https://astrogeology.usgs.gov/search/map/Mars/Mars2020/JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first) | [25 cm GeoTIFF](https://planetarymaps.usgs.gov/mosaic/mars2020_trn/HiRISE/JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first.tif) | 6.9 GB | 0.25 m, 1 band |
| Fallback height | CTX DTM mosaic | [catalog](https://astrogeology.usgs.gov/search/map/Mars/Mars2020/JEZ_ctx_B_soc_008_DTM_MOLAtopography_DeltaGeoid_20m_Eqc_latTs0_lon0) | [20 m GeoTIFF](https://planetarymaps.usgs.gov/mosaic/mars2020_trn/CTX/JEZ_ctx_B_soc_008_DTM_MOLAtopography_DeltaGeoid_20m_Eqc_latTs0_lon0.tif) | 9.3 MB | 20 m |
| Fallback texture | CTX orthomosaic | [catalog](https://astrogeology.usgs.gov/search/map/Mars/Mars2020/JEZ_ctx_B_soc_008_orthoMosaic_6m_Eqc_latTs0_lon0) | [6 m GeoTIFF](https://planetarymaps.usgs.gov/mosaic/mars2020_trn/CTX/JEZ_ctx_B_soc_008_orthoMosaic_6m_Eqc_latTs0_lon0.tif) | 35 MB | 6 m |

Per-image inputs and labels, without downloading the mosaics:

- HiRISE piece list: https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/HiRISE/index.html
- CTX piece list: https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/CTX/index.html

Coverage of the HiRISE mosaic is about 18.3068° to 18.6693° N, 77.2229° to 77.584° E. Projection is equirectangular, planetocentric latitude, longitude positive east, standard parallel 0, sphere radius 3,396,190 m. Gray, 8-bit ortho. DTM elevations are in meters. USGS reports the HiRISE DTM mosaic sits about 0.4 m below the CTX DTM mosaic (median).

The HiRISE ortho catalog also describes a JPL surface-operations mosaic in an orthographic projection centered at 77.4298° E, 18.4663° N, and calls that file 3.2 GB. That direct URL is not the `Online File Link` on the catalog page, so it is not listed here.

Cite Fergason et al., LPSC 2020: https://www.hou.usra.edu/meetings/lpsc2020/pdf/2020.pdf

DOIs:

- HiRISE orthomosaic: https://doi.org/10.5066/P9QJDP48
- HiRISE DTM mosaic: https://doi.org/10.5066/P9REJ9JN
- CTX orthomosaic: https://doi.org/10.5066/P9GV1ND3
- CTX DTM mosaic: https://doi.org/10.5066/P906QQT8
- HiRISE instrument: https://doi.org/10.1029/2005JE002605
- CTX instrument: https://doi.org/10.1029/2006JE002808

## Rover position (NASA Mars 2020)

MMGIS layers used by the public Perseverance map. These are rover-body waypoints, not camera rays.

- Waypoints (about 0.6 MB): https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_waypoints.json
- Traverse (about 1.7 MB): https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_traverse.json

Each waypoint is a GeoJSON point. Properties seen on the first feature include `sol`, `site`, `drive`, `RMC`, `lat`, `lon`, `easting`, `northing`, `elev_geoid`, `elev_radii`, `roll`, `pitch`, `yaw`, and an `images` list. The point coordinate is `[lon, lat, elev_geoid]`. `images[].url` is relative to `https://mars.nasa.gov/mmgis-maps/M20/` and points at cylindrical panoramas (for example a 360° Navcam-style autogen mosaic). A cylindrical panorama is not a pinhole frame. It cannot be dropped into `viewpoints.json` as a fit camera.

Operations localizations, including the database behind those poses, are the PDS PLACES bundle:

- https://pds-geosciences.wustl.edu/missions/mars2020/places.htm
- Bundle id: `urn:nasa:pds:mars2020_rover_places`

Use `elev_geoid` when checking a waypoint against the DTM. `elev_radii` is a different vertical number (the same sample is about −2569.91 m geoid and −4253.47 m radii).

## Rover pictures (NASA Mars 2020)

Single frames, with labels, are the fit and holdout photos.

- Raw browse: https://mars.nasa.gov/mars2020/multimedia/raw-images/
- PDS image bundles: https://pds-imaging.jpl.nasa.gov/volumes/mars2020.html
  - `urn:nasa:pds:mars2020_navcam_ops_calibrated` and `urn:nasa:pds:mars2020_navcam_ops_raw`
  - `urn:nasa:pds:mars2020_mastcamz_ops_calibrated` and `urn:nasa:pds:mars2020_mastcamz_ops_raw`
- MRO source archive for the orbital images: https://pds-imaging.jpl.nasa.gov/volumes/mro.html

The horizontal field of view and the camera direction relative to the rover come from the PDS geometric camera model on the calibrated label (CAHV / CAHVOR). The rover body pose comes from the waypoint or PLACES record for that site and drive. Both are required before `poses_trusted` can be set true. This file does not pick the sol or the frame.

## Frame mismatch to fix before a real bundle

USGS GeoTIFF meters are equirectangular with standard parallel 0: east-west distance is `radius * longitude_radians` with radius 3,396,190 m. Waypoint `easting` on the first feature (lon 77.45088572°, easting 4,354,494.086 m) matches `radius * cos(lat) * longitude_radians`, not the GeoTIFF scale. Overlaying waypoint easting on GeoTIFF pixel coordinates puts the rover in the wrong place. Warp both through latitude and longitude into one site frame, then write `grid.json`.

The shared site origin (`lat0`, `lon0`) is still a team number. The PRD placeholder is 18.46° N, 77.41° E. The JPL mosaic center quoted by USGS is 18.4663° N, 77.4298° E. An early waypoint is 18.44462715° N, 77.45088572° E. One of these has to be chosen and written down before the bubble mesh and the drive map can share coordinates.

## What is safe to assume about the rest of the program

Safe, once the bubble task is implemented:

- Teammates can load `baseline/mesh.glb` and `refined/mesh.glb` from the paths in `bubble_block.json`.
- Both meshes are standard glTF, Y-up, origin at the bubble center, east = +X, up = +Y, north = −Z.
- Vertex positions match. Slope, hazard, and rover pitch stay on the site DEM. The bubble does not publish a height overlay.
- `active` is `baseline` until the numeric gate passes on the synthetic fixture, or a person accepts a real bundle.

Not safe to assume:

- A real Navcam will line up with the HiRISE mesh on the first try. The compare PNG is the test, and the loop is required to refuse projection when `poses_trusted` is false.
- Their lighting will match the offline render. Scoring stays in this loop.
- A full-resolution software render of a 1 km mesh will be quick. The judge images stay at the camera size in `viewpoints.json`. The 6.9 GB orthomosaic is the long download; the bake is a window of it.
- The synthetic mound proves the file contract and the projector. It does not prove a Mars pose.
