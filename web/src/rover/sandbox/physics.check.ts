/**
 * Plain-assertion checks for the rover physics and height-tile sampler.
 * Run from the sandbox folder: npm run check (bundles with esbuild, runs with node).
 */

import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { inflateSync } from 'node:zlib';
import { DEG, poseToEulerYXZ, siteToThree } from '../coords';
import { TileHeightSampler, decodeHeightRGBA, type HeightSampler } from '../heightTiles';
import {
  BUGGY_PARAMS, DEFAULT_BUGGY_PROFILE, DEFAULT_ROVER_PROFILE, MARS_G, RoverPhysics, climbSpeed, footprint, type RoverProfile,
} from '../physics';

let passed = 0;
let failed = 0;
function check(name: string, fn: () => void) {
  const t = performance.now();
  try {
    fn();
  } catch (e) {
    failed++;
    console.log(`FAIL ${name}\n    ${(e as Error).message}`);
    return;
  }
  passed++;
  console.log(`ok  ${name} (${(performance.now() - t).toFixed(0)} ms)`);
}
const near = (a: number, b: number, tol: number, msg: string) => assert.ok(Math.abs(a - b) <= tol, `${msg}: ${a} vs ${b} (tol ${tol})`);
const FWD = { throttle: 1, steer: 0 };
const IDLE = { throttle: 0, steer: 0 };
const BRAKE = { throttle: 0, steer: 0, brake: true };
const flat: HeightSampler = { sampleHeight: () => -2400 };
const slope = (deg: number): HeightSampler => ({ sampleHeight: (_x, y) => y * Math.tan(deg * DEG) });
const buggy = (s: HeightSampler, start = { x: 0, y: 0, heading_deg: 0 }, prof: RoverProfile = DEFAULT_BUGGY_PROFILE) => new RoverPhysics(s, prof, BUGGY_PARAMS, start);
const allFinite = (r: RoverPhysics) => [r.state.x, r.state.y, r.state.z, r.state.pitch_deg, r.state.roll_deg, r.state.speed_mps].every(Number.isFinite);

// --- physics -----------------------------------------------------------------

check('flat ground: buggy settles at static sag m g / (4 k) with no jitter; reaches the cap; brakes stop it', () => {
  const r = buggy(flat);
  const sc = r.suspension;
  near(sc.static_sag_m, (DEFAULT_BUGGY_PROFILE.mass_kg * MARS_G) / (4 * sc.k_n_per_m), 1e-12, 'sag formula');
  // Rear-biased centre of mass: each axle carries weight in proportion to the CG's distance from the other axle.
  const L = r.params.wheelbase_m, d = r.params.cg_forward_m;
  r.state.wheels.forEach((w, i) => {
    const axleShare = i < 2 ? (L / 2 + d) / L : (L / 2 - d) / L;
    const load = (DEFAULT_BUGGY_PROFILE.mass_kg * MARS_G * axleShare) / 2;
    assert.ok(w.in_contact, 'all wheels down');
    near(w.load_n, load, 8, `static wheel load ${i}`);
    near(w.compression_m, load / sc.k_n_per_m, 2e-3, `static compression ${i}`);
  });
  assert.ok(r.state.wheels[2].load_n > r.state.wheels[0].load_n * 1.4, 'rear-biased weight');
  near(r.state.z, -2400, 2e-3, 'body origin on the ground at ride height');
  // Rear squat: the heavier rear compresses more, so the body rests slightly nose-up.
  const L2 = r.params.wheelbase_m;
  const squat = Math.atan((r.state.wheels[2].compression_m - r.state.wheels[0].compression_m) / L2) / DEG;
  near(r.state.pitch_deg, squat, 0.1, 'static rear squat');
  const z0 = r.state.z, p0 = r.state.pitch_deg, x0 = r.state.x, y0 = r.state.y;
  let jitter = 0;
  for (let i = 0; i < 240; i++) {
    const s = r.step(1 / 60, IDLE);
    jitter = Math.max(jitter, Math.abs(s.z - z0), Math.abs(s.pitch_deg - p0), Math.abs(s.roll_deg), Math.hypot(s.x - x0, s.y - y0));
  }
  assert.ok(jitter < 1e-3, `at rest on flat ground: max drift ${jitter}`);
  for (let i = 0; i < 60 * 25; i++) r.step(1 / 60, FWD);
  near(r.state.speed_mps, DEFAULT_BUGGY_PROFILE.flat_speed_mps, 0.05, 'flat speed cap');
  assert.equal(r.state.status, 'driving');
  let t = 0;
  while (r.state.speed_mps > 0 && t < 20) { r.step(1 / 60, BRAKE); t += 1 / 60; }
  // Locked wheels on all four tyres: deceleration mu g = 2.2 m/s^2, so 12 m/s stops in ~5.4 s on Mars.
  near(t, DEFAULT_BUGGY_PROFILE.flat_speed_mps / (DEFAULT_BUGGY_PROFILE.traction_mu * MARS_G), 0.4, 'braking time = v / (mu g)');
  console.log(`    k ${sc.k_n_per_m.toFixed(0)} N/m, c ${sc.c_ns_per_m.toFixed(0)} N s/m, sag ${(sc.static_sag_m * 100).toFixed(1)} cm, inertia ${sc.inertia_kg_m2.map((v) => v.toFixed(0)).join('/')} kg m^2; stop from 12 m/s in ${t.toFixed(2)} s`);
});

