/**
 * Static chassis of the buggy: space frame, roll cage, skid plate, bumpers,
 * floor, seats, fenders, boxes, antennas, camera mast, light bar, panels,
 * racks and cables. Everything here is merged per material.
 *
 * Model space: origin on the ground at the centre, +Y up, forward = -Z,
 * right = +X, metres.
 */

import * as THREE from 'three';
import { Batch, HelixCurve, M, box, cable, cageTube, rod, roundedBox, type V3 } from './geom';
import type { BuggyMaterials } from './materials';

export const WHEELBASE = 2.3;
export const TRACK = 1.8;

/** Suspension hard points (right side; mirror x for left). */
export const SUSP = {
  innerX: 0.34,
  outerX: 0.7,
  upperY: 0.6,
  lowerY: 0.32,
  armSpread: 0.16,
  uprightX: 0.72,
  shockTop: [0.45, 0.93, 0.0] as V3, // relative z to axle
  shockLowFrac: 0.78, // along lower arm
};

/** T-handle hand controller grip centre (bar along X, half-width HC.half). */
export const HC = { x: -0.19, y: 0.98, z: -0.1, half: 0.14 };

export interface ChassisResult {
  group: THREE.Group;
  bolts: THREE.Matrix4[];
  lightBarGlowPoints: THREE.Vector3[];
  headlightPoints: THREE.Vector3[];
}

