# Jezero paleolake levels used by `flood_at_level`

Every number below was read in a source that was opened and read during this work. Quotes are verbatim. Elevations are metres, as printed in the sources. The sources do not name their vertical datum in the text that was read (see "Datum" below).

## Levels in use (atlas_core/lake.py NAMED_LEVELS)

| id | level (m) | what it is | source quote | kind |
|---|---|---|---|---|
| `delta_top_2410` | -2410 | Uppermost delta foresets observed so far (north of Belva crater). Highest delta-recorded lake level; a MINIMUM water level during deposition | "images within Belva crater enabled the localization of the uppermost foresets observed so far at an elevation of -2410 m" [M24] | measured (minimum) |
| `delta_drying_start_2390` | -2390 | Upper end of the lake-level range over which the western delta was deposited in the Villette et al. model | "between the lake levels of -2390 and -2500 m (where 240 < V < 110 km3)" [V25] | modeled |
| `eastern_breach_ca_2400` | -2400 | Elevation of the eastern breach (outlet), approximate | "in a closed basin below the elevation of the eastern breach toward the East (ca. -2400 m)" [M24] | approximate |
| `carew_castle_2445` | -2445 | Topset/foreset transition at Carew Castle (fan top) | "Its elevation of -2445 m, is 25 to 50 m higher than what has been determined at the fan front." [M24] | measured |
| `pinestand_2448` | -2448 | Tops of foresets at Pinestand Mountain; topsets eroded, so a minimum water level | "the tops of these beds mark the minimum water level during their deposition, at -2448 m" [M24] | measured (minimum) |
| `fan_front_2470` | -2470 | Upper end of fan-front topset/foreset transitions | "The transition between topsets and foresets varies from -2470 to -2500 m, corresponding to earlier lake levels" [M24] | measured |
| `kodiak_2490` | -2490 | Kodiak butte (lower of the two values quoted) | "in agreement with what has been deduced at the Kodiak butte, i.e., -2490 and -2500 m" [M24] | measured |
| `delta_low_2500` | -2500 | Lowest delta level (Eastern Cape and Kodiak) | "from -2410 m at the fan top toward -2500 m at the Eastern Cape and at Kodiak" [M24] | measured |
| `model_max_fill_2243` | -2243 | Lake elevation reached by the initial infilling (V = 450 km3) in the Villette et al. model. A model output, NOT an observed shoreline | "its initial infilling (V = 450 km3) up to a lake elevation of -2243m" [V25] | modeled |

Sequence interpreted by [M24]: the levels fall from -2410 m to -2500 m, "consistent with a progressive fall of the lake level, i.e. forced regression"; the visible delta formed "during its progressive drying out in a closed basin". The delta levels are therefore a lake that was falling, not one highstand.

### Not reconciled in the sources read

- [M24] puts the eastern breach at "ca. -2400 m", while [V25] models initial infilling up to -2243 m. The two abstracts do not explain the difference. `model_max_fill_2243` is shown only as a model scenario; use it with that label.
- There is no single published "highstand" number in the sources read. The tool has no alias "highstand". For the highest level that has an observation behind it, use `delta_top_2410` (a minimum, since beds above it may be eroded or not exposed).

### Context numbers (not levels)

- Outlet valley, from [FG17]: breach erosion "up to ~80 m depth"; canyon "~300-m deep, 1-km wide"; outlet valley slope "~3%".
- [J25] (relative offsets only): "10-50 m above the paleolake level implied by the Jezero western fan"; "up to 100 m below the highstand lake level". No absolute elevation, not used as a level.

## Levels NOT used (cannot be verified here)

These were named in the task but no elevation was read in an opened source, so they are not in the tool: Fassett & Head 2005 (GRL), Schon et al. 2012, Goudge et al. 2015/2017/2018, Mangold et al. 2021 (Science), Stack et al. The Science 2021 paper is only cited inside [M24] (as ref [6]); its own text could not be fetched. Pliva Vallis terrace elevations (Villette et al. 2025, JGR Planets, 10.1029/2024JE008862) are drawn in that paper's figures but the numbers were not readable (the publisher page returned 403 / the PDF URL 404), so they are not used.

## Datum and compatibility

- Atlas elevations (layer `elevation`): "Surface elevation above the MOLA areoid (delta-geoid corrected), 1 m HiRISE stereo DTM averaged to 2 m", from the Mars 2020 TRN HiRISE DTM mosaic (Fergason et al. 2020) (site.yaml layer metadata).
- [M24] and [V25] print elevations such as "-2410 m" without stating the datum in the text read. The values sit in the same range as the atlas data (the Perseverance landing point is -2569.83 m in `pois.geojson`; sample sites on the delta/margin are -2407 to -2584 m), which is consistent with a MOLA-areoid-type datum, but the datum is NOT confirmed by the sources. No offset is applied. Treat a systematic offset of a few metres as unverified; the HiRISE DTM vertical accuracy is not independently verified in this repo.
- Elevation uncertainty of the published levels: none stated in the abstracts read, except that the -2448 and -2410 values are minimum water levels and -2400 is "ca.".

## Method limits

`flood_at_level` floods the PRESENT-DAY topography. The modern surface is not the ancient lake floor: it has been eroded, the western delta was built into the lake, later deposits cover the floor, and the crater has been modified since. Areas, depths and "underwater" lists are a present-day-topography approximation, not a reconstruction of ancient bathymetry. The atlas window is about 8 km across, so for high levels the water extends beyond the window and areas are lower bounds.

## Sources

- [M24] Mangold, N., Caravaca, G., Gupta, S., Williams, R.M.E., et al. "Past variations of water level of Jezero paleolake", LPSC 2024 abstract. https://ntrs.nasa.gov/api/citations/20240000546/downloads/MangoldLPSC-2024v3.pdf (opened; text extracted locally).
- [V25] Villette, J., Mangold, N., Kite, E.S., Conway, S.J., Le Deit, L. "Modeling the hydrological evolution of Jezero's crater lake and its deltaic deposit", abstract (MTT 2025). https://www-mars.lmd.jussieu.fr/mtt2025/abstracts/Villette.pdf (opened; text extracted locally).
- [FG17] Fassett, C.I. and Goudge, T.A. "Hydrological modeling of the Jezero crater outlet-forming flood", LPSC abstract. https://ntrs.nasa.gov/api/citations/20170002037/downloads/20170002037.pdf (opened; text extracted locally). Contains no lake-level elevations.
- [J25] Jones, A., Barnes, R., Gupta, S., Paar, G., et al. (2025) "A fluvio-lacustrine environment preserved in the Jezero crater inlet channel, Neretva Vallis (Mars)", EPSC-DPS2025-888. https://meetingorganizer.copernicus.org/EPSC-DPS2025/EPSC-DPS2025-888.html
