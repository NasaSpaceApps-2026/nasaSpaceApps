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