check('scout on flat ground: skid-steers, holds the speed cap, stops with auto brake', () => {
  const r = new RoverPhysics(flat, DEFAULT_ROVER_PROFILE, {}, { x: 0, y: 0, heading_deg: 30 });
  for (let i = 0; i < 300; i++) {
    const s = r.step(1 / 60, { throttle: 1, steer: i < 150 ? 0.5 : 0 });
    near(s.z, -2400, 0.01, 'on the ground');
    assert.ok(Math.abs(s.pitch_deg) < 0.5 && Math.abs(s.roll_deg) < 0.5, `level ${s.pitch_deg} ${s.roll_deg}`);
  }
  assert.ok(r.state.odometer_m > 5, `rover should have moved, odometer ${r.state.odometer_m}`);
  assert.ok(Math.abs(r.state.heading_deg - 30) > 20, `turned, heading ${r.state.heading_deg}`);
  near(r.state.speed_mps, DEFAULT_ROVER_PROFILE.flat_speed_mps, 1e-3, 'flat speed cap');
  for (let i = 0; i < 120; i++) r.step(1 / 60, IDLE);
  assert.ok(Math.abs(r.state.speed_mps) < 1e-4, `stopped ${r.state.speed_mps}`);
  // In place: steer only.
  const h0 = r.state.heading_deg;
  for (let i = 0; i < 60; i++) r.step(1 / 60, { throttle: 0, steer: 1 });
  assert.ok(Math.abs(r.state.heading_deg - h0) > 20, 'turns in place');
  // Time-lapse x16 (3200x): stays stable on the ground.
  r.setTimeScale(16);
  for (let i = 0; i < 300; i++) r.step(1 / 60, { throttle: 1, steer: i % 120 < 60 ? 0.3 : 0 });
  assert.ok(allFinite(r) && Math.abs(r.state.z + 2400) < 0.02 && Math.abs(r.state.pitch_deg) < 1, `time-lapse stable: z ${r.state.z} pitch ${r.state.pitch_deg}`);
});

check('12 deg plane: body pitch/roll follow the slope; climb speed = P / (m g (sin + Crr cos)), slower than flat', () => {
  const g = Math.tan(12 * DEG); // z rises northward at 12 deg
  const plane: HeightSampler = { sampleHeight: (_x, y) => g * y };
  const north = buggy(plane);
  for (let i = 0; i < 60 * 90; i++) north.step(1 / 60, FWD);
  // The body sits ~1 deg steeper than the ground: load transfer compresses the downhill springs (4 k b^2 roll/pitch stiffness).
  near(north.state.pitch_deg, 12, 2, 'pitch heading north');
  near(north.state.roll_deg, 0, 0.1, 'roll heading north');
  near(north.state.z, plane.sampleHeight(north.state.x, north.state.y), 0.1, 'z on plane');
  const v = climbSpeed(DEFAULT_BUGGY_PROFILE, 12);
  near(north.state.speed_mps, v, 0.03 * v, 'steady climb speed');
  const south = buggy(plane, { x: 0, y: 0, heading_deg: 180 });
  near(south.state.pitch_deg, -12, 2, 'pitch heading south (downhill)');
  const east = buggy(plane, { x: 0, y: 0, heading_deg: 90 });
  near(east.state.pitch_deg, 0, 1, 'pitch heading east (rear squat ~0.8 deg)');
  near(east.state.roll_deg, 12, 2, 'roll heading east (right side = south = lower)');
  console.log(`    20 deg climb ${north.state.speed_mps.toFixed(2)} m/s (model ${v.toFixed(2)})`);
});

