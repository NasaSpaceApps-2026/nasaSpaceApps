# Vision loop state

The judge is the render PNG next to the reference photo. model.json is wrong when the PNG is wrong.

## set_01 | satellite | tick 1
### Gaps
- Western fan finger ridges are smooth bright stripes. The 2 m grid has no rock-scale crest on them.
- The bright lobe's scarp lip is a soft edge rather than a rocky lip.
- Plains crater rims (northeast and south) are faint rings without a raised rim.
### Edits
- bump 790.8, 1974.4 radius 6.5 height 1.35
- bump 834.8, 1998.4 radius 6.0 height 1.2
- bump 650.8, 1932.4 radius 7.0 height 1.4
### Render
- same. Overhead orange dirt and the ridges still match the reference. The three knobs sit on bright finger-ridge crests. gap_north still reads as the same ground, not a torn mesh.
### Next
- stay on satellite

## set_01 | satellite | tick 2
### Gaps
- Scarp lip of the bright lobe is still a soft rim: west nose, middle of the lip, and the east tip.
- Plains crater rims are still unraised.
- Transverse ribs on the fan front are brightness bands on a ramp the 2 m grid smoothed.
### Edits
- bump 858.8, 1714.4 radius 6.5 height 1.4
- bump 934.8, 1720.4 radius 6.0 height 1.25
- bump 1110.8, 1698.4 radius 6.0 height 1.3
### Render
- same. The orange dirt pattern and the lobe ridges still match. gap_north is the same ground, with no tears.
### Next
- stay on satellite

## set_01 | satellite | tick 3
### Gaps
- Northeast crater rims and the small southern crater are bright rings with almost no raised lip in the 2 m grid.
- Fan-front ribs are still only brightness bands.
### Edits
- bump 1196.8, 2248.4 radius 5.0 height 1.2
- bump 1432.8, 2056.4 radius 5.5 height 1.35
- bump 1240.8, 1262.4 radius 5.0 height 1.15
### Render
- same. One pixel shifts by 3 levels; the orange dirt and the crater rings still match. gap_north is unchanged and still reads as ground.
### Next
- stay on satellite

## set_01 | satellite | tick 4
### Gaps
- Fan-front ribs still show as bright lines on a smooth ramp. Three crests in that band have no rock-scale relief.
- Further knobs on the same ridges would only repeat these disks.
### Edits
- bump 1120.8, 1518.4 radius 5.5 height 1.2
- bump 1084.8, 1518.4 radius 5.0 height 1.15
- bump 1152.8, 1526.4 radius 5.0 height 1.25
### Render
- same. The ribbed fan front and the orange dirt still match the reference. Max channel change is 3 on one pixel.
### Next
- gap pass. Another satellite tick would only fuss.

## gap pass
### Gaps
- coverage.png is gray on every texel. The nadir photo sees the ground once, so none of it is two-view. low_fraction is 1.0.
- Finger crests, the rest of the scarp lip, one more rib, and the south crater's other rim were still the smooth 2 m grid.
### Edits
- inferred bump 540.8, 2040.4 radius 6.0 height 1.25
- inferred bump 730.8, 1792.4 radius 5.5 height 1.15
- inferred bump 572.8, 1692.4 radius 6.0 height 1.2
- inferred bump 1070.8, 1676.4 radius 6.0 height 1.3
- inferred bump 1186.8, 1556.4 radius 5.0 height 1.15
- inferred bump 1214.8, 1262.4 radius 5.0 height 1.1
### Render
- check passed: inferred ops are only on low-coverage ground.
- Satellite compare still matches. Max channel change versus the reference is 3. The orange dirt pattern and the ridges are intact. No paint.
- gap_north still shows the fan as ground: tan slope, ridge skyline, no tears. The 1 m knobs sit on the crests; at this camera they do not move the picture.
### Next
- The bubble is ready to compare with the projection loop.
