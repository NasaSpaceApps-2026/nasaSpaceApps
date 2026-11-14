/**
 * Off-road tyre with geometric tread lugs, beadlock rim with split spokes,
 * hub, lug nuts and brake disc. Axis = X, outboard face toward +X, centred at
 * the wheel centre. Mirror with scale.x = -1 for the left side.
 */

import * as THREE from 'three';
import { Batch, M } from './geom';
import type { BuggyMaterials } from './materials';

export const WHEEL_RADIUS = 0.42;
export const TIRE_WIDTH = 0.3;

const RC = 0.384; // carcass crown radius
const RB = 0.252; // bead / rim radius
const HW = TIRE_WIDTH / 2;

function lathe(points: Array<[number, number]>, seg: number): THREE.BufferGeometry {
  // points as [radius, x]; lathe revolves around Y, then Y -> X.
  const g = new THREE.LatheGeometry(points.map(([r, x]) => new THREE.Vector2(r, x)), seg);
  g.rotateZ(-Math.PI / 2);
  return g;
}

function carcass(): THREE.BufferGeometry {
  // bead (-x) -> sidewall bulge -> shoulder -> crown -> mirror (+x); x increasing.
  const half: Array<[number, number]> = [
    [RB, HW - 0.022],
    [RB + 0.02, HW - 0.008],
    [0.3, HW + 0.004],
    [0.335, HW + 0.006],
    [0.36, HW - 0.002],
    [0.374, HW - 0.016],
    [0.381, HW - 0.035],
    [RC, HW - 0.06],
  ];
  const pts: Array<[number, number]> = [...half.map(([r, x]) => [r, -x] as [number, number]), ...half.reverse()];
  return lathe(pts, 72);
}

/** Parallelogram lug in (across, along) coords, extruded radially. */
function lug(x0: number, x1: number, len: number, skew: number, depth: number): THREE.BufferGeometry {
  const s = new THREE.Shape();
  s.moveTo(x0, -len / 2 + skew * x0);
  s.lineTo(x1, -len / 2 + skew * x1);
  s.lineTo(x1, len / 2 + skew * x1);
  s.lineTo(x0, len / 2 + skew * x0);
  s.closePath();
  const bev = 0.005;
  const g = new THREE.ExtrudeGeometry(s, { depth: depth - 2 * bev, bevelEnabled: true, bevelThickness: bev, bevelSize: bev, bevelSegments: 1, curveSegments: 1 });
  g.translate(0, 0, bev);
  g.rotateX(-Math.PI / 2); // extrusion -> +Y (radial)
  return g;
}