check('no hard blocks: 30 deg ramp past the grip limit makes the scout slip, not freeze, and it can back away', () => {
  const t30 = Math.tan(30 * DEG);
  const ramp: HeightSampler = { sampleHeight: (_x, y) => (y > 10 ? (y - 10) * t30 : 0) };
  const r = new RoverPhysics(ramp, DEFAULT_ROVER_PROFILE, {}, { x: 0, y: 0, heading_deg: 0 });
  let maxY = 0;
  for (let i = 0; i < 1800; i++) maxY = Math.max(maxY, r.step(1 / 60, FWD).y);
  assert.ok(maxY + r.params.wheelbase_m / 2 > 9.8, `front wheels reached the ramp, y=${maxY}`);
  assert.ok(r.state.y < 13, `cannot climb 30 deg (grip ~27.5 deg with rolling resistance), y=${r.state.y}`);
  assert.ok(['slipping', 'sliding', 'steep', 'caution'].includes(r.state.status), `status ${r.state.status}`);
  for (let i = 0; i < 600; i++) r.step(1 / 60, { throttle: -1, steer: 0 });
  assert.ok(r.state.y < 9, `backed away, y=${r.state.y}`);
});

check('buggy drives into a 2 m deep, 22 deg dip and climbs back out', () => {
  // Bowl centred at y = 30, radius 10 m; walls up to ~22 deg.
  const dip: HeightSampler = {
    sampleHeight: (x, y) => {
      const d = Math.hypot(x, y - 30);
      return d < 10 ? -2 * (1 + Math.cos(Math.PI * d / 10)) / 2 : 0;
    },
  };
  const r = buggy(dip);
  let minZ = 0;
  for (let i = 0; i < 60 * 30 && r.state.y < 50; i++) minZ = Math.min(minZ, r.step(1 / 60, FWD).z);
  assert.ok(minZ < -1.5, `went down into the dip, min z ${minZ}`);
  assert.ok(r.state.y >= 50, `came out the far side, y=${r.state.y}`);
  assert.ok(Math.abs(r.state.roll_deg) < 10, `still upright, roll ${r.state.roll_deg}`);
});

check('brakes: on 35 deg they cannot hold (slides); on 20 deg they hold with no creep', () => {
  const steep = buggy(slope(35));
  const y0 = steep.state.y;
  for (let i = 0; i < 120; i++) steep.step(1 / 60, BRAKE);
  assert.ok(steep.state.y < y0 - 0.5 && steep.state.status === 'sliding', `slid down, y=${steep.state.y} ${steep.state.status}`);
  const mild = buggy(slope(20));
  const m0 = mild.state.y;
  for (let i = 0; i < 60; i++) mild.step(1 / 60, BRAKE);
  const m1 = mild.state.y;
  let jitter = 0;
  const z1 = mild.state.z, p1 = mild.state.pitch_deg;
  for (let i = 0; i < 240; i++) {
    const s = mild.step(1 / 60, BRAKE);
    jitter = Math.max(jitter, Math.abs(s.z - z1), Math.abs(s.pitch_deg - p1) * 0.01);
  }
  near(m1, m0, 1e-3, 'held by brakes');
  near(mild.state.y, m1, 1e-4, 'no creep over 4 s');
  assert.ok(jitter < 1e-4, `no jitter ${jitter}`);
});

