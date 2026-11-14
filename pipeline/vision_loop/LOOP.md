# Mars bubble vision loop

A Cursor agent is the vision model. It looks at PNGs, edits `model.json` with small terrain and texture changes, re-renders, and reverts a tick that looks worse. This is the same observe → edit → render → compare cycle as the interceptor studio loop. The photo-projection loop in `pipeline/bubble` is a different branch of the work. Do not edit it.

The result is a game-world bubble: a mesh you can drive a rover through, not a street-view photo sphere. Surveyed photos guide the edits where several views overlap. Where the photos do not cover a surface from two angles, a later pass may invent plausible ground. That invented ground is labeled `inferred` and is not a claim about Mars.

## Workspace

From the repo root, once per bubble:

```bash
PYTHONPATH=pipeline python3 -m vision_loop init \
  --bundle contracts/fixtures/bubble_source \
  --work data/vision/fixture_mound
```

Use the same `--bundle` the projection loop uses when the two results must be compared. `data/vision/` is gitignored.

| File | Role |
|---|---|
| `baseline/dem.npy`, `baseline/texture.png` | Frozen starting height and satellite picture. Do not edit these. |
| `model.json` | The only file the agent edits. An `ops` list compiled on top of the baseline. |
| `sets.json` | Photo sets. One set is one place seen from similar directions. |
| `refs/` | Reference photos. |
| `compare/<camera>.png` | Reference on the left, current render on the right. |
| `compare/gap_*.png` | Renders of the bubble from the four sides, used only in the gap pass. |
| `coverage.png` | Black = no photo, gray = one photo, white = two or more. |
| `mesh.glb` | Compiled mesh. Y-up, east +X, north −Z, origin at the bubble center. |
| `LOOP_STATE.md` | Tick log. |

## Sets

`sets.json` is already built. A set holds at most 4 photos. Two photos share a set only when both are true:

- The cameras are within 40 m of the set's first camera.
- Their facing directions are within 35 degrees of that first camera.

Photos that look opposite ways, or that sit on different parts of Mars, are different sets. A downward photo does not join a rover-eye set. Work the sets in order. Inside a set, work `focus_order` one image at a time. Finish an image before starting the next.

## One tick

1. Read the reference and the current compare PNG for the active image. The compare file is the judge. Do not argue from the JSON.
2. List at most 3 visual gaps, biggest first (missing mound, wrong color, flat ground where the photo shows a rock, a bump in the wrong place).
3. Append at most 3 ops to `model.json`. Each op is one small change:
   - `bump`: raise or lower a disk. `radius_m` ≤ 8, `height_m` between −1.5 and 1.5.
   - `paint`: blend a color into a disk. `strength` in 0..1, `rgb` three integers 0..255.
   - `smooth`: pull heights in a disk toward their neighbors. `strength` in 0..1.
   - Set `"inferred": false` on every op during the photo passes.
4. Render:

```bash
PYTHONPATH=pipeline python3 -m vision_loop render \
  --work data/vision/fixture_mound \
  --camera <active image name>
```

5. Look at the new compare PNG. If the active image looks worse, revert the whole tick and render again:

```bash
PYTHONPATH=pipeline python3 -m vision_loop revert --work data/vision/fixture_mound
PYTHONPATH=pipeline python3 -m vision_loop render --work data/vision/fixture_mound --camera <active image name>
```

`revert` removes one op. Run it once per op you added in the bad tick.

6. Append a section to `LOOP_STATE.md`:

```markdown
## set_01 | fit_0 | tick 2
### Gaps
- ...
### Edits
- bump x,y radius height
### Render
- better / worse / same
### Next
- stay on fit_0 / advance to the next image / next set / gap pass
```

## When to leave an image

Leave the image when a new tick would only be fussing, or after 8 ticks on that image. Then take the next name in `focus_order`. After the last image in the set, take the next set. Do not start a new image in the same tick you finish one.

## Gap pass

Run this once, after every photo set is finished.

```bash
PYTHONPATH=pipeline python3 -m vision_loop gap --work data/vision/fixture_mound
```

Read `coverage.png`, `gap_report.json`, and `compare/gap_north.png`, `gap_east.png`, `gap_south.png`, `gap_west.png`. Black and gray in `coverage.png` are places the photos do not show from two angles.

Add at most 6 ops whose centers are those places. Set `"inferred": true`. Match the neighboring color and the neighboring shape. This ground does not have to match Mars. It has to look like it could continue the ground next to it. Do not paint over white (two-view) texels.

Then:

```bash
PYTHONPATH=pipeline python3 -m vision_loop check --work data/vision/fixture_mound
PYTHONPATH=pipeline python3 -m vision_loop render --work data/vision/fixture_mound --camera gap_north
```

`check` fails if an inferred op is centered on ground that already has two photos. Revert those ops.

Stop after the gap pass, or after 6 inferred ops. Write a final `LOOP_STATE.md` section that says the bubble is ready to compare with the projection loop.

## Hard rules

- Edit `model.json` only. Do not edit `pipeline/bubble`, the baseline files, or the reference photos.
- Do not download imagery.
- One image per tick. At most 3 ops, each inside the radius and height caps.
- Terrain and texture are both allowed. Slope and hazard for driving stay on the published DEM, not on this mesh.
- If the PNG is worse, the tick is wrong. Revert it.
