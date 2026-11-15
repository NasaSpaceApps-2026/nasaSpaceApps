# Threshold sources for profiles.yaml

Research date: 2026-11-14. Re-verified 2026-11-15 against the source documents (quotes below were
re-read in the fetched page or in text extracted from the downloaded PDF; "(OCR)" marks quotes from a
scanned PDF). Items that could not be re-opened are in "Unverified - treat as assumed" at the end and
are not evidence. Nothing here has been applied to `profiles.yaml`.

Our profile values live in `data/build/site/jezero/profiles.yaml` (all currently `source: assumed`);
`slope_2m` is a Horn slope on our DTM (about a 2 m baseline); `rough_5x5` is the standard deviation of
elevation in a 5x5-cell window.

## Summary table

| Profile | Parameter | Current | Recommended | Status |
|---|---|---|---|---|
| landing_pad | slope_2m <= | 10 deg | keep 10 deg, cite as workshop guideline (weak) | partly cited, baseline mismatch |
| landing_pad | rough_5x5 <= | 0.5 m | keep | assumed (no source fits) |
| landing_pad | footprint_radius_m | 50 | keep | assumed (no source fits) |
| eva_walkable | flat_speed_mps | 0.9 | 0.56 (2 km/h planning pace) | CORRECTED wording: a NASA planning assumption for level lunar regolith, not a measurement |
| eva_walkable | max_slope_deg | 25 | 20 | cited as a lunar xEVA suit capability requirement, not Mars |
| eva_walkable | caution_slope_deg | 15 | keep | assumed |
| eva_walkable | eva_limit_h | 8 | keep 8 (nominal 6+/-2) | cited (lunar xEVA) |
| eva_walkable | reserve_margin | 0.2 | keep | assumed |
| eva_walkable | speed curve | tobler_placeholder | keep label | shape from Tobler; constants verified only via secondary source |
| rover_scout | flat_speed_mps | 0.04 | keep 0.04 | cited (Perseverance 152 m/h = 0.042 m/s) |
| rover_scout | caution_slope_deg | 15 | 16 | cited (ENav tilt limit at landing) |
| rover_scout | max_slope_deg | 20 | keep 20, or 24 if declared Perseverance-class | cited, see caveats |

---

## 1. Landing pad

### 1a. slope_2m <= 10 deg (current: 10, assumed)

Mars 2020 robotic rover limit (VERIFIED):

- Quote: "less than 25 deg-30 deg slopes at length scales of 2-5 meters to ensure stability and
  trafficability of the rover during and after landing." Same paragraph: "less than ~100 meters of
  relief at baseline lengths of 1-1,000 meters to ensure proper control authority and fuel consumption
  during powered descent."
- Source: Fergason, R. L., Hare, T. M., Mayer, D. P., Galuszka, D. M., Golombek, M. P., Otero, R. E.,
  Redding, B. L. (2018). "Mars 2020 Landing Site Evaluation: Digital Terrain Model Procedure and
  Capability Development." LPSC 49, abstract 1611. https://www.hou.usra.edu/meetings/lpsc2018/pdf/1611.pdf
- Caveat: a limit for a ~1 t rover on a sky-crane descent stage, not a crewed lander. Do not transfer
  25-30 deg to the pad profile.

Stricter earlier robotic limit (VERIFIED):

- Quote: "fifteen degrees or less over a 5 m length scale to ensure landing stability and trafficability
  of the rover" (also "fifteen degrees or less over a 20 to 40 m length scale" for altimetry).
- Source: Mars Science Laboratory Project (JPL), "MSL Landing Site Selection User's Guide to Engineering
  Constraints", Change Log April 2006.
  https://marsoweb.nas.nasa.gov/landingsites/msl/memoranda/MSL_Eng_User_Guide_v2.pdf
  The guide says its values "should be considered working assumptions subject to future review and
  revision." A 5 m baseline smooths more than our 2 m Horn slope, so 15 deg at 5 m is not directly
  comparable to 15 deg on slope_2m.

Crewed-mission guideline (VERIFIED, the only one found with a number):

- Quote: "An area of approximately 25 km2 within which the terrain is generally level (slopes less than
  ~10 degrees) and significantly devoid of landing hazards ..." The same list gives "Less than +2 km
  altitude (MOLA reference)".