check('buggy stopped on a hill rolls back when you let go (coasting), but not on near-flat ground; scout holds', () => {
  const hill = buggy(slope(12));
  const y0 = hill.state.y;
  for (let i = 0; i < 120; i++) hill.step(1 / 60, IDLE);
  // a = g (sin 12 - Crr cos 12) ~ 0.48 m/s^2 -> ~0.96 m in 2 s
  near(hill.state.y - y0, -0.5 * MARS_G * (Math.sin(12 * DEG) - 0.08 * Math.cos(12 * DEG)) * 4 * Math.cos(12 * DEG), 0.05, 'rolled back');
  assert.equal(hill.state.status, 'coasting');
  const gentle = buggy(slope(3));
  const g0 = gentle.state.y;
  for (let i = 0; i < 120; i++) gentle.step(1 / 60, IDLE);
  near(gentle.state.y, g0, 1e-4, 'rolling resistance holds on 3 deg');
  const scout = new RoverPhysics(slope(12), DEFAULT_ROVER_PROFILE, {}, { x: 0, y: 0, heading_deg: 0 });
  const s0 = scout.state.y;
  for (let i = 0; i < 120; i++) scout.step(1 / 60, IDLE);
  near(scout.state.y, s0, 1e-4, 'scout holds');
});

check('coasting downhill is faster than climbing', () => {
  const plane = slope(10);
  const up = buggy(plane);
  const down = buggy(plane, { x: 0, y: 0, heading_deg: 180 });
  for (let i = 0; i < 600; i++) { up.step(1 / 60, FWD); down.step(1 / 60, FWD); }
  assert.ok(down.state.speed_mps > up.state.speed_mps, `down ${down.state.speed_mps} vs up ${up.state.speed_mps}`);
});

check('jumps: fast over a crest it flies (all wheels off), lands smoothly on its springs and drives on; slow it stays down', () => {
  // A 1.5 m high, 16 m long bump (max ~16 deg). Crest curvature k = 1.5 (pi/16)^2, so contact is lost
  // above v = sqrt(g / k) ~ 8 m/s on Mars.
  const bump: HeightSampler = {
    sampleHeight: (_x, y) => (y > 100 && y < 116 ? 1.5 * Math.sin(Math.PI * (y - 100) / 16) : 0),
  };
  const run = (speed: number) => {
    const r = buggy(bump, { x: 0, y: 0, heading_deg: 0 }, { ...DEFAULT_BUGGY_PROFILE, flat_speed_mps: speed });
    const sag = r.suspension.static_sag_m;
    let flight = 0, everAir = false, maxGap = 0, maxDz = 0, maxDp = 0, maxDr = 0, landT = -1, t = 0, maxComp = 0, lateOff = 0;
    let prev = { ...r.state };
    for (let i = 0; i < 60 * 30 && r.state.y < 160; i++) {
      const st = r.step(1 / 60, FWD);
      t += 1 / 60;
      const v3 = Math.hypot(st.speed_mps, st.vz_mps);
      maxDz = Math.max(maxDz, Math.abs(st.z - prev.z) - v3 / 60);
      maxDp = Math.max(maxDp, Math.abs(st.pitch_deg - prev.pitch_deg));
      maxDr = Math.max(maxDr, Math.abs(st.roll_deg - prev.roll_deg));
      if (st.airborne) { everAir = true; flight = Math.max(flight, st.air_time_s); }
      maxGap = Math.max(maxGap, st.z - footprint(bump, st.x, st.y, st.heading_deg, r.params).z);
      if (everAir && !st.airborne && landT < 0) landT = t;
      if (landT > 0 && t - landT < 1) maxComp = Math.max(maxComp, ...st.wheels.map((w) => w.compression_m));
      if (landT > 0 && t - landT > 2 && t - landT < 3) lateOff = Math.max(lateOff, ...st.wheels.map((w) => Math.abs(w.compression_m - sag)));
      prev = { ...st };
    }
    return { r, flight, everAir, maxGap, maxDz, maxDp, maxDr, maxComp, lateOff, sag };
  };
  const fast = run(12);
  assert.ok(fast.flight > 0.3 && fast.everAir, `fast: flight ${fast.flight} s`);
  assert.ok(fast.r.state.y >= 160 && !fast.r.state.airborne && Math.abs(fast.r.state.pitch_deg) < 2, `fast: landed and drove on, y=${fast.r.state.y}`);
  assert.ok(fast.maxDz < 0.02, `no z snaps: worst per-frame excess ${fast.maxDz} m`);
  assert.ok(fast.maxDp < 4 && fast.maxDr < 2, `no attitude snaps: worst per-frame pitch ${fast.maxDp} deg, roll ${fast.maxDr} deg`);
  assert.ok(fast.maxComp > fast.sag + 0.05, `suspension soaks up the landing: max compression ${fast.maxComp} m (sag ${fast.sag})`);
  assert.ok(fast.lateOff < 0.02, `settles back to ride height within ~2 s: |compression - sag| ${fast.lateOff} m`);
  const slow = run(2);
  assert.ok(!slow.everAir, 'slow: wheels stay down');
  assert.ok(slow.maxGap < 0.1, `slow: body follows the ground, gap ${slow.maxGap}`);
  console.log(`    12 m/s over a 1.5 m bump: ${fast.flight.toFixed(2)} s air, ${fast.maxGap.toFixed(2)} m clearance, impact ${fast.r.state.last_impact_mps.toFixed(1)} m/s, ` +
    `max compression ${(fast.maxComp * 100).toFixed(0)} cm; worst frame pitch ${fast.maxDp.toFixed(2)} deg`);
});

