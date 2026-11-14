/**
 * Seated astronaut (modern EVA suit) in the buggy's left seat, hands on the
 * T-handle controller. Built from primitives in buggy model space and merged
 * per material. Returned group origin = buggy origin (so bobbing is a small
 * y offset).
 */

import * as THREE from 'three';
import { HC } from './chassis';
import { Batch, M, between, roundedBox } from './geom';
import type { BuggyMaterials } from './materials';

const Z = new THREE.Vector3(0, 0, 1);

/** Two-bone IK: elbow/knee position from root, target, lengths and a pole. */
function ik(S: THREE.Vector3, W: THREE.Vector3, L1: number, L2: number, pole: THREE.Vector3): THREE.Vector3 {
  const d = W.clone().sub(S);
  const dist = Math.min(d.length(), L1 + L2 - 1e-3);
  const dir = d.normalize();
  const a = (L1 * L1 - L2 * L2 + dist * dist) / (2 * dist);
  const h = Math.sqrt(Math.max(0, L1 * L1 - a * a));
  const p = pole.clone().sub(dir.clone().multiplyScalar(pole.dot(dir))).normalize();
  return S.clone().add(dir.multiplyScalar(a)).add(p.multiplyScalar(h));
}

export interface AstronautResult {
  group: THREE.Group;
  head: THREE.Group;
}

