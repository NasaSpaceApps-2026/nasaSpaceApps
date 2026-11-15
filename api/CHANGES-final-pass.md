# Autopilot final pass (2026-11-15, Sameer, approved edits in api/ + web/src/rover/link.ts)

Revert any item by reverting the named hunks (the repo has no commit for this pass; `git diff api/ web/src/rover/link.ts`).

1. **Interpolated (filled) terrain policy** (`planner.py`, `rover.py`, `main.py`, `terrain.py`)
   - Why: FSD stopped with `blocked_hazard` on filled cells while `/route` planned through them silently.
   - `/route` takes `filled: block | caution | allow` (default `caution` = passable, cost penalty `cost_weights.unknown`,
     warning with count and length). Response adds `filled_policy`, `filled_cells`, `filled_length_m`.
   - FSD policy gets `filled` (default `caution`): half speed (`caution_speed_factor`) and an event on entry; `block`
     keeps the old stop; `allow` is silent. Command results add `filled_crossed_m`. Replans and `goto` use the policy.
   - No-data cells (`Terrain.nodata`) are always impassable (planner and sim; before, NaN slope slipped past `slope > limit`).
   - Perf: the sim rebuilt `filled.astype(uint8)` every 1 m sub-step; now cached.
   - Revert: restore old `_advance` filled check (`cmd.fsd and filled`), drop the `filled` args.
2. **/route default profile** = active vehicle (`Planner.default_vehicle`, set by `RoverSim`). Was `astronaut_walk`.
   Two existing tests that relied on the old default now pass `profile` explicitly. Revert: `profile or self.default_vehicle` -> `profile`.
3. **caution_slope_deg** added to `/rover/vehicles`, `/rover/state` (with `nogo_slope_deg`) and `/fsd/status`. `/site.profiles` already had it.
4. **Browser takeover bug**: `RoverSim.manual_pose` ignores a pose while a command runs unless it carries
   `takeover: true`. `web/src/rover/link.ts` sets that flag on poses sent while the last server mode was auto/fsd
   (the driver only does that after a key press, `takeover()`); periodic manual poses carry no flag. Pose with no
   command running is adopted as before. `RoverSim.start` now broadcasts `rover.state` immediately. Contract: `rover.pose`
   gains optional `takeover`.
5. Tests: `api/tests/test_final_pass.py`, `filled_client` fixture in `conftest.py`. SCIENCE_VALIDITY.md: one row added.

6. **Rover snap-back after an API/agent drive** (`rover.py` `manual_pose`, `_finish`, `teleport`; `web/src/rover/link.ts`)
   - Why: the browser keeps reporting its last manual pose; once the command ended (or after a teleport) `manual_pose`
     adopted it and the rover jumped back (docs/buggy-driving.md section 6).
   - API: after a command ends or a teleport, `_await_ack` is set. An unflagged pose more than `STALE_POSE_M` (2 m) from the
     server pose is ignored, unless it keeps changing (a far pose that moves >5 cm between reports is real driving, so
     legacy viewers still work). A pose within 2 m clears the guard; a `takeover: true` pose always applies and clears it.
   - link.ts: `noteServerPose` records every server pose; on auto/fsd -> idle or a >5 m jump while not auto, `sendPose`
     drops poses farther than 2 m from the server pose for up to 3 s (until the driver adopts it). Takeover flag logic unchanged.
   - Revert: remove `_await_ack`/`_stale_seen` and the guard block in `manual_pose`; remove `noteServerPose`, `serverPose`,
     `staleUntil` and the guard in `RoverLink.sendPose`.
7. **POST /view `mode: "planet"`** (`main.py` `ViewReq`/`set_view`): `lat` (-90..90), `lon` (east, deg), optional `height_m`, `label`.
   Emits `view.camera` as flat fields AND a `payload` object with the same fields (`{"type":"view.camera","mode":"planet",
   "lat":..,"payload":{"mode":"planet","lat":..,"lon":..,"height_m":..,"label":..}}`), matching the AtlasDock handler
   (reads `payload` else flat). Missing lat/lon -> 422. Other modes unchanged. Contract regenerated
   (`contracts/api.openapi.yaml`). Revert: drop "planet" from the Literal, the four fields and the `if req.mode == "planet"` block.
8. Tests: `test_final_pass.py` (stale pose ignored, ack resumes manual, takeover applies, moving far pose accepted, planet view).
   No safety number changed; SCIENCE_VALIDITY.md untouched.