check('cornering: grip limits the turn radius at speed (R >= v^2 / mu g); near the 3.1 m minimum at walking pace', () => {
  const turn = (speed: number) => {
    const r = buggy(flat, { x: 0, y: 0, heading_deg: 0 }, { ...DEFAULT_BUGGY_PROFILE, flat_speed_mps: speed });
    for (let i = 0; i < 600; i++) r.step(1 / 60, FWD);
    for (let i = 0; i < 180; i++) r.step(1 / 60, { throttle: 1, steer: 1 });
    // Measure path curvature over 2 s: travelled length / change of travel direction.
    let len = 0, turnAng = 0, vSum = 0, n = 0, maxRoll = 0;
    let px = r.state.x, py = r.state.y, dir: number | null = null;
    for (let i = 0; i < 120; i++) {
      const s = r.step(1 / 60, { throttle: 1, steer: 1 });
      const dx = s.x - px, dy = s.y - py;
      const d = Math.atan2(dx, dy);
      if (dir !== null) { let dd = d - dir; while (dd > Math.PI) dd -= 2 * Math.PI; while (dd < -Math.PI) dd += 2 * Math.PI; turnAng += dd; }
      dir = d;
      len += Math.hypot(dx, dy);
      px = s.x; py = s.y;
      vSum += Math.abs(s.speed_mps); n++;
      maxRoll = Math.max(maxRoll, Math.abs(s.roll_deg));
    }
    return { R: len / Math.abs(turnAng), v: vSum / n, maxRoll, r };
  };
  const fast = turn(12);
  const limit = (fast.v * fast.v) / (DEFAULT_BUGGY_PROFILE.traction_mu * MARS_G);
  assert.ok(fast.R >= 0.9 * limit, `12 m/s: radius ${fast.R} m vs grip limit ${limit} m`);
  assert.ok(fast.maxRoll < 30 && fast.r.state.wheels.some((w) => w.in_contact), `stays on its wheels, roll ${fast.maxRoll}`);
  assert.equal(fast.r.state.status, 'slipping', 'reports the cornering limit');
  const slow = turn(2);
  // Front steering only: tightest turn ~3.1 m at the rear axle, a bit more at the body centre.
  assert.ok(slow.R > 2.6 && slow.R < 4.5, `2 m/s: radius ${slow.R} m`);
  console.log(`    12 m/s full lock: R ${fast.R.toFixed(1)} m at ${fast.v.toFixed(1)} m/s (v^2/mu g = ${limit.toFixed(1)} m), roll ${fast.maxRoll.toFixed(1)} deg; 2 m/s: R ${slow.R.toFixed(2)} m`);
});

check('flat ground at full speed and boost never leaves the ground', () => {
  const r = buggy(flat);
  for (let i = 0; i < 900; i++) {
    const s = r.step(1 / 60, { ...FWD, boost: i > 300 });
    assert.equal(s.airborne, false);
    assert.ok(s.wheels.every((w) => w.in_contact), 'every wheel down');
  }
  assert.ok(r.state.speed_mps > 15, `boosting ${r.state.speed_mps}`);
});

check('roll-over is possible and the chassis rests on the ground (no sinking)', () => {
  // Hard right at 12 m/s across a 25 deg side slope.
  const r = buggy(slope(25), { x: 0, y: 0, heading_deg: 90 });
  for (let i = 0; i < 60 * 8; i++) r.step(1 / 60, { throttle: 1, steer: i > 60 ? -1 : 0 });
  for (let i = 0; i < 60 * 6; i++) r.step(1 / 60, BRAKE);
  const s = r.state;
  assert.ok(allFinite(r), 'finite');
  const ground = slope(25).sampleHeight(s.x, s.y);
  assert.ok(s.z > ground - 1.0, `not sunk: origin ${s.z} vs ground ${ground}`);
  console.log(`    side-slope test ends roll ${s.roll_deg.toFixed(0)} deg, pitch ${s.pitch_deg.toFixed(0)} deg, status ${s.status}`);
});

