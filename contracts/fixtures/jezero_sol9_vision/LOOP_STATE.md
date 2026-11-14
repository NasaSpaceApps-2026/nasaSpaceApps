# Vision loop state

The judge is the render PNG next to the reference photo. model.json is wrong when the PNG is wrong.

## Next
- done; the bubble is ready to compare with the projection loop

## set_01 | fit_0 | tick 1
### Gaps
- Near-field soil is much darker than the Navcam dust
- Bright cobble missing on the lower-right
- Ground is a smooth satellite drape with no pebble relief
### Edits
- paint 1849.59,-437.06 radius 6.0 rgb 188,186,180 strength 0.55
- bump 1848.97,-438.62 radius 1.5 height 0.45
- paint 1848.97,-438.62 radius 1.5 rgb 225,222,215 strength 0.80
### Render
- better
### Next
- stay on fit_0

## set_01 | fit_0 | tick 2
### Gaps
- Left ground is still too light; the Navcam shows darker pebbles and soil there
- Second bright cobble missing just above the first one
- Left-side rock relief is still flat
### Edits
- paint 1850.59,-436.29 radius 2.5 rgb 128,128,125 strength 0.60
- bump 1849.58,-438.36 radius 1.2 height 0.35
- paint 1849.58,-438.36 radius 1.2 rgb 215,213,208 strength 0.75
### Render
- better
### Next
- stay on fit_0

## set_01 | fit_0 | tick 3
### Gaps
- Light-toned rock field in the mid-distance is still just smooth ortho
- Dark pebble cluster left of center is still too light
- Mid-ground has no extra relief
### Edits
- paint 1853.96,-438.17 radius 3.5 rgb 175,173,168 strength 0.50
- bump 1853.96,-438.17 radius 2.0 height 0.35
- paint 1850.49,-436.84 radius 1.5 rgb 120,120,118 strength 0.65
### Render
- better
### Next
- stay on fit_0

## set_01 | fit_0 | tick 4
### Gaps
- Far-left ground is still too light versus the Navcam shadow/dark pebbles
- Right near-field soil is still duller than the sunlit dust
- Dark rock on the left still has no height
### Edits
- paint 1853.07,-434.08 radius 4.0 rgb 108,108,105 strength 0.50
- paint 1848.97,-437.97 radius 2.5 rgb 200,198,192 strength 0.45
- bump 1851.23,-435.72 radius 1.2 height 0.30
### Render
- better
### Next
- stay on fit_0

## set_01 | fit_0 | tick 5
### Gaps
- Mid-right light cobble still missing
- Left pebble field still too light
- Rock relief is still too sparse
### Edits
- bump 1850.89,-439.65 radius 1.5 height 0.40
- paint 1850.89,-439.65 radius 1.5 rgb 200,198,190 strength 0.60
- paint 1850.88,-435.41 radius 2.0 rgb 115,115,112 strength 0.50
### Render
- better
### Next
- stay on fit_0

## set_01 | fit_0 | tick 6
### Gaps
- Far ground still shows large HiRISE blotches unlike the even Navcam dust
- Light rock patch in the upper-left of the ground is missing
- Mid-distance relief is still too flat there
### Edits
- paint 1855.08,-438.52 radius 6.0 rgb 148,146,141 strength 0.40
- bump 1854.59,-436.57 radius 2.0 height 0.30
- paint 1854.59,-436.57 radius 2.0 rgb 185,183,176 strength 0.55
### Render
- better
### Next
- stay on fit_0

## set_01 | fit_0 | tick 7
### Gaps
- Right cobble hill is a bit too peaked versus the flatter Navcam dust
- Mid-left still lacks dark pebble patches
- Left-of-center dark stones still missing
### Edits
- smooth 1848.97,-438.62 radius 3.0 strength 0.45
- paint 1850.19,-436.44 radius 1.2 rgb 125,125,122 strength 0.55
- paint 1851.74,-437.02 radius 1.2 rgb 125,125,122 strength 0.50
### Render
- same
### Next
- advance to holdout_0

## set_01 | holdout_0 | tick 1
### Gaps
- Bright cobble on the right is still a vague hill, not a light stone
- Left pebble field is still too light from this camera
- Extra stereo-right bright rock is missing
### Edits
- bump 1849.24,-439.88 radius 1.4 height 0.35
- paint 1849.24,-439.88 radius 1.4 rgb 220,218,210 strength 0.70
- paint 1850.62,-435.72 radius 2.0 rgb 110,110,108 strength 0.45
### Render
- better
### Next
- stay on holdout_0

## set_01 | holdout_0 | tick 2
### Gaps
- Far-left is still too light versus the Navcam edge
- Second bright cobble still missing above the first
- Left field still too even
### Edits
- paint 1852.80,-434.56 radius 3.5 rgb 100,100,98 strength 0.40
- bump 1850.11,-439.31 radius 1.3 height 0.30
- paint 1850.11,-439.31 radius 1.3 rgb 210,208,200 strength 0.55
### Render
- worse; reverted
### Next
- stay on holdout_0

## set_01 | holdout_0 | tick 3
### Gaps
- Far bright rock cluster still missing from this stereo view
- Mid-left still lacks a dark pebble
- Extra mid-distance relief still weak
### Edits
- paint 1850.58,-440.90 radius 2.0 rgb 190,188,180 strength 0.40
- bump 1850.58,-440.90 radius 1.5 height 0.20
- paint 1849.82,-436.70 radius 1.0 rgb 120,120,118 strength 0.40
### Render
- better
### Next
- gap pass

## gap pass
### Gaps
- coverage.png is ~82% black: Navcam stereo only sees a small ESE wedge
- gap_north/east/south/west show flat HiRISE with no continuation relief away from the rover
- Unsurveyed ground is the satellite crop with no game-world mounds
### Edits
- bump 1849.08,-412.02 radius 7.0 height 0.70 inferred
- bump 1824.08,-437.02 radius 6.0 height 0.55 inferred
- bump 1849.08,-462.02 radius 6.0 height 0.60 inferred
- bump 1874.08,-417.02 radius 5.0 height 0.45 inferred
- paint 1827.08,-419.02 radius 6.0 rgb 78,78,76 strength 0.45 inferred
- paint 1831.08,-459.02 radius 6.0 rgb 82,82,80 strength 0.40 inferred
### Render
- check passed: inferred ops are only on low-coverage ground
- gap_north rewritten by vision_loop gap
### Next
- done; the bubble is ready to compare with the projection loop
