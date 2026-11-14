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
import { DEFAULT_ROVER_PROFILE, RoverPhysics, footprint } from '../physics';

let passed = 0;
function check(name: string, fn: () => void) {
  fn();
  passed++;
  console.log(`ok  ${name}`);
}
const near = (a: number, b: number, tol: number, msg: string) => assert.ok(Math.abs(a - b) <= tol, `${msg}: ${a} vs ${b} (tol ${tol})`);
const FWD = { throttle: 1, steer: 0 };

// --- physics -----------------------------------------------------------------

check('flat ground: z = ground, pitch = roll = 0 while driving and turning', () => {
  const flat: HeightSampler = { sampleHeight: () => -2400 };
  const r = new RoverPhysics(flat, DEFAULT_ROVER_PROFILE, {}, { x: 0, y: 0, heading_deg: 30 });
  for (let i = 0; i < 300; i++) {
    const s = r.step(1 / 60, { throttle: 1, steer: i < 150 ? 0.5 : 0 });
    assert.equal(s.z, -2400);
    assert.equal(s.pitch_deg, 0);
    assert.equal(s.roll_deg, 0);
  }
  assert.ok(r.state.odometer_m > 5, `rover should have moved, odometer ${r.state.odometer_m}`);
  near(Math.abs(r.state.speed_mps), DEFAULT_ROVER_PROFILE.flat_speed_mps, 1e-9, 'flat speed reached');
});

check('tilted plane: pitch = atan(gradient) uphill, roll = atan(gradient) side-on, z = ground', () => {
  const g = 0.15; // z rises 0.15 m per m northward
  const plane: HeightSampler = { sampleHeight: (_x, y) => g * y };
  const expect = Math.atan(g) / DEG;
  const north = new RoverPhysics(plane, DEFAULT_ROVER_PROFILE, {}, { x: 0, y: 0, heading_deg: 0 });
  for (let i = 0; i < 60; i++) north.step(1 / 60, FWD);
  near(north.state.pitch_deg, expect, 1e-9, 'pitch heading north');
  near(north.state.roll_deg, 0, 1e-9, 'roll heading north');
  near(north.state.z, plane.sampleHeight(north.state.x, north.state.y), 1e-9, 'z on plane');
  near(north.state.slope_deg, expect, 1e-9, 'footprint slope');

  const south = new RoverPhysics(plane, DEFAULT_ROVER_PROFILE, {}, { x: 0, y: 0, heading_deg: 180 });
  near(south.state.pitch_deg, -expect, 1e-9, 'pitch heading south (downhill)');

  const east = new RoverPhysics(plane, DEFAULT_ROVER_PROFILE, {}, { x: 0, y: 0, heading_deg: 90 });
  near(east.state.pitch_deg, 0, 1e-9, 'pitch heading east');
  near(east.state.roll_deg, expect, 1e-9, 'roll heading east (right side = south = lower)');

  // Speed follows the profile curve on 8.5 deg: flat * exp(-k * tan).
  const want = DEFAULT_ROVER_PROFILE.flat_speed_mps * Math.exp(-DEFAULT_ROVER_PROFILE.speed_curve_k * g);
  near(north.state.speed_mps, want, 1e-9, 'slope speed');
});

check('refuses to climb a 30 deg slope with nogo 20 (stops on the flat, reports blocked_slope)', () => {
  const t30 = Math.tan(30 * DEG);
  const ramp: HeightSampler = { sampleHeight: (_x, y) => (y > 10 ? (y - 10) * t30 : 0) };
  const r = new RoverPhysics(ramp, { ...DEFAULT_ROVER_PROFILE, nogo_slope_deg: 20 }, {}, { x: 0, y: 0, heading_deg: 0 });
  for (let i = 0; i < 600; i++) r.step(1 / 60, FWD);
  const s = r.state;
  assert.equal(s.status, 'blocked_slope');
  assert.equal(s.speed_mps, 0);
  assert.ok(s.stop_reason && /no-go/.test(s.stop_reason), `stop reason: ${s.stop_reason}`);
  assert.ok(s.y + 2.7 / 2 < 10, `front axle must stay off the ramp, y=${s.y}`);
  near(s.z, 0, 1e-9, 'still on the flat');
  assert.ok(s.y > 5, `should have driven up to the ramp first, y=${s.y}`);
  // Backing away is allowed.
  for (let i = 0; i < 60; i++) r.step(1 / 60, { throttle: -1, steer: 0 });
  assert.ok(r.state.status !== 'blocked_slope' && r.state.speed_mps < 0, 'can reverse away');
});

check('same ramp at 18 deg (caution, under nogo 20) is climbed, slower', () => {
  const t = Math.tan(18 * DEG);
  const ramp: HeightSampler = { sampleHeight: (_x, y) => (y > 10 ? (y - 10) * t : 0) };
  const r = new RoverPhysics(ramp, DEFAULT_ROVER_PROFILE, {}, { x: 0, y: 0, heading_deg: 0 });
  for (let i = 0; i < 1800; i++) r.step(1 / 60, FWD);
  assert.ok(r.state.y > 30, `should be well up the ramp, y=${r.state.y}`);
  assert.equal(r.state.status, 'caution');
  near(r.state.pitch_deg, 18, 1e-6, 'pitch on 18 deg ramp');
  const want = 0.04 * Math.exp(-3.5 * t) * 0.5;
  near(r.state.speed_mps, want, 1e-9, 'caution speed');
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
  let worst = 0;
  for (let i = 0; i < 2000; i++) {
    const x = (Math.random() - 0.5) * 1000, y = (Math.random() - 0.5) * 1000;
    worst = Math.max(worst, Math.abs(s.sampleHeight(x, y) - elevation(x, y)));
  }
  assert.ok(worst < 0.05, `max |sampled - analytic| = ${worst} m`);
  console.log(`    max |sampled - analytic| over 2000 points: ${worst.toFixed(4)} m`);

  // Drive east into the scarp (~35 deg at x = 180): must stop before it, never float/sink > 0.2 m.
  const r = new RoverPhysics(s, DEFAULT_ROVER_PROFILE, {}, { x: 100, y: -60, heading_deg: 90 });
  let maxGap = 0;
  for (let i = 0; i < 1200; i++) {
    const st = r.step(1 / 60, FWD);
    const f = footprint(s, st.x, st.y, st.heading_deg);
    maxGap = Math.max(maxGap, Math.abs(st.z - s.sampleHeight(st.x, st.y)), Math.abs(st.z - f.z));
  }
  assert.equal(r.state.status, 'blocked_slope');
  assert.ok(r.state.x < 180, `stopped before the scarp crest, x=${r.state.x}`);
  assert.ok(maxGap < 0.2, `float/sink ${maxGap} m`);
  console.log(`    fixture scarp: stopped at x=${r.state.x.toFixed(1)} m (${r.state.stop_reason}); max body-ground gap ${maxGap.toFixed(3)} m`);
});

console.log(`\n${passed} checks passed`);