check('contract 1 mapping: heading 90 faces +X (east), pitch up raises the nose', () => {
  assert.deepEqual(siteToThree(1, 2, 3), [1, 3, -2]);
  const [ex, ey] = poseToEulerYXZ(90, 0, 0);
  // forward (0,0,-1) rotated by ey about Y: (-sin ey, 0, -cos ey)
  near(-Math.sin(ey), 1, 1e-12, 'X component');
  near(-Math.cos(ey), 0, 1e-12, 'Z component');
  assert.equal(ex, 0);
});

// --- height tiles --------------------------------------------------------------

check('bilinear sampler: exact on a linear field, shared edges, edge clamp', () => {
  const size = 1024;
  const s = new TileHeightSampler(size);
  const fn = (x: number, y: number) => 0.02 * x - 0.01 * y - 2400;
  for (const z of [0, 1]) {
    const n = 2 ** z;
    const ts = size / n;
    for (let ty = 0; ty < n; ty++) for (let tx = 0; tx < n; tx++) {
      const d = new Float32Array(257 * 257);
      for (let j = 0; j < 257; j++) for (let i = 0; i < 257; i++) d[j * 257 + i] = fn(-512 + tx * ts + (i * ts) / 256, 512 - ty * ts - (j * ts) / 256);
      s.addTile(z, tx, ty, d);
    }
  }
  for (const [x, y] of [[0, 0], [123.4, -321.9], [-511.9, 511.9], [256, 0], [0.001, -0.001]]) near(s.sampleHeight(x, y), fn(x, y), 1e-3, `at ${x},${y}`);
  near(s.sampleHeight(900, 0), fn(512, 0), 1e-3, 'clamped east');
});

// Minimal PNG reader (8-bit RGB, non-interlaced) to test against the real fixture tiles.
function readPngRGB(file: string): { w: number; h: number; rgba: Uint8Array } {
  const b = readFileSync(file);
  let p = 8;
  let w = 0, h = 0, ct = 0;
  const idat: Buffer[] = [];
  while (p < b.length) {
    const len = b.readUInt32BE(p);
    const type = b.toString('ascii', p + 4, p + 8);
    const data = b.subarray(p + 8, p + 8 + len);
    if (type === 'IHDR') {
      w = data.readUInt32BE(0);
      h = data.readUInt32BE(4);
      assert.equal(data[8], 8, 'bit depth');
      ct = data[9];
      assert.equal(data[12], 0, 'interlace');
    } else if (type === 'IDAT') idat.push(data);
    p += 12 + len;
  }
  const bpp = ct === 2 ? 3 : ct === 6 ? 4 : 0;
  assert.ok(bpp, `colour type ${ct}`);
  const raw = inflateSync(Buffer.concat(idat));
  const stride = w * bpp;
  const out = new Uint8Array(w * h * 4);
  let prev = new Uint8Array(stride);
  for (let y = 0; y < h; y++) {
    const f = raw[y * (stride + 1)];
    const line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    const cur = new Uint8Array(stride);
    for (let i = 0; i < stride; i++) {
      const a = i >= bpp ? cur[i - bpp] : 0, up = prev[i], c = i >= bpp ? prev[i - bpp] : 0;
      let v = line[i];
      if (f === 1) v += a;
      else if (f === 2) v += up;
      else if (f === 3) v += (a + up) >> 1;
      else if (f === 4) {
        const pp = a + up - c, pa = Math.abs(pp - a), pb = Math.abs(pp - up), pc = Math.abs(pp - c);
        v += pa <= pb && pa <= pc ? a : pb <= pc ? up : c;
      }
      cur[i] = v & 255;
    }
    for (let x = 0; x < w; x++) for (let k = 0; k < 3; k++) out[(y * w + x) * 4 + k] = cur[x * bpp + k];
    prev = cur;
  }
  return { w, h, rgba: out };
}