export function buildChassis(mats: BuggyMaterials): ChassisResult {
  const b = new Batch();
  const bolts: THREE.Matrix4[] = [];
  const R = 0.024; // main tube radius
  const r = 0.016; // secondary tubes
  const zf = -WHEELBASE / 2;
  const zr = WHEELBASE / 2;

  const bolt = (x: number, y: number, z: number, nx = 0, ny = 1, nz = 0) => {
    const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), new THREE.Vector3(nx, ny, nz).normalize());
    bolts.push(new THREE.Matrix4().compose(new THREE.Vector3(x, y, z), q, new THREE.Vector3(1, 1, 1)));
  };
  /** bolts around the top face of a box (centre c, size s) */
  const boltRing = (c: V3, s: V3, n = 3) => {
    const [cx, cy, cz] = c;
    const [sx, sy, sz] = s;
    const y = cy + sy / 2;
    for (let i = 0; i <= n; i++) {
      const t = i / n - 0.5;
      bolt(cx + t * (sx - 0.04), y, cz - sz / 2 + 0.02);
      bolt(cx + t * (sx - 0.04), y, cz + sz / 2 - 0.02);
      if (i > 0 && i < n) {
        bolt(cx - sx / 2 + 0.02, y, cz + t * (sz - 0.04));
        bolt(cx + sx / 2 - 0.02, y, cz + t * (sz - 0.04));
      }
    }
  };

  // ---------------------------------------------------------------- frame
  for (const s of [-1, 1]) {
    // lower main rails, kicked up at both ends
    cageTube(b, mats.frame, [[s * 0.32, 0.5, -1.56], [s * 0.32, 0.4, -1.2], [s * 0.32, 0.4, 1.2], [s * 0.32, 0.5, 1.56]], R, 0.15);
    // upper rails front and rear (shock tower carriers)
    cageTube(b, mats.frame, [[s * 0.42, 0.62, -1.58], [s * 0.42, 0.8, -1.35], [s * 0.42, 0.8, -0.66]], R, 0.15);
    cageTube(b, mats.frame, [[s * 0.42, 0.8, 0.66], [s * 0.42, 0.8, 1.35], [s * 0.42, 0.62, 1.58]], R, 0.15);
    // cockpit side sills + nerf bars
    cageTube(b, mats.frame, [[s * 0.32, 0.4, -0.66], [s * 0.82, 0.42, -0.6], [s * 0.86, 0.44, 0.0], [s * 0.82, 0.42, 0.64], [s * 0.32, 0.4, 0.68]], R, 0.12);
    cageTube(b, mats.frame, [[s * 0.7, 0.78, -0.66], [s * 0.9, 0.72, -0.3], [s * 0.9, 0.72, 0.3], [s * 0.7, 0.8, 0.7]], r, 0.2);
    // verticals tying rails together at bulkheads
    for (const z of [-0.66, 0.68]) {
      b.add(rod([s * 0.32, 0.4, z], [s * 0.42, 0.8, z], R), mats.frame);
      b.add(rod([s * 0.42, 0.8, z], [s * 0.7, 0.8, z], r), mats.frame);
      b.add(rod([s * 0.7, 0.8, z], [s * 0.82, 0.42, z * 0.93], r), mats.frame);
    }
    // front & rear diagonals
    for (const zs of [-1, 1]) {
      b.add(rod([s * 0.32, 0.4, zs * 1.35], [s * 0.42, 0.8, zs * 0.85], r), mats.frame);
      b.add(rod([s * 0.32, 0.4, zs * 0.85], [s * 0.42, 0.8, zs * 1.35], r), mats.frame);
    }
    // shock towers (plates + braces) at each axle
    for (const za of [zf, zr]) {
      const [tx, ty, tz] = SUSP.shockTop;
      const top: V3 = [s * tx, ty, za + tz];
      b.add(rod([s * 0.42, 0.8, za - 0.18], top, r), mats.darkMetal);
      b.add(rod([s * 0.42, 0.8, za + 0.25], top, r), mats.darkMetal);
      b.add(rod([s * 0.32, 0.4, za + 0.07], top, r), mats.darkMetal);
      b.add(box(0.05, 0.06, 0.07), mats.darkMetal, M(top[0] - s * 0.02, top[1] + 0.01, top[2]));
      bolt(top[0] - s * 0.045, top[1] + 0.01, top[2], -s, 0, 0);
      // wishbone pivot brackets on the rails
      for (const y of [SUSP.upperY, SUSP.lowerY]) {
        for (const dz of [-SUSP.armSpread, SUSP.armSpread]) {
          b.add(box(0.06, 0.05, 0.03), mats.darkMetal, M(s * (SUSP.innerX - 0.02), y, za + dz));
          bolt(s * (SUSP.innerX - 0.02), y + 0.026, za + dz);
        }
        b.add(rod([s * 0.32, 0.4, za - SUSP.armSpread], [s * 0.32, y, za - SUSP.armSpread], r * 0.9), mats.frame);
        b.add(rod([s * 0.32, 0.4, za + SUSP.armSpread], [s * 0.32, y, za + SUSP.armSpread], r * 0.9), mats.frame);
      }
      // steering rack ends
      b.add(rod([s * 0.1, 0.44, za + 0.14], [s * 0.3, 0.44, za + 0.14], 0.02, 12), mats.darkMetal);
      const boot = new THREE.CylinderGeometry(0.024, 0.018, 0.05, 12);
      b.add(boot, mats.black, M(s * 0.31, 0.44, za + 0.14, 0, 0, Math.PI / 2));
      // drive motor in hub area (LRV: hub motors) -> inboard motor housing
    }
  }
  // cross members
  for (const z of [-1.5, -1.2, -0.66, 0, 0.68, 1.2, 1.5]) b.add(rod([-0.32, 0.4, z], [0.32, 0.4, z], r), mats.frame);
  for (const z of [-1.35, -0.66, 0.68, 1.35]) b.add(rod([-0.42, 0.8, z], [0.42, 0.8, z], r), mats.frame);
  for (const za of [zf, zr]) b.add(rod([-0.1, 0.44, za + 0.14], [0.1, 0.44, za + 0.14], 0.03, 14), mats.darkMetal); // rack body

  // ---------------------------------------------------------------- roll cage
  const hz = 0.72; // main hoop z
  const top = 1.92;
  const hoop: V3[] = [[-0.62, 0.4, hz], [-0.74, 0.86, hz], [-0.68, top, hz], [0.68, top, hz], [0.74, 0.86, hz], [0.62, 0.4, hz]];
  cageTube(b, mats.frame, hoop, R, 0.16);
  // hoop cross bar + diagonal behind the seats (below PLSS line at the sides)
  b.add(rod([-0.73, 1.0, hz], [0.73, 1.0, hz], r), mats.frame);
  b.add(rod([-0.08, 1.0, hz], [0.08, 1.0, hz], R * 1.1), mats.darkMetal);
  // shoulder harness bar
  b.add(rod([-0.72, 1.42, hz], [0.72, 1.42, hz], r), mats.frame);
  for (const s of [-1, 1]) {
    // A-pillars from front bulkhead to roof
    cageTube(b, mats.frame, [[s * 0.7, 0.8, -0.66], [s * 0.78, 1.15, -0.55], [s * 0.7, 1.88, -0.28], [s * 0.68, top, 0.2], [s * 0.68, top, hz]], R, 0.18, true);
    // rear stays down to the rear upper rails
    cageTube(b, mats.frame, [[s * 0.68, top, hz], [s * 0.5, 1.2, 1.42], [s * 0.42, 0.8, 1.48]], r * 1.15, 0.1);
    // gussets
    b.add(rod([s * 0.68, top - 0.02, hz - 0.22], [s * 0.705, top - 0.25, hz], r * 0.8), mats.frame);
  }
  // roof front cross bar (light bar mount) and mid bar
  cageTube(b, mats.frame, [[-0.7, 1.88, -0.28], [0, 1.95, -0.3], [0.7, 1.88, -0.28]], R, 0.5, false);
  b.add(rod([-0.68, top, 0.2], [0.68, top, 0.2], r), mats.frame);
  // carbon sun shade over the rear half of the roof
  const shade = roundedBox(1.32, 0.52, 0.012, 0.06, 0.004);
  b.add(shade, mats.carbon, M(0, top + 0.03, 0.46, -Math.PI / 2));

  // ---------------------------------------------------------------- floor / skid / bumpers
  // skid plate under the frame (dark anodised), upturned at the ends
  b.add(box(0.72, 0.012, 2.3), mats.darkMetal, M(0, 0.37, 0));
  b.add(box(0.72, 0.012, 0.34), mats.darkMetal, M(0, 0.42, -1.3, -0.45));
  b.add(box(0.72, 0.012, 0.34), mats.darkMetal, M(0, 0.42, 1.3, 0.45));
  for (let i = 0; i < 8; i++) bolt(-0.3 + (i % 2) * 0.6, 0.3635, -1.0 + Math.floor(i / 2) * 0.66, 0, -1, 0);
  // cockpit floor (carbon) with grip strips
  b.add(box(1.62, 0.02, 1.3), mats.carbon, M(0, 0.4, 0.0));
  for (let i = 0; i < 6; i++) b.add(box(0.36, 0.006, 0.03), mats.black, M(-0.4, 0.413, -0.58 + i * 0.07));
  for (let i = 0; i < 6; i++) b.add(box(0.36, 0.006, 0.03), mats.black, M(0.4, 0.413, -0.58 + i * 0.07));
  // footrests (angled plates) with bolts
  for (const x of [-0.4, 0.4]) {
    b.add(box(0.42, 0.012, 0.16), mats.darkMetal, M(x, 0.47, -0.62, 0.9));
    bolt(x - 0.15, 0.49, -0.6, 0, 0.6, 0.8);
    bolt(x + 0.15, 0.49, -0.6, 0, 0.6, 0.8);
  }
  // centre tunnel / console between seats
  const tunnel = roundedBox(0.3, 0.26, 1.0, 0.04);
  b.add(tunnel, mats.carbon, M(0, 0.54, 0.05));
  b.add(box(0.26, 0.012, 0.22), mats.darkMetal, M(0, 0.676, 0.33));
  for (const dx of [-0.1, 0.1]) for (const dz of [0.24, 0.42]) bolt(dx, 0.683, dz);

  // front bumper (tube) + bash plates + tow loops
  for (const zs of [-1, 1]) {
    const zb = zs * 1.64;
    cageTube(b, mats.frame, [[-0.32, 0.5, zs * 1.56], [-0.62, 0.52, zb * 0.99], [-0.66, 0.66, zb], [0.66, 0.66, zb], [0.62, 0.52, zb * 0.99], [0.32, 0.5, zs * 1.56]], R * 1.1, 0.12);
    b.add(rod([-0.55, 0.48, zb * 0.985], [0.55, 0.48, zb * 0.985], R), mats.frame);
    for (const x of [-0.3, 0, 0.3]) b.add(rod([x, 0.48, zb * 0.985], [x, 0.66, zb], r), mats.frame);
    b.add(box(0.5, 0.012, 0.2), mats.darkMetal, M(0, 0.47, zs * 1.6, zs * 0.25));
    for (const x of [-0.45, 0.45]) {
      const loop = new THREE.TorusGeometry(0.035, 0.009, 8, 16);
      b.add(loop, mats.orange, M(x, 0.5, zb + zs * 0.03, 0, Math.PI / 2, 0));
    }
  }
  // headlights on the front bumper
  const headlightPoints: THREE.Vector3[] = [];
  for (const x of [-0.42, 0.42]) {
    const housing = new THREE.CylinderGeometry(0.055, 0.045, 0.07, 20);
    b.add(housing, mats.darkMetal, M(x, 0.74, -1.62, Math.PI / 2));
    const lens = new THREE.CircleGeometry(0.046, 20);
    b.add(lens, mats.lamp, M(x, 0.74, -1.656, 0, Math.PI));
    b.add(rod([x, 0.66, -1.62], [x, 0.7, -1.62], 0.012), mats.darkMetal);
    headlightPoints.push(new THREE.Vector3(x, 0.74, -1.67));
  }

  // ---------------------------------------------------------------- front bay
  // battery (gold foil blanket), radiator top with louvres
  const batt = { c: [0, 0.6, -1.02] as V3, s: [0.56, 0.34, 0.6] as V3 };
  b.add(roundedBox(batt.s[0], batt.s[1], batt.s[2], 0.03, 0.01), mats.gold, M(...batt.c));
  b.add(box(0.6, 0.02, 0.64), mats.paint, M(0, 0.785, -1.02));
  for (let i = 0; i < 9; i++) b.add(box(0.5, 0.012, 0.03), mats.chrome, M(0, 0.8, -1.26 + i * 0.06, -0.5));
  boltRing([0, 0.785, -1.02], [0.6, 0.02, 0.64], 4);
  // electronics box (avionics) on the right of the front bay with panel lines
  b.add(box(0.26, 0.2, 0.3), mats.paint, M(0.28, 0.9, -0.84));
  boltRing([0.28, 0.9, -0.84], [0.26, 0.2, 0.3], 2);
  // connectors on the electronics box
  for (let i = 0; i < 3; i++) {
    const conn = new THREE.CylinderGeometry(0.016, 0.016, 0.03, 12);
    b.add(conn, mats.darkMetal, M(0.18 + i * 0.07, 0.95, -0.995, Math.PI / 2));
  }
  // carbon nose / cowl
  const nose = roundedBox(0.92, 0.03, 0.36, 0.08, 0.008);
  b.add(nose, mats.paint, M(0, 0.7, -1.47, Math.PI / 2 - 0.55));
  for (const x of [-0.25, 0.25]) b.add(roundedBox(0.2, 0.012, 0.05, 0.02, 0.003), mats.black, M(x, 0.75, -1.43, Math.PI / 2 - 0.55).multiply(M(0, 0.016, 0)));
  b.add(roundedBox(0.5, 0.18, 0.012, 0.03), mats.orange, M(0, 0.62, -1.58));

  // dashboard + firewall
  b.add(box(1.36, 0.36, 0.03), mats.carbon, M(0, 0.6, -0.69));
  const dash = roundedBox(1.2, 0.03, 0.3, 0.05, 0.008);
  b.add(dash, mats.carbon, M(0, 0.95, -0.76, Math.PI / 2 + 0.6));
  // display console (centre, LRV-style) with screens
  b.add(roundedBox(0.36, 0.26, 0.1, 0.02), mats.paint, M(0, 1.08, -0.78, -0.5));
  b.add(new THREE.PlaneGeometry(0.28, 0.14), mats.screen, M(0, 1.105, -0.722, -0.5));
  b.add(new THREE.PlaneGeometry(0.18, 0.09), mats.screen, M(-0.4, 1.0, -0.66, -0.75));
  b.add(box(0.22, 0.12, 0.05), mats.darkMetal, M(-0.4, 0.99, -0.69, -0.75));
  // switches / knobs on console
  for (let i = 0; i < 5; i++) {
    const k = new THREE.CylinderGeometry(0.009, 0.009, 0.02, 8);
    b.add(k, mats.orange, M(-0.12 + i * 0.06, 0.97, -0.705, -0.5 + Math.PI / 2));
  }
  // T-handle hand controller (driver side of the centre console)
  // cantilevered from the centre tunnel so it clears the driver's right thigh
  const hc = HC;
  b.add(roundedBox(0.14, 0.06, 0.2, 0.02), mats.darkMetal, M(-0.04, 0.7, hc.z + 0.04));
  b.add(rod([-0.04, 0.72, hc.z], [hc.x, 0.86, hc.z], 0.022, 12), mats.darkMetal);
  b.add(new THREE.SphereGeometry(0.03, 12, 8), mats.darkMetal, M(hc.x, 0.86, hc.z));
  const bootG = new THREE.CylinderGeometry(0.022, 0.034, 0.06, 14);
  b.add(bootG, mats.black, M(hc.x, 0.89, hc.z));
  b.add(rod([hc.x, 0.9, hc.z], [hc.x, hc.y, hc.z], 0.014, 12), mats.chrome);
  b.add(rod([hc.x - hc.half, hc.y, hc.z], [hc.x + hc.half, hc.y, hc.z], 0.018, 14), mats.black);
  for (const dx of [-hc.half, hc.half]) b.add(new THREE.SphereGeometry(0.02, 12, 8), mats.orange, M(hc.x + dx, hc.y, hc.z));
  b.add(box(0.03, 0.02, 0.03), mats.orange, M(hc.x, hc.y + 0.024, hc.z));

  // ---------------------------------------------------------------- seats
  for (const x of [-0.4, 0.4]) {
    // tubular seat frame
    cageTube(b, mats.darkMetal, [[x - 0.22, 0.42, 0.55], [x - 0.22, 0.55, 0.52], [x - 0.22, 0.56, 0.02], [x - 0.22, 0.44, -0.02]], 0.014, 0.05);
    cageTube(b, mats.darkMetal, [[x + 0.22, 0.42, 0.55], [x + 0.22, 0.55, 0.52], [x + 0.22, 0.56, 0.02], [x + 0.22, 0.44, -0.02]], 0.014, 0.05);
    // cushion + bolsters
    b.add(roundedBox(0.44, 0.07, 0.5, 0.03, 0.012), mats.seat, M(x, 0.575, 0.27));
    b.add(roundedBox(0.06, 0.09, 0.46, 0.025, 0.01), mats.seat, M(x - 0.21, 0.62, 0.28, 0, 0, 0.25));
    b.add(roundedBox(0.06, 0.09, 0.46, 0.025, 0.01), mats.seat, M(x + 0.21, 0.62, 0.28, 0, 0, -0.25));
    // low back (PLSS sits above it)
    b.add(roundedBox(0.44, 0.32, 0.06, 0.03, 0.012), mats.seat, M(x, 0.78, 0.53, -0.08));
    b.add(rod([x - 0.22, 0.55, 0.52], [x - 0.22, 0.96, 0.56], 0.014), mats.darkMetal);
    b.add(rod([x + 0.22, 0.55, 0.52], [x + 0.22, 0.96, 0.56], 0.014), mats.darkMetal);
    b.add(rod([x - 0.22, 0.96, 0.56], [x + 0.22, 0.96, 0.56], 0.014), mats.darkMetal);
    // lap belt (orange webbing) on the empty seat only runs flat; buckles
    b.add(box(0.05, 0.015, 0.03), mats.chrome, M(x + 0.2, 0.62, 0.1));
  }
  // sample case strapped to the passenger seat
  b.add(roundedBox(0.36, 0.24, 0.32, 0.03, 0.01), mats.orange, M(0.4, 0.74, 0.28));
  b.add(box(0.37, 0.02, 0.33), mats.darkMetal, M(0.4, 0.74, 0.28));
  for (const dx of [-0.1, 0.1]) b.add(box(0.04, 0.03, 0.02), mats.chrome, M(0.4 + dx, 0.74, 0.115));
  b.add(rod([0.33, 0.87, 0.28], [0.47, 0.87, 0.28], 0.01), mats.black);
  b.add(cable([[0.18, 0.6, 0.18], [0.24, 0.87, 0.18], [0.56, 0.87, 0.18], [0.62, 0.6, 0.18]], 0.01), mats.cableOrange);

  // ---------------------------------------------------------------- side pods + livery
  for (const s of [-1, 1]) {
    const pod = roundedBox(0.12, 0.28, 1.2, 0.04, 0.01);
    b.add(pod, mats.paint, M(s * 0.86, 0.56, 0.02));
    const liv = new THREE.PlaneGeometry(1.08, 0.27);
    b.add(liv, mats.livery, M(s * 0.921, 0.56, 0.02, 0, s * Math.PI / 2));
    for (let i = 0; i < 5; i++) bolt(s * 0.86, 0.7, -0.5 + i * 0.25);
  }

  // ---------------------------------------------------------------- fenders
  for (const s of [-1, 1]) {
    for (const za of [zf, zr]) {
      const front = za < 0;
      const a0 = front ? 0.25 : 0.55; // radians from horizontal (front side)
      const a1 = front ? Math.PI - 0.55 : Math.PI - 0.25;
      const ri = 0.555;
      const ro = 0.57;
      const shp = new THREE.Shape();
      shp.absarc(0, 0, ro, a0, a1, false);
      shp.absarc(0, 0, ri, a1, a0, true);
      const fg = new THREE.ExtrudeGeometry(shp, { depth: 0.36, bevelEnabled: true, bevelSize: 0.006, bevelThickness: 0.006, bevelSegments: 2, curveSegments: 28 });
      fg.translate(0, 0, -0.18);
      // shape x -> car -z (forward), so arc start (a0 near +x) is the front.
      b.add(fg, mats.carbon, M(s * 0.9, 0.44, za, 0, Math.PI / 2, 0));
      // orange edge trim on the outer lip
      const trimShp = new THREE.Shape();
      trimShp.absarc(0, 0, ro + 0.012, a0, a1, false);
      trimShp.absarc(0, 0, ro - 0.004, a1, a0, true);
      const tg = new THREE.ExtrudeGeometry(trimShp, { depth: 0.02, bevelEnabled: false, curveSegments: 28 });
      tg.translate(0, 0, -0.01);
      b.add(tg, mats.orange, M(s * 1.083, 0.44, za, 0, Math.PI / 2, 0));
      // mud flap at the trailing edge
      const trail = front ? a1 : a1;
      const fx = Math.cos(trail) * ri;
      const fy = Math.sin(trail) * ri;
      b.add(box(0.34, 0.16, 0.008), mats.black, M(s * 0.9, 0.44 + fy - 0.07, za - fx + 0.005, 0.1));
      // struts from fender to chassis
      const apex: V3 = [s * 0.76, 0.44 + ro * 0.97, za];
      b.add(rod(apex, [s * 0.42, 0.8, za + (front ? -0.25 : 0.25)], 0.012), mats.frame);
      bolt(apex[0] + s * 0.02, apex[1] + 0.012, apex[2]);
    }
  }

  // ---------------------------------------------------------------- rear bay
  // electronics box with panel lines and bolts
  b.add(box(0.6, 0.38, 0.62), mats.paint, M(0, 0.62, 1.05));
  boltRing([0, 0.62, 1.05], [0.6, 0.38, 0.62], 4);
  // radiator fins on the rear face
  for (let i = 0; i < 10; i++) b.add(box(0.5, 0.24, 0.006), mats.chrome, M(0, 0.62, 1.37 + i * 0.008, 0, 0, 0).multiply(M(0, 0, 0, 0, 0, 0, 1, 1, 1)));
  for (let i = 0; i < 12; i++) b.add(box(0.008, 0.26, 0.06), mats.chrome, M(-0.25 + i * (0.5 / 11), 0.62, 1.39));
  // battery #2 gold blanketed under the deck
  b.add(roundedBox(0.4, 0.14, 0.36, 0.02, 0.008), mats.gold, M(0, 0.88, 0.98));
  // rear deck plate
  b.add(box(1.0, 0.02, 0.72), mats.carbon, M(0, 0.96, 1.08));
  // solar panel (right half, tilted)
  b.add(box(0.48, 0.015, 0.62), mats.darkMetal, M(0.28, 1.04, 1.08, -0.18));
  b.add(new THREE.PlaneGeometry(0.46, 0.6), mats.solar, M(0.28, 1.049, 1.08, -Math.PI / 2 - 0.18));
  b.add(rod([0.1, 0.97, 0.82], [0.1, 1.0, 0.82], 0.01), mats.darkMetal);
  b.add(rod([0.46, 0.97, 1.33], [0.46, 1.1, 1.33], 0.01), mats.darkMetal);
  // sample bag rack (left half): rack frame + 2x4 bags
  cageTube(b, mats.frame, [[-0.06, 0.97, 0.78], [-0.06, 1.24, 0.8], [-0.62, 1.24, 0.8], [-0.62, 0.97, 0.78]], 0.01, 0.04);
  cageTube(b, mats.frame, [[-0.06, 0.97, 1.38], [-0.06, 1.24, 1.38], [-0.62, 1.24, 1.38], [-0.62, 0.97, 1.38]], 0.01, 0.04);
  b.add(rod([-0.06, 1.24, 0.8], [-0.06, 1.24, 1.38], 0.01), mats.frame);
  b.add(rod([-0.62, 1.24, 0.8], [-0.62, 1.24, 1.38], 0.01), mats.frame);
  for (let i = 0; i < 4; i++) {
    for (let j = 0; j < 2; j++) {
      const bx = -0.2 - j * 0.28;
      const bz = 0.88 + i * 0.145;
      b.add(roundedBox(0.2, 0.2, 0.11, 0.03, 0.01), i % 3 === 0 ? mats.gold : mats.dish, M(bx, 1.08, bz, 0.03 * i, 0.05 * j));
      b.add(box(0.21, 0.02, 0.03), mats.darkMetal, M(bx, 1.19, bz));
    }
  }

  // tool rack on the rear (behind the rear box): frame + tools
  cageTube(b, mats.frame, [[-0.5, 0.62, 1.5], [-0.52, 1.28, 1.5], [0.52, 1.28, 1.5], [0.5, 0.62, 1.5]], 0.014, 0.06);
  b.add(rod([-0.5, 0.95, 1.5], [0.5, 0.95, 1.5], 0.01), mats.frame);
  // shovel
  b.add(rod([-0.38, 0.66, 1.53], [-0.38, 1.36, 1.53], 0.012), mats.chrome);
  b.add(box(0.14, 0.18, 0.01), mats.chrome, M(-0.38, 0.6, 1.53));
  b.add(rod([-0.44, 1.36, 1.53], [-0.32, 1.36, 1.53], 0.01), mats.black);
  // rake
  b.add(rod([-0.18, 0.7, 1.53], [-0.18, 1.4, 1.53], 0.011), mats.chrome);
  b.add(box(0.2, 0.02, 0.02), mats.darkMetal, M(-0.18, 0.69, 1.53));
  for (let i = 0; i < 7; i++) b.add(rod([-0.27 + i * 0.03, 0.69, 1.53], [-0.27 + i * 0.03, 0.62, 1.53], 0.003, 4), mats.chrome);
  // tongs
  b.add(rod([0.05, 0.68, 1.53], [0.03, 1.32, 1.53], 0.008), mats.chrome);
  b.add(rod([0.09, 0.68, 1.53], [0.07, 1.32, 1.53], 0.008), mats.chrome);
  b.add(box(0.06, 0.08, 0.03), mats.orange, M(0.05, 1.3, 1.53));
  // hammer
  b.add(rod([0.25, 0.75, 1.53], [0.25, 1.2, 1.53], 0.012), mats.black);
  b.add(box(0.14, 0.04, 0.04), mats.chrome, M(0.25, 1.21, 1.53));
  // core tubes
  for (let i = 0; i < 3; i++) b.add(rod([0.38 + i * 0.045, 0.72, 1.53], [0.38 + i * 0.045, 1.16, 1.53], 0.017, 10), i === 1 ? mats.gold : mats.chrome);
  for (const y of [0.8, 1.1]) b.add(rod([-0.48, y, 1.545], [0.48, y, 1.545], 0.006), mats.orange); // straps

  // ---------------------------------------------------------------- antennas / mast / light bar
  // high-gain dish on a mast (front right), pointing up and back toward "Earth"
  const hgaBase: V3 = [0.42, 0.8, -1.22];
  const hgaTop: V3 = [0.46, 1.72, -1.22];
  b.add(rod(hgaBase, hgaTop, 0.022, 12), mats.frame);
  b.add(rod([0.42, 0.8, -1.32], [0.44, 1.25, -1.22], 0.01), mats.frame);
  b.add(box(0.07, 0.08, 0.07), mats.darkMetal, M(0.46, 1.73, -1.22));
  const dishGeo = new THREE.SphereGeometry(0.34, 36, 10, 0, Math.PI * 2, 0, 0.62);
  dishGeo.translate(0, -0.34, 0); // concave rim at y=0.. down to vertex at y = -(1-cos)*R? (vertex at top)
  const dishM = M(0.46, 1.86, -1.22).multiply(M(0, 0, 0, 0.75, 0, 0)).multiply(M(0, 0, 0, Math.PI));
  b.add(dishGeo, mats.dish, dishM);
  // dish rim + feed tripod + feed horn
  const rimR = 0.34 * Math.sin(0.62);
  const rimY = -0.34 + 0.34 * Math.cos(0.62);
  const rimG = new THREE.TorusGeometry(rimR, 0.008, 6, 40);
  rimG.rotateX(Math.PI / 2);
  b.add(rimG, mats.orange, dishM.clone().multiply(M(0, rimY, 0)));
  const feedLocal = new THREE.Vector3(0, rimY - 0.2, 0);
  for (let i = 0; i < 3; i++) {
    const a = (i / 3) * Math.PI * 2;
    const p = new THREE.Vector3(Math.cos(a) * rimR * 0.98, rimY, Math.sin(a) * rimR * 0.98);
    b.add(rod(p, feedLocal, 0.005, 6), mats.frame, dishM);
  }
  b.add(new THREE.CylinderGeometry(0.03, 0.02, 0.06, 12), mats.gold, dishM.clone().multiply(M(0, rimY - 0.2, 0)));
  // gimbal arm
  b.add(rod([0.46, 1.73, -1.22], [0.46, 1.8, -1.12], 0.015), mats.darkMetal);

  // low-gain helical antenna (front left)
  b.add(rod([-0.5, 0.8, -1.3], [-0.5, 1.5, -1.3], 0.012), mats.frame);
  const helix = new THREE.TubeGeometry(new HelixCurve(0.035, 0.32, 9), 180, 0.004, 5, false);
  b.add(helix, mats.chrome, M(-0.5, 1.82, -1.3));
  b.add(new THREE.CylinderGeometry(0.06, 0.06, 0.01, 20), mats.darkMetal, M(-0.5, 1.5, -1.3));
  b.add(new THREE.CylinderGeometry(0.008, 0.008, 0.34, 6), mats.orange, M(-0.5, 1.67, -1.3));

  // camera mast (front, left of centre) with pan-tilt stereo head
  b.add(rod([-0.16, 0.8, -1.42], [-0.16, 1.3, -1.42], 0.02, 12), mats.frame);
  b.add(new THREE.CylinderGeometry(0.035, 0.035, 0.05, 16), mats.darkMetal, M(-0.16, 1.32, -1.42));
  b.add(box(0.24, 0.1, 0.1), mats.paint, M(-0.16, 1.4, -1.43));
  b.add(box(0.25, 0.012, 0.11), mats.gold, M(-0.16, 1.456, -1.43));
  for (const dx of [-0.07, 0.07]) {
    const lensBarrel = new THREE.CylinderGeometry(0.025, 0.028, 0.05, 16);
    b.add(lensBarrel, mats.black, M(-0.16 + dx, 1.4, -1.5, Math.PI / 2));
    b.add(new THREE.CircleGeometry(0.019, 16), mats.screen, M(-0.16 + dx, 1.4, -1.5255, 0, Math.PI));
  }

  // LED light bar on the roof front cross bar
  const lightBarGlowPoints: THREE.Vector3[] = [];
  b.add(roundedBox(1.0, 0.08, 0.08, 0.02, 0.006), mats.darkMetal, M(0, 1.99, -0.31));
  for (let i = 0; i < 8; i++) {
    const x = -0.4375 + i * 0.125;
    b.add(new THREE.PlaneGeometry(0.1, 0.05), mats.lamp, M(x, 1.99, -0.3505, 0, Math.PI));
    lightBarGlowPoints.push(new THREE.Vector3(x, 1.99, -0.37));
  }
  for (const x of [-0.42, 0.42]) b.add(box(0.03, 0.06, 0.04), mats.darkMetal, M(x, 1.95, -0.3));

  // small radiator panels (white, tilted) on the side of the rear hoop
  for (const s of [-1, 1]) {
    b.add(box(0.008, 0.3, 0.42), mats.dish, M(s * 0.78, 1.25, 0.95, 0, 0, s * 0.12));
    for (let i = 0; i < 6; i++) b.add(box(0.012, 0.28, 0.01), mats.chrome, M(s * 0.785, 1.25, 0.77 + i * 0.07, 0, 0, s * 0.12));
    b.add(rod([s * 0.73, 1.1, 0.78], [s * 0.6, 1.2, 0.98], 0.008), mats.frame);
  }

  // ---------------------------------------------------------------- cables
  b.add(cable([[0.2, 0.78, -1.1], [0.3, 0.86, -0.95], [0.32, 0.92, -0.86]], 0.01), mats.cableOrange);
  b.add(cable([[0.4, 0.8, -1.22], [0.42, 1.0, -1.22], [0.45, 1.3, -1.22], [0.46, 1.7, -1.2]], 0.007), mats.black);
  b.add(cable([[-0.2, 0.78, -1.15], [-0.18, 0.95, -1.38], [-0.17, 1.3, -1.4]], 0.007), mats.black);
  b.add(cable([[0.1, 0.78, -0.9], [0.06, 0.92, -0.78], [0.0, 0.96, -0.74]], 0.009), mats.cableOrange);
  b.add(cable([[0.0, 0.66, -0.7], [0.0, 0.62, -0.2], [0.0, 0.62, 0.5], [0.05, 0.7, 0.74]], 0.012), mats.cableOrange);
  for (const s of [-1, 1]) {
    // hub motor power lines along the frame toward each corner
    for (const za of [zf, zr]) {
      b.add(cable([[s * 0.3, 0.46, za * 0.7], [s * 0.38, 0.5, za * 0.92], [s * 0.5, 0.48, za]], 0.008), mats.black);
    }
    b.add(cable([[s * 0.66, 0.8, -0.63], [s * 0.735, 1.15, -0.52], [s * 0.665, 1.86, -0.25], [s * 0.42, 1.95, -0.27]], 0.007), mats.black);
  }

  return { group: b.build('chassis'), bolts, lightBarGlowPoints, headlightPoints };
}
