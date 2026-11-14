You are running the Mars bubble vision refine loop on this repo. You are the vision model. You look at rendered PNGs and edit the text model. You do not call a separate vision API, and you do not use an agent token.

Read `pipeline/vision_loop/LOOP.md` and follow it exactly. Also read `docs/superpowers/plans/2026-11-14-vision-refine-loop.md` for the caps. Do not edit `pipeline/bubble`. That package is the other refine method.

The comparison bundle is `contracts/fixtures/bubble_source`. If `data/vision/fixture_mound/model.json` does not exist, create the workspace:

```bash
PYTHONPATH=pipeline python3 -m vision_loop init \
  --bundle contracts/fixtures/bubble_source \
  --work data/vision/fixture_mound
```

Then work `data/vision/fixture_mound`.

Rules:

- Read `sets.json`. Do set_01 completely before set_02. Inside a set, follow `focus_order` one image at a time.
- A tick looks at `compare/<camera>.png` (reference on the left, render on the right). List at most 3 gaps. Append at most 3 ops to `model.json`. Each op is one `bump`, `paint`, or `smooth` inside the caps in LOOP.md. Photo-pass ops have `"inferred": false`.
- Render with `PYTHONPATH=pipeline python3 -m vision_loop render --work data/vision/fixture_mound --camera <name>`.
- If the new PNG is worse, revert each op from that tick with `PYTHONPATH=pipeline python3 -m vision_loop revert --work data/vision/fixture_mound` and render again.
- Log the tick in `LOOP_STATE.md` using the template in LOOP.md.
- Leave an image when it is close to the reference or after 8 ticks. Then the next image. Then the next set.
- After the last set, run the gap pass in LOOP.md. Inferred ops have `"inferred": true`, at most 6, only on ground that `coverage.png` shows as black or gray. Then run `vision_loop check` and render `gap_north`.
- Stop when the gap pass is logged. Do not download imagery. Do not edit the baseline files or the reference photos.

Terrain and texture may both change. This mesh is a game-world bubble. Invented ground in the gap pass only has to look like a likely continuation of the ground beside it.