check('fixture height tiles decode to the analytic fixture terrain (pipeline/make_fixture.py); scout cannot climb the scarp', () => {
  const fx = process.env.ARES_FIXTURES ?? path.resolve(process.cwd(), '../../../../contracts/fixtures');
  const manifest = JSON.parse(readFileSync(path.join(fx, 'manifest.json'), 'utf8'));
  const z = manifest.height.max_level as number;
  const s = new TileHeightSampler(manifest.size_m);
  for (let ty = 0; ty < 2 ** z; ty++) for (let tx = 0; tx < 2 ** z; tx++) {
    const { w, h, rgba } = readPngRGB(path.join(fx, `tiles/height/${z}/${tx}/${ty}.png`));
    s.addTile(z, tx, ty, decodeHeightRGBA(rgba, w, h));
  }
  const elevation = (x: number, y: number) => {
    let e = -2400 + 0.02 * x - 0.01 * y;
    const d = Math.hypot(x + 150, y - 100);
    e += -22 * Math.max(0, 1 - (d / 120) ** 2) + 4 * Math.exp(-(((d - 125) / 14) ** 2));
    e += 25 / (1 + Math.exp(-(x - 180) / 10));
    return e + 0.4 * Math.sin(x / 7) * Math.cos(y / 9);
  };
  // Seeded points; bilinear error between 1 m posts peaks near ~0.07 m on the sharp crater rim.
  let seed = 12345;
  const rand = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
  let worst = 0;
  for (let i = 0; i < 2000; i++) {
    const x = (rand() - 0.5) * 1000, y = (rand() - 0.5) * 1000;
    worst = Math.max(worst, Math.abs(s.sampleHeight(x, y) - elevation(x, y)));
  }
  assert.ok(worst < 0.1, `max |sampled - analytic| = ${worst} m`);
  console.log(`    max |sampled - analytic| over 2000 points: ${worst.toFixed(4)} m`);

  // Drive the scout east into the scarp (~32 deg): it cannot get up it. Wheel contact stays consistent:
  // every wheel touching the ground has compression within its travel and the body stays on its wheels.
  const r = new RoverPhysics(s, DEFAULT_ROVER_PROFILE, {}, { x: 100, y: -60, heading_deg: 90 });
  const travel = r.suspension.travel_m;
  let maxGap = 0, minContacts = 4;
  for (let i = 0; i < 1800; i++) {
    const st = r.step(1 / 60, FWD);
    const f = footprint(s, st.x, st.y, st.heading_deg, r.params);
    maxGap = Math.max(maxGap, Math.abs(st.z - f.z));
    minContacts = Math.min(minContacts, st.wheels.filter((w) => w.in_contact).length);
    for (const w of st.wheels) assert.ok(w.compression_m >= 0 && w.compression_m <= travel + 1e-9, `compression ${w.compression_m}`);
  }
  assert.ok(r.state.x < 185, `did not climb the scarp, x=${r.state.x}`);
  assert.ok(maxGap < 0.3 && minContacts >= 3, `body on its wheels: max |z - ground plane| ${maxGap} m, min wheels down ${minContacts}`);
  console.log(`    fixture scarp: scout ends at x=${r.state.x.toFixed(1)} m, status ${r.state.status}; max body-ground plane gap ${maxGap.toFixed(3)} m`);
});

check('buggy: boost reaches boost speed, drains, then recharges; time-lapse scales ground speed only', () => {
  const r = buggy(flat);
  for (let i = 0; i < 60 * 25; i++) r.step(1 / 60, FWD);
  near(r.state.speed_mps, DEFAULT_BUGGY_PROFILE.flat_speed_mps, 0.05, 'buggy top speed');
  let peak = 0;
  for (let i = 0; i < 340; i++) peak = Math.max(peak, r.step(1 / 60, { ...FWD, boost: true }).speed_mps);
  assert.ok(peak > DEFAULT_BUGGY_PROFILE.flat_speed_mps + 5 && peak <= DEFAULT_BUGGY_PROFILE.boost_speed_mps! + 0.05, `boost peak ${peak}`);
  for (let i = 0; i < 60; i++) r.step(1 / 60, { ...FWD, boost: true });
  assert.ok(r.state.boost_charge < 0.01, 'drained');
  assert.equal(r.state.boost_active, false, 'boost stops when empty');
  for (let i = 0; i < 60 * 11; i++) r.step(1 / 60, FWD);
  near(r.state.boost_charge, 1, 1e-9, 'recharged');
  r.setTimeScale(4);
  r.step(1 / 60, FWD);
  near(r.state.display_speed_mps, r.state.speed_mps * 4, 1e-9, 'screen speed x4');
});

