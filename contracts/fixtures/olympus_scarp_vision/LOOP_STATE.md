# Vision loop state

The judge is the render PNG next to the reference photo. model.json is wrong when the PNG is wrong.

## set_01 | 01_scarp | tick 1
### Gaps
- The right-hand render matches the reference on every pixel. Same pale orange dust, same dark rust band across the upper face, same layer lines.
- There is no missing mound and no flat patch where the reference shows a rock.
- A new bump would move a layer that is already the HiRISE scarp.
### Edits
- none
### Render
- same. Mean absolute difference is 0. Mean colour is 242, 232, 194.
### Next
- advance to set_02 04_overview, one render only. Another scarp tick would only fuss a match.

## set_02 | 04_overview | tick 1
### Gaps
- Check that the orange satellite picture is intact. No new shapes from this nadir frame.
- A thin sliver on the left edge is black in the reference and orange in the render. It is the silhouette, not a grey wash and not a slid layer.
### Edits
- none
### Render
- same. Magenta corners match. Terrain mean colour is 237, 225, 192 against the reference 236, 224, 192. The diagonal layers stay put.
### Next
- gap pass

## gap pass
### Gaps
- coverage.png is 14.9% black and 85.0% gray. White is 981 texels, one patch at x 356 to 454, y -762 to -688, where the scarp camera meets the nadir.
- The side views are already one pale orange scarp. The east and south cameras sit inside the cliff, so those frames are thin silhouettes. That is the camera, not a hole in the ground.
- Gray and black layer lips away from the white patch are still the smooth step in the elevation model.
### Edits
- inferred bump -230, 818 radius 5.5 height 1.05
- inferred bump 930, 1032 radius 5.0 height 0.95
- inferred bump 484, 910 radius 5.5 height 0.9
- inferred bump 718, -818 radius 5.0 height 0.85
- inferred bump 770, 410 radius 4.5 height 0.8
- inferred bump -292, 14 radius 5.0 height 0.75
### Render
- check passed: inferred ops are only on low-coverage ground.
- 01_scarp is still an exact match (mean absolute difference 0). The photographed layers did not move.
- gap_north is not a viewpoints camera, so the side views were refreshed with the gap command. The skyline is still one tan surface. No tears and no gray paint.
### Next
- enhance pass

## enhance | 01_scarp | tick 1
### Gaps
- The zoomed scarp is a smooth orange wash. The Mastcam frame on the left is the kind of grain to grow. These are not the same rocks.
- The dark band is the layer. The first three disks sat east of the pixels that band actually draws.
### Edits
- paint 386.0, -736.0 radius 1.5 strength 0.34 rgb 141, 112, 86
- paint 390.0, -732.0 radius 1.4 strength 0.32 rgb 102, 80, 61
- paint 384.0, -731.0 radius 1.6 strength 0.30 rgb 171, 135, 105
### Render
- worse. 81 pixels changed, all in a faint smudge at the top edge. Two of the disks did not appear. Mean colour stayed 227, 201, 173.
### Next
- revert the tick and stay on 01_scarp

## enhance | 01_scarp | tick 2
### Gaps
- The same dark band, now using the world points the close camera actually draws, around x 380.
- The grains stay on that line. They do not start a second stripe.
### Edits
- paint 380.9, -736.3 radius 1.4 strength 0.34 rgb 112, 87, 67
- paint 380.3, -732.2 radius 1.3 strength 0.34 rgb 166, 131, 103
- paint 379.8, -728.9 radius 1.4 strength 0.32 rgb 146, 115, 90
### Render
- better. 3885 pixels change, and the difference follows the dark band. The strongest pixel moves by 55 levels summed across the channels. Mean colour stays 227, 201, 173. No gray pixels.
### Next
- stay on 01_scarp. The pale benches above the band are still a smooth wash.

## enhance | 01_scarp | tick 3
### Gaps
- Pale dust on the upper benches, and one disk on the bright foreground.
- The foreground disk faces the camera, so a 1.3 m radius covers a wide block.
### Edits
- paint 399.5, -731.8 radius 1.5 strength 0.28 rgb 226, 197, 159
- paint 397.6, -725.6 radius 1.4 strength 0.26 rgb 206, 165, 130
- paint 375.6, -734.7 radius 1.3 strength 0.30 rgb 189, 155, 124
### Render
- worse. 12739 pixels change. Two spots on the upper benches are small. The foreground disk is a flat rust block on the bright dust.
### Next
- revert the tick and stay on 01_scarp

## enhance | 01_scarp | tick 4
### Gaps
- The upper benches still want a few grains. Keep them off the bright foreground and off the dark band already painted.
### Edits
- paint 399.5, -731.8 radius 1.2 strength 0.28 rgb 226, 197, 159
- paint 397.6, -725.6 radius 1.2 strength 0.26 rgb 206, 165, 130
- paint 403.6, -723.4 radius 1.1 strength 0.24 rgb 220, 176, 138
### Render
- better. 1979 pixels change, all in the upper-left layers, as three separate spots. The strongest move is 31 levels summed across the channels. Mean colour goes from 226.6, 201.1, 172.5 to 226.5, 201.0, 172.5. Every changed pixel stays orange. The 01_scarp photo changes by 125 pixels and its mean colour stays 242, 232, 194. The dark band does not move sideways.
### Next
- stop. Four enhance ticks is enough. Another tick would only fuss the same benches.

## Ready
The bubble is ready to compare with the projection loop.