export function buildWheelGeometry(mats: BuggyMaterials): THREE.Group {
  const b = new Batch();
  b.add(carcass(), mats.tire);

  // Tread: two staggered rows of angled blocks + centre blocks, embedded into
  // the carcass so the shoulder overhang has no gap.
  const N = 18;
  const base = 0.355;
  const depth = WHEEL_RADIUS - base;
  for (let i = 0; i < N; i++) {
    const th = (i / N) * Math.PI * 2;
    const th2 = th + Math.PI / N;
    b.add(lug(-HW - 0.006, -0.028, 0.072, 0.32, depth), mats.tire, M(0, 0, 0, th).multiply(M(0, base, 0)));
    b.add(lug(0.028, HW + 0.006, 0.072, -0.32, depth), mats.tire, M(0, 0, 0, th2).multiply(M(0, base, 0)));
    // centre bridge blocks (slightly lower)
    b.add(lug(-0.04, 0.012, 0.04, 0, depth - 0.008), mats.tire, M(0, 0, 0, th + Math.PI / N / 2).multiply(M(0, base, 0)));
    // sidewall shoulder knobs (both faces), give the chunky silhouette
    for (const sx of [-1, 1]) {
      const kn = new THREE.BoxGeometry(0.018, 0.05, 0.05);
      b.add(kn, mats.tire, M(0, 0, 0, (sx < 0 ? th : th2)).multiply(M(sx * (HW + 0.004), 0.345, 0)));
    }
  }

  // Rim barrel with flanges (inside the tyre, flange visible at the bead)
  b.add(
    lathe(
      [
        [RB + 0.022, -HW + 0.005],
        [RB - 0.005, -HW + 0.012],
        [RB - 0.012, -HW + 0.03],
        [RB - 0.012, HW - 0.03],
        [RB - 0.005, HW - 0.012],
        [RB + 0.024, HW - 0.004],
        [RB + 0.03, HW + 0.006],
      ],
      48,
    ),
    mats.rim,
  );
  // Beadlock ring on the outboard face
  const ring = new THREE.RingGeometry(RB - 0.01, RB + 0.034, 48, 1);
  ring.rotateY(Math.PI / 2);
  b.add(ring, mats.orange, M(HW + 0.0075, 0, 0));
  const ringBack = new THREE.RingGeometry(RB - 0.01, RB + 0.034, 48, 1);
  ringBack.rotateY(-Math.PI / 2);
  b.add(ringBack, mats.rim, M(HW + 0.0065, 0, 0));
  // beadlock bolts
  for (let i = 0; i < 20; i++) {
    const a = (i / 20) * Math.PI * 2;
    const bolt = new THREE.CylinderGeometry(0.007, 0.007, 0.01, 6);
    bolt.rotateZ(Math.PI / 2);
    b.add(bolt, mats.bolt, M(HW + 0.012, Math.cos(a) * (RB + 0.018), Math.sin(a) * (RB + 0.018)));
  }

  // Split spokes (concave dish): hub at x=0.10, barrel at x=0.06
  const spokes = 6;
  for (let i = 0; i < spokes; i++) {
    for (const off of [-0.11, 0.11]) {
      const a = (i / spokes) * Math.PI * 2 + off;
      const a0 = (i / spokes) * Math.PI * 2 + off * 0.35;
      const p0 = new THREE.Vector3(0.1, Math.cos(a0) * 0.075, Math.sin(a0) * 0.075);
      const p1 = new THREE.Vector3(0.055, Math.cos(a) * (RB - 0.01), Math.sin(a) * (RB - 0.01));
      const d = p1.clone().sub(p0);
      const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), d.clone().normalize());
      const m = new THREE.Matrix4().compose(p0.clone().add(p1).multiplyScalar(0.5), q, new THREE.Vector3(1, d.length(), 1));
      b.add(new THREE.BoxGeometry(0.03, 1, 0.026), mats.rim, m);
    }
  }
  // Hub, centre cap, lug nuts
  const hub = new THREE.CylinderGeometry(0.085, 0.09, 0.07, 24);
  hub.rotateZ(Math.PI / 2);
  b.add(hub, mats.rim, M(0.085, 0, 0));
  const cap = new THREE.SphereGeometry(0.05, 20, 10, 0, Math.PI * 2, 0, Math.PI / 2);
  cap.rotateZ(-Math.PI / 2);
  b.add(cap, mats.chrome, M(0.12, 0, 0, 0, 0, 0, 0.5, 1, 1));
  const capRing = new THREE.TorusGeometry(0.052, 0.006, 6, 24);
  capRing.rotateY(Math.PI / 2);
  b.add(capRing, mats.orange, M(0.121, 0, 0));
  for (let i = 0; i < 6; i++) {
    const a = (i / 6) * Math.PI * 2 + 0.26;
    const nut = new THREE.CylinderGeometry(0.009, 0.009, 0.016, 6);
    nut.rotateZ(Math.PI / 2);
    b.add(nut, mats.bolt, M(0.127, Math.cos(a) * 0.068, Math.sin(a) * 0.068));
  }
  // Brake disc (spins with the wheel), drilled look via ring + inner hat
  const disc = new THREE.CylinderGeometry(0.19, 0.19, 0.014, 40, 1, false);
  disc.rotateZ(Math.PI / 2);
  b.add(disc, mats.chrome, M(-0.02, 0, 0));
  const hat = new THREE.CylinderGeometry(0.1, 0.1, 0.1, 24);
  hat.rotateZ(Math.PI / 2);
  b.add(hat, mats.darkMetal, M(0.02, 0, 0));
  return b.build('wheel');
}
