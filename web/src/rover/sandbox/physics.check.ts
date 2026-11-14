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
import { BUGGY_PARAMS, DEFAULT_BUGGY_PROFILE, DEFAULT_ROVER_PROFILE, RoverPhysics, climbSpeed, footprint } from '../physics';

let passed = 0;
function check(name: string, fn: () => void) {
  fn();
  passed++;
  console.log(`ok  ${name}`);
}
const near = (a: number, b: number, tol: number, msg: string) => assert.ok(Math.abs(a - b) <= tol, `${msg}: ${a} vs ${b} (tol ${tol})`);
const FWD = { throttle: 1, steer: 0 };

// --- physics -----------------------------------------------------------------

check('flat ground: z = ground, pitch = roll = 0; speed settles at the controller cap', () => {
  const flat: HeightSampler = { sampleHeight: () => -2400 };
  const r = new RoverPhysics(flat, DEFAULT_ROVER_PROFILE, {}, { x: 0, y: 0, heading_deg: 30 });
  for (let i = 0; i < 300; i++) {
    const s = r.step(1 / 60, { throttle: 1, steer: i < 150 ? 0.5 : 0 });
    assert.equal(s.z, -2400);
    assert.equal(s.pitch_deg, 0);
    assert.equal(s.roll_deg, 0);
  }
  assert.ok(r.state.odometer_m > 5, `rover should have moved, odometer ${r.state.odometer_m}`);
  near(r.state.speed_mps, DEFAULT_ROVER_PROFILE.flat_speed_mps, 1e-3, 'flat speed cap');
  // Released throttle: brakes stop it.
  for (let i = 0; i < 120; i++) r.step(1 / 60, { throttle: 0, steer: 0 });
  assert.equal(r.state.speed_mps, 0);
});

check('tilted plane: pitch = atan(gradient) uphill, roll = atan(gradient) side-on; climb speed from power', () => {
  const g = 0.15; // z rises 0.15 m per m northward (8.5 deg)
  const plane: HeightSampler = { sampleHeight: (_x, y) => g * y };
  const expect = Math.atan(g) / DEG;
  const north = new RoverPhysics(plane, DEFAULT_BUGGY_PROFILE, BUGGY_PARAMS, { x: 0, y: 0, heading_deg: 0 });
  for (let i = 0; i < 1200; i++) north.step(1 / 60, FWD);
  near(north.state.pitch_deg, expect, 1e-9, 'pitch heading north');
  near(north.state.roll_deg, 0, 1e-9, 'roll heading north');
  near(north.state.z, plane.sampleHeight(north.state.x, north.state.y), 1e-9, 'z on plane');
  near(north.state.speed_mps, climbSpeed(DEFAULT_BUGGY_PROFILE, expect), 0.02, 'steady climb speed = P / (m g (sin + Crr cos))');
  assert.ok(north.state.speed_mps < DEFAULT_BUGGY_PROFILE.flat_speed_mps, 'slower uphill');

  const south = new RoverPhysics(plane, DEFAULT_ROVER_PROFILE, {}, { x: 0, y: 0, heading_deg: 180 });
  near(south.state.pitch_deg, -expect, 1e-9, 'pitch heading south (downhill)');
  const east = new RoverPhysics(plane, DEFAULT_ROVER_PROFILE, {}, { x: 0, y: 0, heading_deg: 90 });
  near(east.state.pitch_deg, 0, 1e-9, 'pitch heading east');
  near(east.state.roll_deg, expect, 1e-9, 'roll heading east (right side = south = lower)');
});

check('no hard blocks: 30 deg ramp past the grip limit makes the scout slip, not freeze, and it can back away', () => {
  const t30 = Math.tan(30 * DEG);
  const ramp: HeightSampler = { sampleHeight: (_x, y) => (y > 10 ? (y - 10) * t30 : 0) };
  const r = new RoverPhysics(ramp, DEFAULT_ROVER_PROFILE, {}, { x: 0, y: 0, heading_deg: 0 });
  let maxY = 0;
  for (let i = 0; i < 1800; i++) maxY = Math.max(maxY, r.step(1 / 60, FWD).y);
  assert.ok(maxY > 9, `reached and touched the ramp, y=${maxY}`);
  assert.ok(r.state.y < 13, `cannot climb 30 deg (grip ~31 deg minus rolling resistance), y=${r.state.y}`);
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
  const r = new RoverPhysics(dip, DEFAULT_BUGGY_PROFILE, BUGGY_PARAMS, { x: 0, y: 0, heading_deg: 0 });
  let minZ = 0;
  for (let i = 0; i < 60 * 30 && r.state.y < 50; i++) minZ = Math.min(minZ, r.step(1 / 60, FWD).z);
  assert.ok(minZ < -1.5, `went down into the dip, min z ${minZ}`);
  assert.ok(r.state.y >= 50, `came out the far side, y=${r.state.y}`);
});

check('released on 35 deg the brakes cannot hold: it slides downhill; on 20 deg it holds', () => {
  const slope = (deg: number): HeightSampler => ({ sampleHeight: (_x, y) => y * Math.tan(deg * DEG) });
  const steep = new RoverPhysics(slope(35), DEFAULT_BUGGY_PROFILE, BUGGY_PARAMS, { x: 0, y: 0, heading_deg: 0 });
  for (let i = 0; i < 120; i++) steep.step(1 / 60, { throttle: 0, steer: 0 });
  assert.ok(steep.state.y < -0.5 && steep.state.status === 'sliding', `slid down, y=${steep.state.y} ${steep.state.status}`);
  const mild = new RoverPhysics(slope(20), DEFAULT_BUGGY_PROFILE, BUGGY_PARAMS, { x: 0, y: 0, heading_deg: 0 });
  for (let i = 0; i < 120; i++) mild.step(1 / 60, { throttle: 0, steer: 0 });
  near(mild.state.y, 0, 1e-6, 'held by brakes');
});

