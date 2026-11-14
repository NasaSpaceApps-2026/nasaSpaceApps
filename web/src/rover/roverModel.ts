/**
 * Simple Perseverance-like rover mesh from three.js primitives.
 *
 * Model space: origin on the ground at the body centre, +Y up, forward = -Z,
 * right = +X, metres. Approximate dimensions: 3.0 m long, 2.7 m wide, mast top
 * ~2.2 m, wheels 0.525 m diameter on a 2.7 m wheelbase / 2.3 m track. It is an
 * illustration, not an engineering model.
 */

import * as THREE from 'three';
import { poseToEulerYXZ, siteToThree, type SitePose } from './coords';
import { DEFAULT_ROVER_PARAMS } from './physics';

export interface RoverModelOptions {
  wheelbase_m?: number;
  track_m?: number;
  bodyColor?: THREE.ColorRepresentation;
}

export function createRoverModel(opts: RoverModelOptions = {}): THREE.Group {
  const wb = opts.wheelbase_m ?? DEFAULT_ROVER_PARAMS.wheelbase_m;
  const tr = opts.track_m ?? DEFAULT_ROVER_PARAMS.track_m;
  const wheelR = 0.2625;

  const body = new THREE.MeshStandardMaterial({ color: opts.bodyColor ?? 0xd9d6cf, roughness: 0.7, metalness: 0.15 });
  const dark = new THREE.MeshStandardMaterial({ color: 0x3a3a3c, roughness: 0.8, metalness: 0.3 });
  const metal = new THREE.MeshStandardMaterial({ color: 0x9a9890, roughness: 0.5, metalness: 0.6 });
  const wheelMat = new THREE.MeshStandardMaterial({ color: 0x6d6a66, roughness: 0.9, metalness: 0.2 });

  const g = new THREE.Group();
  g.name = 'rover';

  const add = (geo: THREE.BufferGeometry, mat: THREE.Material, x: number, y: number, z: number, parent: THREE.Object3D = g) => {
    const m = new THREE.Mesh(geo, mat);
    m.position.set(x, y, z);
    m.castShadow = true;
    m.receiveShadow = true;
    parent.add(m);
    return m;
  };

  // Chassis (warm electronics box) and deck.
  add(new THREE.BoxGeometry(1.8, 0.6, 2.4), body, 0, 0.95, 0);
  add(new THREE.BoxGeometry(1.9, 0.05, 2.5), metal, 0, 1.27, 0);

  // RTG at the rear, tilted up.
  const rtg = add(new THREE.CylinderGeometry(0.3, 0.3, 0.7, 16), dark, 0, 1.15, 1.45);
  rtg.rotation.x = Math.PI / 2 - 0.5;

  // Remote sensing mast (front right) with head.
  add(new THREE.CylinderGeometry(0.06, 0.07, 0.95, 10), metal, 0.55, 1.75, -0.95);
  add(new THREE.BoxGeometry(0.5, 0.22, 0.25), body, 0.55, 2.25, -0.95);
  add(new THREE.BoxGeometry(0.12, 0.08, 0.04), dark, 0.42, 2.25, -1.09);
  add(new THREE.BoxGeometry(0.12, 0.08, 0.04), dark, 0.68, 2.25, -1.09);

  // High-gain antenna disc on the deck.
  const hga = add(new THREE.CylinderGeometry(0.28, 0.28, 0.04, 20), metal, -0.6, 1.45, 0.35);
  hga.rotation.z = 0.4;

  // Stowed robotic arm across the front.
  add(new THREE.BoxGeometry(1.5, 0.1, 0.1), metal, 0, 0.85, -1.3);
  add(new THREE.BoxGeometry(0.3, 0.25, 0.25), dark, -0.65, 0.85, -1.35);

  // Rocker-bogie rails and six wheels.
  const half = tr / 2;
  const axles = [-wb / 2, 0, wb / 2];
  const wheelGeo = new THREE.CylinderGeometry(wheelR, wheelR, 0.4, 20);
  wheelGeo.rotateZ(Math.PI / 2);
  for (const side of [-1, 1]) {
    const rail = add(new THREE.BoxGeometry(0.08, 0.08, wb + 0.2), metal, side * half, 0.62, 0);
    rail.rotation.x = 0.04;
    for (const z of axles) {
      add(new THREE.BoxGeometry(0.06, 0.4, 0.06), metal, side * half, 0.45, z);
      const w = add(wheelGeo, wheelMat, side * half, wheelR, z);
      w.name = `wheel_${side < 0 ? 'L' : 'R'}_${z < 0 ? 'front' : z > 0 ? 'rear' : 'mid'}`;
    }
  }

  return g;
}

/** Place a three.js object at a site-frame pose (Contract 1 mapping). */
export function applyPose(obj: THREE.Object3D, pose: SitePose): void {
  const [X, Y, Z] = siteToThree(pose.x, pose.y, pose.z);
  obj.position.set(X, Y, Z);
  const [ex, ey, ez] = poseToEulerYXZ(pose.heading_deg, pose.pitch_deg, pose.roll_deg);
  obj.rotation.set(ex, ey, ez, 'YXZ');
}
