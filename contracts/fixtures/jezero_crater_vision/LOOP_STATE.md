# Vision loop state

The judge is the render PNG next to the reference photo. model.json is wrong when the PNG is wrong.

## set_01 | 01_whoops | tick 1
### Gaps
- The bright mound's crest is a smooth 2 m ridge. Three local highs sit on it and the photo shows that crest just under the dark far lip.
- The far lip itself is a colour break, not a place to start a new ridge.
- The sand below the crest already matches the reference.
### Edits
- bump 590.8, 1448.4 radius 5.0 height 1.0
- bump 588.8, 1452.4 radius 4.5 height 0.85
- bump 596.8, 1444.4 radius 4.5 height 0.75
### Render
- better. The crest reads a little higher and stays on the same line. The dark lip at the top of the frame did not move. Mean change under the crest is about 4 levels; the lower sand is unchanged.
### Next
- stay on 01_whoops

## set_01 | 01_whoops | tick 2
### Gaps
- The crest still fades before the dark far lip and again on the near side of the same ridge.
- Those stretches are the same ridge line, not a new stripe.
- Another pass on the three highs from tick 1 would only stack disks.
### Edits
- bump 586.8, 1464.4 radius 4.5 height 0.7
- bump 582.8, 1472.4 radius 4.0 height 0.55
- bump 596.8, 1438.4 radius 4.0 height 0.5
### Render
- better. The crest now reaches toward the dark lip and a short way down the near side. The lip stays on the same pixels. The sand in the lower half of the frame is unchanged.
### Next
- advance to set_02 02_scarp. Another whoops tick would only fuss the same crest.

## set_02 | 02_scarp | tick 1
### Gaps
- The lit lip is a smooth ramp. The brightest nose is the west end of that lip, and the top of the frame is the rest of the lip.
- One brighter speck sits on the right of the face, a rock rather than a new ridge.
- The black face below is the steep slope. It should stay dark. The magenta corner is outside the camera crop and is not a paint target.
### Edits
- bump 486.8, 1224.4 radius 5.5 height 1.05
- bump 500.8, 1282.4 radius 5.0 height 0.85
- bump 524.8, 1278.4 radius 3.5 height 0.75
### Render
- better. The lip rises in the frame and stays on the same horizontal line (best match is 0 px sideways). The dark face below row 80 is unchanged. Magenta corner is unchanged.
### Next
- stay on 02_scarp

## set_02 | 02_scarp | tick 2
### Gaps
- A rock on the face sits just under the west nose.
- Two bright specks along the lip are still the smooth 2 m grid.
- More height on the nose itself would only stack on tick 1.
### Edits
- bump 488.8, 1218.4 radius 3.2 height 0.6
- bump 514.8, 1294.4 radius 4.5 height 0.7
- bump 506.8, 1276.4 radius 3.5 height 0.55
### Render
- better. The lip is still unshifted sideways. The lower face stays black. No gray paint.
### Next
- advance to set_03 03_crater. Another scarp tick would only fuss the same lip.

## set_03 | 03_crater | tick 1
### Gaps
- A short bright rim crosses the upper frame. The pit floor just north of it is darker and lower.
- The first disks were tall enough to thin that bright line.
### Edits
- bump 1190.8, 1804.4 radius 3.0 height 0.65
- bump 1190.8, 1810.4 radius 3.5 height 0.9
- bump 1188.8, 1816.4 radius 3.0 height 0.55
### Render
- worse. The bright rim moved up and lost pixels, so the line looked thinner. The floor stayed dark, but the rim itself was the thing the photo shows.
### Next
- revert the tick and stay on 03_crater

## set_03 | 03_crater | tick 2
### Gaps
- The same bright rim, raised only slightly so the bright line stays on the crest.
- Do not raise the dark floor.
### Edits
- bump 1190.8, 1810.4 radius 3.0 height 0.4
- bump 1190.8, 1806.4 radius 2.8 height 0.35
### Render
- better. The rim centroid moves 0.2 px sideways and 1.4 px up, and the bright line gains pixels. The dark floor and the left shadow are unchanged.
### Next
- advance to set_04 04_overview, one render only. Another crater tick would only fuss the same rim.