export function buildAstronaut(mats: BuggyMaterials): AstronautResult {
  const b = new Batch();
  const hb = new Batch(); // head (helmet) can nod separately
  const X0 = -0.4;

  const limb = (A: THREE.Vector3, B: THREE.Vector3, rA: number, rB: number, mat = mats.suit, joint = true) => {
    const g = new THREE.CylinderGeometry(rB, rA, 1, 18, 1, true);
    b.add(g, mat, between(A, B));
    if (joint) b.add(new THREE.SphereGeometry(rA * 1.02, 18, 12), mat, M(A.x, A.y, A.z));
  };
  const ring = (P: THREE.Vector3, dir: THREE.Vector3, R: number, t: number, mat: THREE.Material, batch = b, w = 0) => {
    const q = new THREE.Quaternion().setFromUnitVectors(Z, dir.clone().normalize());
    const m = new THREE.Matrix4().compose(P, q, new THREE.Vector3(1, 1, 1));
    batch.add(new THREE.TorusGeometry(R, t, 8, 28), mat, m);
    if (w > 0) {
      const c = new THREE.CylinderGeometry(R, R, w, 28, 1, true);
      c.rotateX(Math.PI / 2);
      batch.add(c, mat, m);
    }
  };
  /** seams / bands along a limb at fractions */
  const bands = (A: THREE.Vector3, B: THREE.Vector3, rA: number, rB: number, fr: number[], mat: THREE.Material, t = 0.004, grow = 0.002) => {
    const d = B.clone().sub(A);
    for (const f of fr) ring(A.clone().add(d.clone().multiplyScalar(f)), d, rA + (rB - rA) * f + grow, t, mat);
  };

  // ------------------------------------------------------------ pelvis & torso
  const pelvis = new THREE.Vector3(X0, 0.745, 0.29);
  b.add(new THREE.SphereGeometry(1, 24, 16), mats.suit, M(pelvis.x, pelvis.y, pelvis.z, 0, 0, 0, 0.19, 0.13, 0.17));
  // brief / waist bearing
  const waist = new THREE.Vector3(X0, 0.86, 0.32);
  ring(waist, new THREE.Vector3(0, 1, -0.12), 0.165, 0.012, mats.bearing, b, 0.02);
  // lower torso (soft) up to the hard upper torso
  const chest = new THREE.Vector3(X0, 1.19, 0.335);
  b.add(new THREE.CylinderGeometry(0.17, 0.16, 1, 22, 1, true), mats.suit, between(waist, new THREE.Vector3(X0, 1.08, 0.35)).multiply(M(0, 0, 0, 0, 0, 0, 1.15, 1, 0.95)));
  // hard upper torso (HUT)
  b.add(new THREE.SphereGeometry(1, 28, 18), mats.helmet, M(chest.x, chest.y, chest.z, -0.08, 0, 0, 0.235, 0.21, 0.175));
  // HUT seam & shoulder yokes
  ring(new THREE.Vector3(X0, 1.12, 0.36), new THREE.Vector3(0, 1, -0.1), 0.2, 0.005, mats.suitSeam);
  // display & control module (DCM) on the chest
  b.add(roundedBox(0.2, 0.09, 0.07, 0.015), mats.paint, M(X0, 1.1, 0.165, -0.15));
  for (let i = 0; i < 4; i++) {
    const k = new THREE.CylinderGeometry(0.009, 0.009, 0.02, 8);
    b.add(k, i === 0 ? mats.accentRed : mats.darkMetal, M(X0 - 0.06 + i * 0.04, 1.12, 0.128, Math.PI / 2 - 0.15));
  }
  b.add(new THREE.PlaneGeometry(0.06, 0.03), mats.screen, M(X0 + 0.05, 1.085, 0.1295, -0.15, Math.PI));
  // single umbilical from the DCM down to the right hip
  b.add(new THREE.TubeGeometry(new THREE.CatmullRomCurve3([
    new THREE.Vector3(X0 + 0.09, 1.08, 0.16),
    new THREE.Vector3(X0 + 0.17, 0.98, 0.2),
    new THREE.Vector3(X0 + 0.19, 0.88, 0.3),
  ]), 12, 0.011, 6), mats.suitSeam);
  // mission patch (left chest) — abstract ARES patch
  {
    const n = new THREE.Vector3(-0.45, 0.25, -0.86).normalize();
    const rads = new THREE.Vector3(0.235, 0.21, 0.175);
    const p = new THREE.Vector3(n.x * rads.x, n.y * rads.y, n.z * rads.z);
    const nn = new THREE.Vector3(n.x / rads.x, n.y / rads.y, n.z / rads.z).normalize();
    const pos = chest.clone().add(p.applyAxisAngle(new THREE.Vector3(1, 0, 0), -0.08)).add(nn.clone().multiplyScalar(0.004));
    const q = new THREE.Quaternion().setFromUnitVectors(Z, nn.applyAxisAngle(new THREE.Vector3(1, 0, 0), -0.08));
    b.add(new THREE.CircleGeometry(0.045, 24), mats.patch, new THREE.Matrix4().compose(pos, q, new THREE.Vector3(1, 1, 1)));
  }

  // ------------------------------------------------------------ PLSS backpack
  const plss = new THREE.Vector3(X0, 1.24, 0.64);
  b.add(roundedBox(0.46, 0.56, 0.2, 0.05, 0.012), mats.paint, M(plss.x, plss.y, plss.z, -0.06));
  b.add(roundedBox(0.4, 0.12, 0.05, 0.02, 0.006), mats.darkMetal, M(plss.x, plss.y + 0.18, plss.z + 0.1, -0.06));
  b.add(roundedBox(0.4, 0.16, 0.05, 0.02, 0.006), mats.gold, M(plss.x, plss.y - 0.12, plss.z + 0.105, -0.06));
  for (let i = 0; i < 6; i++) b.add(new THREE.BoxGeometry(0.38, 0.008, 0.01), mats.chrome, M(plss.x, plss.y + 0.13 + i * 0.018, plss.z + 0.126, -0.06));
  // antenna nub & handles
  b.add(new THREE.CylinderGeometry(0.008, 0.008, 0.12, 6), mats.darkMetal, M(plss.x + 0.17, plss.y + 0.33, plss.z + 0.03));
  for (const s of [-1, 1]) b.add(new THREE.TorusGeometry(0.04, 0.008, 6, 12, Math.PI), mats.darkMetal, M(plss.x + s * 0.24, plss.y + 0.05, plss.z, 0, Math.PI / 2, Math.PI / 2));

  // ------------------------------------------------------------ legs
  for (const s of [-1, 1]) {
    const hip = new THREE.Vector3(X0 + s * 0.1, 0.73, 0.25);
    const ankle = new THREE.Vector3(X0 + s * 0.13, 0.5, -0.4);
    const knee = ik(hip, ankle, 0.45, 0.44, new THREE.Vector3(s * 0.15, 1, -0.5));
    // hip bearing
    ring(hip.clone().add(new THREE.Vector3(s * 0.02, 0.0, -0.05)), knee.clone().sub(hip), 0.09, 0.01, mats.bearing, b, 0.02);
    limb(hip, knee, 0.088, 0.072);
    limb(knee, ankle, 0.072, 0.058);
    // knee bearing (just above the knee joint)
    const kd = knee.clone().sub(hip);
    ring(knee.clone().sub(kd.clone().normalize().multiplyScalar(0.06)), kd, 0.079, 0.009, mats.bearing, b, 0.018);
    // thigh band (commander ID stripe) + seams
    bands(hip, knee, 0.088, 0.072, [0.5], mats.accentRed, 0.012, 0.004);
    bands(hip, knee, 0.088, 0.072, [0.3, 0.72], mats.suitSeam);
    bands(knee, ankle, 0.072, 0.058, [0.3, 0.55], mats.suitSeam);
    // boots: shaft + foot + sole
    ring(ankle.clone().add(new THREE.Vector3(0, 0.02, 0)), new THREE.Vector3(0, 1, 0.3), 0.064, 0.01, mats.bearing, b, 0.02);
    b.add(new THREE.CylinderGeometry(0.068, 0.072, 0.12, 18), mats.boot, M(ankle.x, ankle.y - 0.03, ankle.z, 0.35));
    b.add(roundedBox(0.12, 0.1, 0.28, 0.04, 0.012), mats.boot, M(ankle.x, 0.47, ankle.z - 0.09, 0.9));
    b.add(roundedBox(0.13, 0.025, 0.3, 0.012, 0.006), mats.black, M(ankle.x, 0.44, ankle.z - 0.11, 0.9));
    b.add(roundedBox(0.125, 0.05, 0.06, 0.02, 0.006), mats.accentRed, M(ankle.x, 0.53, ankle.z - 0.165, 0.9));
  }

  // ------------------------------------------------------------ arms (to the T-handle)
  const hc = HC;
  for (const s of [-1, 1]) {
    const sh = new THREE.Vector3(X0 + s * 0.215, 1.29, 0.33);
    const grip = new THREE.Vector3(hc.x + s * (hc.half - 0.04), hc.y, hc.z);
    const wrist = grip.clone().add(new THREE.Vector3(-s * 0.0, 0.035, 0.07));
    const elbow = ik(sh, wrist, 0.33, 0.3, new THREE.Vector3(s * 1, -0.9, 0.4));
    // shoulder bearing (scye) ring: axis points outward along upper arm root
    const ua = elbow.clone().sub(sh);
    ring(sh.clone().add(ua.clone().normalize().multiplyScalar(0.035)), ua, 0.083, 0.012, mats.bearing, b, 0.025);
    b.add(new THREE.SphereGeometry(0.085, 18, 12), mats.helmet, M(sh.x, sh.y, sh.z)); // HUT shoulder cup
    limb(sh, elbow, 0.074, 0.062);
    limb(elbow, wrist, 0.062, 0.05);
    // upper arm bearing + elbow + wrist bearings
    ring(sh.clone().add(ua.clone().multiplyScalar(0.45)), ua, 0.074, 0.009, mats.bearing, b, 0.016);
    const fa = wrist.clone().sub(elbow);
    ring(elbow.clone().add(ua.clone().normalize().multiplyScalar(-0.045)), ua, 0.068, 0.008, mats.bearing, b, 0.014);
    ring(wrist.clone().sub(fa.clone().normalize().multiplyScalar(0.01)), fa, 0.054, 0.011, mats.bearing, b, 0.022);
    bands(sh, elbow, 0.074, 0.062, [0.25], mats.accentRed, 0.01, 0.004);
    bands(elbow, wrist, 0.062, 0.05, [0.3, 0.6], mats.suitSeam);
    // left shoulder patch (outer face of the upper arm)
    if (s < 0) {
      const mid = sh.clone().add(ua.clone().multiplyScalar(0.25));
      const out = new THREE.Vector3(-1, 0.1, 0).sub(ua.clone().normalize().multiplyScalar(new THREE.Vector3(-1, 0.1, 0).dot(ua.clone().normalize()))).normalize();
      const pos = mid.add(out.clone().multiplyScalar(0.074));
      const q = new THREE.Quaternion().setFromUnitVectors(Z, out);
      b.add(new THREE.CircleGeometry(0.035, 20), mats.patch, new THREE.Matrix4().compose(pos, q, new THREE.Vector3(1, 1, 1)));
    }
    // glove: gauntlet, back of hand, fingers wrapped around the grip bar (X axis)
    b.add(new THREE.CylinderGeometry(0.056, 0.05, 0.05, 16), mats.glove, between(wrist.clone().sub(fa.clone().normalize().multiplyScalar(0.03)), wrist.clone().add(fa.clone().normalize().multiplyScalar(0.015))));
    const palm = wrist.clone().lerp(grip, 0.55);
    const hd = grip.clone().sub(wrist);
    const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, -1), hd.clone().normalize());
    b.add(roundedBox(0.09, 0.04, 0.09, 0.018, 0.006), mats.glove, new THREE.Matrix4().compose(palm, q, new THREE.Vector3(1, 1, 1)));
    const fingers = new THREE.TorusGeometry(0.034, 0.019, 8, 14, Math.PI * 1.3);
    fingers.rotateY(Math.PI / 2);
    b.add(fingers, mats.glove, M(grip.x, grip.y, grip.z, -0.4 + Math.PI, 0, 0, 1.3, 1, 1));
    // thumb over the bar
    b.add(new THREE.CapsuleGeometry(0.014, 0.04, 4, 8), mats.glove, M(grip.x - s * 0.045, grip.y + 0.02, grip.z - 0.01, 0, 0, Math.PI / 2));
  }

  // ------------------------------------------------------------ helmet (separate group, nods)
  const neck = new THREE.Vector3(X0, 1.42, 0.35);
  ring(neck, new THREE.Vector3(0, 1, 0.12), 0.125, 0.018, mats.bearing, b, 0.04);
  b.add(new THREE.CylinderGeometry(0.125, 0.15, 0.06, 28), mats.helmet, M(neck.x, neck.y - 0.035, neck.z, 0.12));
  const H = new THREE.Vector3(0, 0.135, -0.005); // helmet centre relative to the neck pivot
  hb.add(new THREE.SphereGeometry(0.168, 36, 24), mats.helmet, M(H.x, H.y, H.z));
  // gold visor: front patch, slightly larger
  const visor = new THREE.SphereGeometry(0.174, 40, 24, Math.PI * 1.5 - 1.25, 2.5, 0.42, 1.38);
  hb.add(visor, mats.visor, M(H.x, H.y, H.z));
  // visor frame (EVVA shell) around the visor
  const brow = new THREE.SphereGeometry(0.181, 40, 4, Math.PI * 1.5 - 1.3, 2.6, 0.33, 0.12);
  hb.add(brow, mats.helmet, M(H.x, H.y, H.z));
  const chin = new THREE.SphereGeometry(0.179, 40, 4, Math.PI * 1.5 - 1.3, 2.6, 1.76, 0.12);
  hb.add(chin, mats.helmet, M(H.x, H.y, H.z));
  // shell over top/back (outer EVVA) leaves the visor open
  const shell = new THREE.SphereGeometry(0.18, 36, 18, Math.PI * 1.5 + 1.15, Math.PI * 2 - 2.3, 0.0, 1.95);
  hb.add(shell, mats.helmet, M(H.x, H.y, H.z));
  // visor pivot hubs
  for (const s of [-1, 1]) {
    const hub = new THREE.CylinderGeometry(0.03, 0.03, 0.02, 16);
    hb.add(hub, mats.suitSeam, M(H.x + s * 0.178, H.y - 0.01, H.z + 0.02, 0, 0, Math.PI / 2));
    // helmet lights (HDL): housing + emissive lens, on the sides, pointing forward
    const lp = new THREE.Vector3(H.x + s * 0.17, H.y + 0.07, H.z + 0.02);
    hb.add(roundedBox(0.036, 0.04, 0.11, 0.01, 0.004), mats.darkMetal, M(lp.x, lp.y, lp.z));
    hb.add(new THREE.CircleGeometry(0.012, 12), mats.helmetLamp, M(lp.x, lp.y + 0.006, lp.z - 0.0555, 0, Math.PI, 0));
  }

  const group = b.build('astronaut');
  const head = hb.build('helmet');
  head.position.copy(neck);
  head.rotation.x = -0.06;
  group.add(head);
  return { group, head };
}

