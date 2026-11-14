# Vision Refine Loop Plan

Parallel branch `cursor/vision-refine-loop-b90d`, forked from the photo-projection implementation. The projection loop stays in `pipeline/bubble` and is not the editor for this pass.

**Goal:** Give a fresh Cursor agent the same observe → edit → render → compare cycle used on the interceptor, aimed at one Mars bubble. The agent edits terrain and texture. A person then compares that mesh with the projection loop on the same bundle.

**Architecture:** Photos are grouped into sets of at most four that share a place and a facing direction. The editable model is `model.json`, a list of small bump, paint, and smooth ops compiled onto a copy of the baseline height and satellite texture. `vision_loop render` writes a side-by-side PNG. The agent is the vision model. No separate model API token is read.

**Tech stack:** The existing numpy rasterizer, glTF writer, and bundle loader. Pillow and pytest.

## Global constraints

- A set is one place and one facing. Cameras more than 40 m apart, or facing more than 35° off the set's first camera, are different sets. Opposite views never share a set. At most 4 photos per set.
- Inside a set, one photo at a time, in `focus_order`.
- At most 3 ops per tick. `radius_m` ≤ 8. `height_m` within ±1.5. `strength` in 0..1.
- Photo-pass ops set `inferred` false. Gap-pass ops set `inferred` true and may only be centered where fewer than two photos see the ground.
- Gap-pass terrain is a plausible continuation of the neighbors. It is not a surveyed Mars surface.
- Do not edit `pipeline/bubble`.
- Compiled mesh is glTF binary (`.glb`), Y-up, east +X, north −Z.
- The text file the agent edits is `model.json`, not the glTF.

## Commands

```bash
PYTHONPATH=pipeline python3 -m vision_loop init --bundle <bundle> --work <work>
PYTHONPATH=pipeline python3 -m vision_loop render --work <work> --camera <name>
PYTHONPATH=pipeline python3 -m vision_loop revert --work <work>
PYTHONPATH=pipeline python3 -m vision_loop gap --work <work>
PYTHONPATH=pipeline python3 -m vision_loop check --work <work>
```

The protocol the agent follows is `pipeline/vision_loop/LOOP.md`. The prompt to paste into a fresh Cursor agent is `pipeline/vision_loop/AGENT_PROMPT.md`.

## Comparison run

Both loops read `contracts/fixtures/bubble_source`. Projection output is `contracts/fixtures/bubble/compare/`. Vision output is `data/vision/fixture_mound/compare/`. Same reference photos, different meshes. The vision mesh may differ in height. The projection mesh may not.

## Out of scope

- Downloading HiRISE or rover frames.
- The Cesium globe and the teammate tile pyramid.
- Training a network. The Cursor agent looking at PNGs is the vision model.