## set_04 | 04_overview | tick 1
### Gaps
- Check that the close-up bumps did not wipe the orange satellite picture. No new shapes from this nadir frame.
### Edits
- none
### Render
- same. Six pixels differ, one of them by 6 levels. Mean colour stays 148, 122, 101. Magenta corners match. The ridges and the orange dirt are intact.
### Next
- gap pass

## gap pass
### Gaps
- coverage.png has no black. 99.75% of texels are gray (one photo, the nadir) and 0.25% are white where a close camera overlaps that nadir.
- The side views already read as continuous tan ground. Gray crests away from those white patches are still the smooth 2 m grid.
- Inferred ops stay off the white patches.
### Edits
- inferred bump 1212.8, 1750.4 radius 6.0 height 1.05
- inferred bump 1514.8, 2412.4 radius 5.5 height 0.95
- inferred bump 974.8, 1974.4 radius 5.5 height 0.9
- inferred bump 316.8, 2334.4 radius 5.0 height 0.85
- inferred bump 394.8, 1070.4 radius 5.0 height 0.8
- inferred bump 1510.8, 2252.4 radius 4.5 height 0.75
### Render
- check passed: inferred ops are only on low-coverage ground.
- gap_north is not a viewpoints camera, so the side views were refreshed with the gap command. The skyline is still one tan surface. No tears and no gray paint.
### Next
- enhance pass

## Next
- enhance, tick 1 on 01_whoops

## enhance | 01_whoops | tick 1
### Gaps
- The zoomed sand is a smooth orange wash. The Mastcam frame on the left is the kind of grain to grow. These are not the same rocks.
- The light bands are the whoop crests. They stay on this line.
- Three tan grains fit on the near face of that crest.
### Edits
- paint 582.8, 1468.4 radius 1.6 strength 0.32 rgb 165, 133, 106
- paint 584.8, 1466.4 radius 1.5 strength 0.28 rgb 181, 147, 118
- paint 586.8, 1470.4 radius 1.7 strength 0.3 rgb 207, 167, 132
### Render
- better. 6954 pixels take the tan. The strongest pixel moves by 89 levels summed across the channels. Mean colour goes from 225, 200, 167 to 224, 199, 166. The brightest row stays the near sand.
### Next
- 02_scarp

## enhance | 02_scarp | tick 1
### Gaps
- The lit corner is dark brown. The black face under it stays black.
- The grain on that corner is rust and dark sand.
### Edits
- paint 496.8, 1260.4 radius 1.5 strength 0.32 rgb 91, 71, 56
- paint 498.8, 1256.4 radius 1.4 strength 0.3 rgb 67, 53, 41
- paint 500.8, 1254.4 radius 1.4 strength 0.28 rgb 52, 41, 32
### Render
- better. 3488 pixels on the lit corner change. The strongest move is 31 levels summed across the channels. Mean colour stays 13, 10, 8. The whoops frame is unchanged.
### Next
- 01_whoops

## enhance | 01_whoops | tick 2
### Gaps
- The first grains sit on the lower crest. The sand just north of them is still the smooth wash.
- The new disks stay off those three centers.
### Edits
- paint 584.8, 1472.4 radius 1.4 strength 0.26 rgb 165, 133, 106
- paint 590.8, 1472.4 radius 1.5 strength 0.24 rgb 181, 147, 118
- paint 584.8, 1474.4 radius 1.3 strength 0.22 rgb 207, 167, 132
### Render
- better. 3556 pixels change. The strongest move is 54 levels summed across the channels. Mean colour stays 224, 199, 166. The brightest row is unchanged.
### Next
- one more tick on 01_whoops. The sand still reads soft and the ridges are intact.

## enhance | 01_whoops | tick 3
### Gaps
- Open sand on the east side of the crest, off the disks already there.
- The colours are dark sand from that ground.
### Edits
- paint 590.8, 1466.4 radius 1.0 strength 0.35 rgb 127, 104, 85
- paint 592.8, 1478.4 radius 0.9 strength 0.35 rgb 132, 106, 85
- paint 588.8, 1476.4 radius 1.1 strength 0.34 rgb 146, 118, 94
### Render
- better. 2445 pixels change. The strongest move is 53 levels summed across the channels. Mean colour moves from 224.3, 198.7, 165.9 to 224.0, 198.5, 165.7. Height is unchanged and the brightest row stays put. The scarp frame is unchanged.
### Next
- stop

## Ready
The bubble is ready to compare with the projection loop.