check('buggy: car steering cannot turn in place', () => {
  const r = buggy(flat);
  for (let i = 0; i < 60; i++) r.step(1 / 60, { throttle: 0, steer: 1 });
  near(r.state.heading_deg, 0, 0.01, 'no turn while stopped');
  for (let i = 0; i < 120; i++) r.step(1 / 60, { throttle: 1, steer: 1 });
  assert.ok(r.state.heading_deg > 10, 'turns once moving');
});

check('rear-wheel drive: climbs 15 deg, stalls on 24 deg (rear grip ~20 deg); neither buggy nor scout climbs 33 deg', () => {
  const ramp = (deg: number): HeightSampler => ({ sampleHeight: (_x, y) => (y > 5 ? (y - 5) * Math.tan(deg * DEG) : 0) });
  const r15 = buggy(ramp(15));
  for (let i = 0; i < 60 * 40; i++) r15.step(1 / 60, { ...FWD, boost: true });
  assert.ok(r15.state.y > 40, `buggy climbs 15 deg, y=${r15.state.y}`);
  // Only the rear wheels push: with ~61 % static rear weight plus uphill load transfer, rear grip runs out near 20 deg.
  const r24 = buggy(ramp(24));
  let max24 = 0;
  for (let i = 0; i < 60 * 60; i++) max24 = Math.max(max24, r24.step(1 / 60, FWD).y);
  assert.ok(r24.state.speed_mps <= 0.05 && max24 < 80, `stalls on 24 deg: max y ${max24}, speed ${r24.state.speed_mps}`);
  // 33 deg is past grip: momentum carries it a little way up, then the wheels spin and it slides back
  // (tan 33 = 0.65 > mu - Crr); parked on it with the brakes on, it slides too (tan 33 > mu).
  const r = buggy(ramp(33));
  let maxY = 0, slidBack = false;
  for (let i = 0; i < 60 * 30; i++) {
    const s = r.step(1 / 60, { ...FWD, boost: true });
    maxY = Math.max(maxY, s.y);
    if (s.y > 6 && s.speed_mps < -0.3 && s.status === 'sliding') slidBack = true;
  }
  assert.ok(maxY < 35 && slidBack, `momentum only: max y ${maxY}, slid back ${slidBack}`);
  const parked = buggy(ramp(33), { x: 0, y: 20, heading_deg: 0 });
  const y0 = parked.state.y;
  for (let i = 0; i < 120; i++) parked.step(1 / 60, BRAKE);
  assert.ok(parked.state.y < y0 - 0.2, `brakes cannot hold 33 deg: ${y0} -> ${parked.state.y}`);
  const scout = new RoverPhysics(ramp(33), DEFAULT_ROVER_PROFILE, {}, { x: 0, y: 0 });
  for (let i = 0; i < 60 * 40; i++) scout.step(1 / 60, FWD);
  assert.ok(scout.state.y < 8, `scout cannot climb 33 deg, y ${scout.state.y}`);
});

check('performance: one frame costs little (scout at 200x, buggy at x16 time-lapse)', () => {
  const scout = new RoverPhysics(slope(5), DEFAULT_ROVER_PROFILE, {}, { x: 0, y: 0 });
  let t = performance.now();
  for (let i = 0; i < 120; i++) scout.step(1 / 60, FWD);
  const tScout = (performance.now() - t) / 120;
  const b = buggy(slope(5));
  b.setTimeScale(16);
  t = performance.now();
  for (let i = 0; i < 120; i++) b.step(1 / 60, FWD);
  const tBuggy = (performance.now() - t) / 120;
  assert.ok(tScout < 4 && tBuggy < 4, `ms per frame: scout ${tScout}, buggy x16 ${tBuggy}`);
  console.log(`    ms per frame: scout ${tScout.toFixed(2)}, buggy x1 ~${(tBuggy / 16).toFixed(2)}, buggy x16 ${tBuggy.toFixed(2)}`);
});

console.log(`\n${passed} checks passed${failed ? `, ${failed} FAILED` : ''}`);
if (failed) process.exit(1);