- Source: NASA, "First Landing Site/Exploration Zone Workshop for Human Missions to the Surface of Mars,
  Second Announcement", Oct 27-30, 2015.
  https://www.nasa.gov/wp-content/uploads/2015/06/hls2-2nd-announcement-7-8.pdf
- Caveat: a call-for-proposals screening guideline for a ~25 km2 area, no stated slope baseline, not a
  lander touchdown limit. Supports "10 deg" as a community-guideline order of magnitude only.

Crewed lander touchdown slope: not found. NASA Human Exploration of Mars Design Reference Architecture
5.0 (Drake (ed.), NASA/SP-2009-566, 2009, https://www.nasa.gov/pdf/373665main_NASA-SP-2009-566.pdf) was
text-searched and gives no landing slope number (re-checked 2026-11-15).

Recommendation: keep 10 deg; label it "guideline: generally level terrain, slopes less than ~10 degrees
(NASA Human Landing Site/EZ workshop 2015, area scale, baseline unstated); robotic Mars 2020 limit 25-30
deg at 2-5 m (Fergason et al. 2018) is an upper bound for a different vehicle." Not a cited touchdown limit.

### 1b. rough_5x5 <= 0.5 m (assumed) - keep assumed

No source defines an elevation-standard-deviation threshold. Documented rock criteria are different
quantities (VERIFIED, MSL guide section 1.6): belly pan "0.6 m above the ground"; probability of a rock
taller than 0.6 m in 4 m2 "should be less than 0.25%", "a rock abundance ... of around 10%"; mobility
"would accommodate rocks that are 0.5 m high". Our 0.5 m is a coincidence, not an equivalence; rover-specific.

### 1c. footprint_radius_m = 50 (assumed) - keep assumed

No source gives a crewed pad footprint. Related only: Mars 2020 Lander Vision System reduces position
error "down to 40m relative to a map of the landing site" (VERIFIED, NASA, "Terrain Relative Navigation:
Landing Between the Hazards",
https://science.nasa.gov/science-research/science-enabling-technology/technology-highlights/terrain-relative-navigation-landing-between-the-hazards/).
That is position knowledge, not a pad size.

### 1d. quality_flag in [hirise_dtm]

Data-quality rule, not a physical threshold; no change.

---

## 2. eva_walkable

Gravity caveat: all suited data found are Apollo or xEVA (lunar). No Mars-gravity suited slope or speed
data were found (Mars ~0.38 g, Moon ~0.17 g). Do not call lunar numbers Mars-validated.

### 2a. flat_speed_mps (current 0.9, assumed) - recommend 0.56

- VERIFIED quote: "Assumes ~2 km/hr walking pace on relatively level regolith", "Does not account for
  slope or obstacles (boulders)", "Apollo 14 walked 1.45 km from lander" (slide "EVA Walking Range ~2 km").
- Source: Coan, D. (NASA EVA Strategic Integration Office), "NASA Exploration EVA Integrated HITL Testing
  and Analogs", Revision A, Jan 31, 2021, NTRS 20210000827.
  https://ntrs.nasa.gov/api/citations/20210000827/downloads/Exporation%20EVA%20Testing%20and%20Analogs%2020210000827.pdf
  (about 17 MB; use a plain download). 2 km/h = 0.556 m/s.
- CORRECTION to earlier "cited" label: this is a planning assumption on the slide, not a measured pace.
  Call it "NASA xEVA planning assumption", not "measured".
- Apollo corroboration (VERIFIED via OCR text): "(e.g., 50 ft. or more) the mobility rates exceeded 2.0
  ft./sec." (0.61 m/s); level-terrain walking gait "about 3.0 ft./sec." (0.91 m/s); "walking at the rate
  of 3 ft./sec. with a 41 pound (lunar weight) load is shown by the 2300 BTU/hr. metabolic rate".
  Source: Kubis, J. F., Elrod, J. T., Rusnak, R., Barnes, J. E., Saxon, S. C., "Apollo 16 Time and Motion
  Study (Final Mission Report)", NASA MSC, M72-6, July 1972, NTRS 19730008098.
  https://ntrs.nasa.gov/api/citations/19730008098/downloads/19730008098.pdf
  So 0.9 m/s matches an Apollo short-burst peak on level ground, not a sustained planning pace.
  The report also states: "the terrain was level, with only slight inclines or downhill slopes. Thus, no
  data are analyzed for effect of slope", so it gives no slope-speed data.
- Recommendation: 0.56 m/s as base planning speed; 0.9 only as a labelled peak value.

### 2b. max_slope_deg (current 25, assumed) - recommend 20

- VERIFIED quote: "Walking up/down/across a slope of up to 20 deg" (xEVA suit capabilities), with the
  note "Apollo 15 traversed on slopes of ~17 deg". Source: Coan 2021, NTRS 20210000827.
- Caveat: a lunar xEVA design capability requirement, not a measured safe slope for Mars; no stated DEM
  baseline, so not guaranteed to equal our 2 m Horn slope.

### 2c. caution_slope_deg (current 15, assumed) - keep assumed

Nothing sets a caution tier. One anecdote (VERIFIED, OCR): Apollo 16 CDR on a ~10 deg downhill, "rocky
downhill slopes caused the CDR to exercise a greater degree of caution" (Kubis et al. 1972). Not a limit.

### 2d. eva_limit_h (current 8) - keep 8, cite

- VERIFIED quote: "Supports EVAs of up to 8 hours in duration (6+/-2 hours)". Source: Coan 2021.
- 8 h is the upper capability of the lunar xEVA suit; nominal planning is 6 h. For Mars, DRA 5.0 gives
  only a distance: "(approximately 15 km, or EVA walk-back distance)" (VERIFIED, Drake (ed.) 2009,
  NASA/SP-2009-566), not a time limit.

### 2e. reserve_margin 0.2 - keep assumed

No verified source gives a fraction. The Coan deck supports having a reserve ("Unpressurized Rover Range
~10 km (~5 hr walk back)", "(< 8-hr walk back)") but gives no number.

### 2f. Speed-slope curve (Tobler)

- Source: Tobler, W. (Feb 1993), "Three Presentations on Geographical Analysis and Modeling ...", NCGIA
  Technical Report 93-1, UC Santa Barbara (title/date confirmed via the secondary source below; the
  report text could not be re-opened on 2026-11-15, see Unverified).
- Formula (VERIFIED against Wikipedia only, secondary): W = 6 exp(-3.5 |dh/dx + 0.05|) km/h, off-path
  factor 3/5; flat speed about 5 km/h. https://en.wikipedia.org/wiki/Tobler%27s_hiking_function
- Caveats: Earth hiking on Alpine paths (Imhof data), unsuited. Only the shape should be borrowed.
  Code uses `flat * exp(-3.5 * tan(slope))`, which differs from Tobler: no +0.05 offset and no absolute
  value (downhill gets faster without limit). Label "placeholder" is correct. No Mars/lunar-gravity
  adapted Tobler found.
- Related, VERIFIED, not used for values: Baughman, Kim, Suri, Abercromby (2022), "APACHE Physical
  Workload Approximation", ICES-2022-209 (NTRS 20220003008): unsuited treadmill 0/10/20% grade at 0.67
  and 1.34 m/s; grade and speed both significant for metabolic rate. Useful for a future energy model.

---

## 3. rover_scout

### 3a. flat_speed_mps (current 0.04) - keep, cited

- VERIFIED quote: "Its top speed on flat, hard ground is just under 0.1 mph (152 meters per hour)."
  NASA, "Perseverance Rover Components", https://science.nasa.gov/mission/mars-2020-perseverance/rover-components/
  (152 m/h = 0.042 m/s).
- VERIFIED: "designed for a mechanical ground speed of 4.2 cm/s (approximately 2.5 m/min) on hard, flat
  terrain"; with hazard avoidance and onboard planning "50% of the mechanical speed, or 2.1 cm/s"; with
  visual odometry "25% ... around 1 cm/s" (MSL User's Guide 2006, working assumptions).
- VERIFIED: AutoNav "might enable Perseverance to hit a top speed of 393 feet (120 meters) per hour"
  (projection, 0.033 m/s) and "identifies hazards, and plans a route around any obstacles" (NASA/JPL,
  1 July 2021, https://www.nasa.gov/solar-system/nasas-self-driving-perseverance-mars-rover-takes-the-wheel).
- Caveat: top speed on flat hard ground; real drive averages are far lower.

### 3b. max_slope_deg and caution_slope_deg (current 20 and 15)

These are tilt limits, not DEM slope at a 2 m baseline.

- VERIFIED: "Perseverance is designed to withstand a 45-degree tilt in any direction without tipping
  over"; "rover drivers avoid terrains that would tilt the rover more than 30 degrees" (NASA rover
  components page). MSL guide: "vehicle tilt angles below 30 degrees". Static-stability limits.
- VERIFIED: ENav at landing: "the maximum terrain tilt it was allowed to climb was only 16 degrees, based
  on the worst-case drive performance of the Curiosity 'Scarecrow' rover in pure sand dunes"; "no AutoNav
  driving was possible due to tilts above 16 degrees and/or greater rock density". Source: Maimone, M.,
  Verma, V., et al., "Roving on the Edge: Robotic Operations Power Perseverance's Ascent of Jezero Crater
  Rim", AAS 08-138, JPL (sols 1267-1720). https://robotics.jpl.nasa.gov/media/documents/2026_RO_AAS_final.pdf
  The paper notes parameterized limits changed in flight software S8.1 (from Sol 1588, 8 Aug 2025).
- VERIFIED: "the team raised the rover's tilt limit to 24 degrees" and "reduced the clearance required
  under the rover's belly pan to 20 centimeters". Source: Verma, V., "Tracks along the Longest Drive on
  Another World", NASA Science blog (text dated Oct 12, 2026; page header Oct 21, 2026),
  https://science.nasa.gov/blog/tracks-along-the-longest-drive-on-another-world/ . The number 24 does not
  appear in the AAS paper text we extracted; the blog is the only source for it.
- VERIFIED (MSL guide 2006, granular slip): "Transitionary slope angle (10 - 17 degrees tilt) = 25 - 80%
  upslope slip"; "High slope angle (> 17 degrees tilt) = > 80% upslope slip".
- Recommendation: caution_slope_deg = 16; max_slope_deg keep 20 for a generic scout (between the 16 and
  24 autonomous limits, below the 30 operations threshold), 24 only if declared Perseverance-class.
  Label as "rover tilt limit (Perseverance), 2 m Horn slope is a rough proxy".

---

## Unverified - treat as assumed

Not re-opened or not supported on 2026-11-15; do not cite.

- Tobler 1993 report text itself (quotes "have worked out a simple example for walking in variable terrain
  using the hiking function (Figure II) ... Imhof (1950)" and "simply calculates the slope ... converts this
  to a walking velocity"): escholarship PDF download returned empty. The 6 / -3.5 / 0.05 / 3-5 constants are
  verified only through Wikipedia.
- Gernhardt, Norcross, Vos (2008) Integrated Suit Test 1, "NTRS 20080013604" and the "10 kilometer walkback
  scenario on the Moon (1/6-g)" quote: NTRS record not found; searches only confirm IST-1 is SAE 2008-01-1951
  and the Walkback Test is SAE 2007-01-3133 (10 km suited lunar walkback feasibility). Possibly useful lead:
  Norcross et al. 2010, "Metabolic costs and biomechanics of inclined ambulation and exploration tasks in a
  planetary suit" (NTRS 20110003409), which has inclined suited data; not read.
- Townsend & Maimone, LSIC 2024 poster ("2005: Spirit - 30 deg slope"): not re-opened.
- Apollo guideline ALSJ/NTRS 19790073512 ("1200 BTU/hr at 3.3 km/hr", "20% pad ... emergency walkback"):
  URL 404/empty; the 20% reserve rests on this lead only.
- Olthoff (2018), "EVA Walk-Back Limit Calculation Using the Virtual Spacesuit": HTTP 403, not read.
- Apollo 14 "ten-degree slope to Cone Crater" and Apollo 15 speed progression: not opened.
- Golombek et al. (2012), Space Sci. Rev. 170, 641-737 (MSL landing site): paywalled, not read. NASA Mars
  Next ~7% rock abundance for Mars 2020: host failed TLS, not opened.
- NASA xEVA requirements document itself (1.38 km walk-back claim in a search summary): not opened.
- Context only: "slopes <15 deg and rock abundance <10%" is the InSight constraint, not Mars 2020
  (VERIFIED: Golombek et al. 2020, JGR Planets 125(8), e2020JE006502, "Assessment of InSight Landing Site
  Predictions", PMC7507760: "slopes <15 deg at 84 and 2 m length scales for radar tracking and touchdown
  stability", "low rock abundance (<10%)"). A robotic limit, not a crewed-lander criterion.
- No source found for: crewed Mars lander touchdown slope or rock height; pad footprint radius; roughness
  (elevation std) thresholds; Mars-gravity suited speed-slope curves; any reserve-fraction value.