check('coasting downhill is faster than climbing', () => {
  const g = Math.tan(10 * DEG);
  const plane: HeightSampler = { sampleHeight: (_x, y) => g * y };
  const up = new RoverPhysics(plane, DEFAULT_BUGGY_PROFILE, BUGGY_PARAMS, { x: 0, y: 0, heading_deg: 0 });
  const down = new RoverPhysics(plane, DEFAULT_BUGGY_PROFILE, BUGGY_PARAMS, { x: 0, y: 0, heading_deg: 180 });
  for (let i = 0; i < 600; i++) { up.step(1 / 60, FWD); down.step(1 / 60, FWD); }
  assert.ok(down.state.speed_mps > up.state.speed_mps, `down ${down.state.speed_mps} vs up ${up.state.speed_mps}`);
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

check('fixture height tiles decode to the analytic fixture terrain (pipeline/make_fixture.py)', () => {
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

  // Drive the scout east into the scarp (~32 deg): it cannot get up it, never floats/sinks > 0.2 m.
  const r = new RoverPhysics(s, DEFAULT_ROVER_PROFILE, {}, { x: 100, y: -60, heading_deg: 90 });
  let maxGap = 0;
  for (let i = 0; i < 1800; i++) {
    const st = r.step(1 / 60, FWD);
    const f = footprint(s, st.x, st.y, st.heading_deg);
    maxGap = Math.max(maxGap, Math.abs(st.z - s.sampleHeight(st.x, st.y)), Math.abs(st.z - f.z));
  }
  assert.ok(r.state.x < 185, `did not climb the scarp, x=${r.state.x}`);
  assert.ok(maxGap < 0.2, `float/sink ${maxGap} m`);
  console.log(`    fixture scarp: scout ends at x=${r.state.x.toFixed(1)} m, status ${r.state.status}; max body-ground gap ${maxGap.toFixed(3)} m`);
});

check('buggy: boost reaches boost speed, drains, then recharges; time-lapse scales ground speed only', () => {
  const flat: HeightSampler = { sampleHeight: () => -2400 };
  const r = new RoverPhysics(flat, DEFAULT_BUGGY_PROFILE, BUGGY_PARAMS, { x: 0, y: 0 });
  for (let i = 0; i < 600; i++) r.step(1 / 60, FWD);
  near(r.state.speed_mps, 3.6, 1e-3, 'buggy flat speed (LRV)');
  let peak = 0;
  for (let i = 0; i < 230; i++) peak = Math.max(peak, r.step(1 / 60, { ...FWD, boost: true }).speed_mps);
  near(peak, 5.0, 1e-3, 'boost speed (LRV record)');
  for (let i = 0; i < 60; i++) r.step(1 / 60, { ...FWD, boost: true });
  assert.ok(r.state.boost_charge < 0.01, 'drained');
  assert.equal(r.state.boost_active, false, 'boost stops when empty');
  for (let i = 0; i < 60 * 9; i++) r.step(1 / 60, FWD);
  near(r.state.boost_charge, 1, 1e-9, 'recharged');
  r.setTimeScale(4);
  r.step(1 / 60, FWD);
  near(r.state.display_speed_mps, r.state.speed_mps * 4, 1e-9, 'screen speed x4');
});

check('buggy: car steering cannot turn in place', () => {
  const flat: HeightSampler = { sampleHeight: () => -2400 };
  const r = new RoverPhysics(flat, DEFAULT_BUGGY_PROFILE, BUGGY_PARAMS, { x: 0, y: 0, heading_deg: 0 });
  for (let i = 0; i < 60; i++) r.step(1 / 60, { throttle: 0, steer: 1 });
  assert.equal(r.state.heading_deg, 0, 'no turn while stopped');
  for (let i = 0; i < 120; i++) r.step(1 / 60, { throttle: 1, steer: 1 });
  assert.ok(r.state.heading_deg > 10, 'turns once moving');
});

check('buggy climbs 22 deg (grip-limited ~27 deg), scout slower on it; neither climbs 33 deg', () => {
  const ramp = (deg: number): HeightSampler => ({ sampleHeight: (_x, y) => (y > 5 ? (y - 5) * Math.tan(deg * DEG) : 0) });
  const climb = (prof: typeof DEFAULT_ROVER_PROFILE, params: object, deg: number, secs = 40) => {
    const r = new RoverPhysics(ramp(deg), prof, params, { x: 0, y: 0 });
    for (let i = 0; i < 60 * secs; i++) r.step(1 / 60, { ...FWD, boost: true });
    return r.state;
  };
  assert.ok(climb(DEFAULT_BUGGY_PROFILE, BUGGY_PARAMS, 22).y > 20, 'buggy climbs 22 deg');
  // 33 deg is past grip: momentum carries it a little way, then it stalls; let go and it slides.
  const r = new RoverPhysics(ramp(33), DEFAULT_BUGGY_PROFILE, BUGGY_PARAMS, { x: 0, y: 0 });
  for (let i = 0; i < 60 * 60; i++) r.step(1 / 60, { ...FWD, boost: true });
  assert.ok(r.state.y < 20 && r.state.speed_mps === 0, `stalled low on the ramp, y ${r.state.y}`);
  const stall = r.state.y;
  for (let i = 0; i < 120; i++) r.step(1 / 60, { throttle: 0, steer: 0 });
  assert.ok(r.state.y < stall - 0.2, `slid back after release: ${stall} -> ${r.state.y}`);
  assert.ok(climb(DEFAULT_ROVER_PROFILE, {}, 33).y < 8, 'scout cannot climb 33 deg');
});

console.log(`\n${passed} checks passed`);
