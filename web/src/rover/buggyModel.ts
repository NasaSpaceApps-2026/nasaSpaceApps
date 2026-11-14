/**
 * Crewed Mars buggy ("Ares Atlas"): an LRV-inspired, off-road-racing styled
 * two-seat buggy with an astronaut driver, built procedurally from three.js
 * primitives. No external assets.
 *
 * Model space (same as roverModel.ts): origin on the ground at the centre,
 * +Y up, forward = -Z, right = +X, metres. 3.3 m long over bumpers, 2.17 m
 * wide over the fenders, wheelbase 2.3 m, track 1.8 m, wheels 0.84 m diameter.
 * `applyPose(group, pose)` from roverModel.ts places it.
 *
 * `update()` spins and steers the wheels (4-wheel steering, rear opposite
 * phase like the LRV), moves the double-wishbone suspension and coil-overs,
 * bobs the astronaut, kicks up dust proportional to speed, and on boost
 * shows a rear dust plume and a brighter light bar.
 */

import * as THREE from 'three';
import { buildAstronaut } from './buggy/astronaut';
import { SUSP, TRACK, WHEELBASE, buildChassis } from './buggy/chassis';
import { DustSystem } from './buggy/dust';
import { Batch, HelixCurve, M, roundedBox, rod } from './buggy/geom';
import { createMaterials, type BuggyMaterials } from './buggy/materials';
import { softSprite } from './buggy/textures';
import { WHEEL_RADIUS, buildWheelGeometry } from './buggy/wheel';

export interface BuggyAnimState {
  /** Signed ground speed shown on screen, m/s (positive = forward). */
  speedMps: number;
  /** Steering angle, degrees; positive = turning right. */
  steerDeg: number;
  boost: boolean;
  /** Wall-clock seconds since the last update. */
  dt: number;
  /** Per-wheel suspension input FL, FR, RL, RR in metres, positive = wheel pushed up. */
  wheelGroundOffsets?: [number, number, number, number];
}

export interface BuggyModel {
  group: THREE.Group;
  update(s: BuggyAnimState): void;
  dispose(): void;
}

export const BUGGY_DIMENSIONS = { wheelbase_m: WHEELBASE, track_m: TRACK, wheel_radius_m: WHEEL_RADIUS, length_m: 3.3, width_m: 2.17 };

const SHOCK_LEN0 = 0.6; // reference length of the spring mesh
const ARM_L = SUSP.outerX - SUSP.innerX;
const MAX_TRAVEL = 0.12;

interface Corner {
  s: number; // -1 left, +1 right
  za: number;
  front: boolean;
  upper: THREE.Group;
  lower: THREE.Group;
  upright: THREE.Group;
  steer: THREE.Group;
  spin: THREE.Group;
  shock: THREE.Group;
  spring: THREE.Object3D;
  shaft: THREE.Object3D;
  tieRod: THREE.Object3D;
  offset: number;
}

function armGroup(mats: BuggyMaterials, s: number, lower: boolean, shadows: boolean): THREE.Group {
  const b = new Batch();
  const L = ARM_L;
  const sp = SUSP.armSpread;
  const r = lower ? 0.017 : 0.014;
  b.add(rod([0, 0, -sp], [s * L, 0, 0], r, 10), mats.darkMetal);
  b.add(rod([0, 0, sp], [s * L, 0, 0], r, 10), mats.darkMetal);
  b.add(rod([s * L * 0.35, 0, -sp * 0.65], [s * L * 0.35, 0, sp * 0.65], r * 0.8, 8), mats.darkMetal);
  for (const z of [-sp, sp]) {
    const bush = new THREE.CylinderGeometry(0.022, 0.022, 0.05, 12);
    b.add(bush, mats.orange, M(0, 0, z, Math.PI / 2));
  }
  b.add(new THREE.SphereGeometry(0.026, 12, 8), mats.chrome, M(s * L, 0, 0));
  if (lower) {
    b.add(new THREE.BoxGeometry(0.04, 0.05, 0.06), mats.darkMetal, M(s * L * SUSP.shockLowFrac, 0.025, 0));
    // CV / drive shaft boot from the frame to the hub
  }
  const g = b.build(lower ? 'lowerArm' : 'upperArm', shadows);
  return g;
}

function knuckleGroup(mats: BuggyMaterials, s: number, shadows: boolean): THREE.Group {
  const b = new Batch();
  // upright body
  b.add(roundedBox(0.05, 0.31, 0.09, 0.02, 0.005), mats.darkMetal, M(0, 0.04, 0));
  b.add(new THREE.SphereGeometry(0.03, 12, 8), mats.chrome, M(-s * 0.02, SUSP.upperY - WHEEL_RADIUS, 0));
  b.add(new THREE.SphereGeometry(0.03, 12, 8), mats.chrome, M(-s * 0.02, SUSP.lowerY - WHEEL_RADIUS, 0));
  // spindle into the hub
  b.add(rod([0, 0, 0], [s * 0.17, 0, 0], 0.035, 14), mats.chrome);
  // hub traction drive (LRV-style hub motor), gold-foil wrapped
  const motor = new THREE.CylinderGeometry(0.11, 0.11, 0.09, 24);
  b.add(motor, mats.gold, M(s * 0.075, 0, 0, 0, 0, Math.PI / 2));
  const cap = new THREE.CylinderGeometry(0.115, 0.115, 0.012, 24);
  b.add(cap, mats.darkMetal, M(s * 0.03, 0, 0, 0, 0, Math.PI / 2));
  b.add(cap.clone(), mats.darkMetal, M(s * 0.12, 0, 0, 0, 0, Math.PI / 2));
  // brake caliper (orange) straddling the disc
  b.add(roundedBox(0.05, 0.09, 0.12, 0.015, 0.005), mats.orange, M(s * 0.16, 0.12, 0.11, -0.75));
  // steering arm toward the tie rod
  b.add(rod([0, 0.02, 0], [0, 0.02, 0.14], 0.014, 8), mats.darkMetal);
  b.add(new THREE.SphereGeometry(0.02, 10, 8), mats.chrome, M(0, 0.02, 0.14));
  return b.build('knuckle', shadows);
}

function shockGroup(mats: BuggyMaterials, shadows: boolean): { shock: THREE.Group; spring: THREE.Object3D; shaft: THREE.Object3D } {
  // local: top mount at origin, axis along -Y
  const body = new Batch();
  body.add(new THREE.CylinderGeometry(0.03, 0.03, 0.3, 16), mats.chrome, M(0, -0.17, 0));
  body.add(new THREE.CylinderGeometry(0.04, 0.04, 0.03, 16), mats.orange, M(0, -0.05, 0)); // top spring perch
  body.add(new THREE.TorusGeometry(0.018, 0.008, 8, 14), mats.darkMetal, M(0, 0, 0, 0, Math.PI / 2, 0));
  body.add(new THREE.CylinderGeometry(0.022, 0.022, 0.16, 12), mats.orange, M(0.045, -0.12, 0.0)); // piggyback reservoir
  body.add(rod([0, -0.05, 0], [0.045, -0.05, 0], 0.01, 8), mats.darkMetal);
  const shock = body.build('shock', shadows);
  const helix = new THREE.TubeGeometry(new HelixCurve(0.048, SHOCK_LEN0 - 0.1, 8), 128, 0.0075, 5, false);
  helix.translate(0, -0.05, 0);
  const springB = new Batch().add(helix, mats.orange);
  const spring = springB.build('spring', shadows);
  const shaftB = new Batch();
  shaftB.add(new THREE.CylinderGeometry(0.012, 0.012, 0.3, 10), mats.chrome, M(0, 0.15, 0));
  shaftB.add(new THREE.CylinderGeometry(0.04, 0.04, 0.025, 16), mats.darkMetal, M(0, 0.05, 0)); // lower perch
  shaftB.add(new THREE.TorusGeometry(0.018, 0.008, 8, 14), mats.darkMetal, M(0, 0, 0, 0, Math.PI / 2, 0));
  const shaft = shaftB.build('shaft', shadows);
  shock.add(spring, shaft);
  return { shock, spring, shaft };
}

export function createBuggyModel(opts: { castShadow?: boolean } = {}): BuggyModel {
  const shadows = opts.castShadow ?? true;
  const mats = createMaterials();
  const group = new THREE.Group();
  group.name = 'buggy';

  // ---------------------------------------------------------------- chassis + bolts
  const ch = buildChassis(mats);
  if (!shadows) ch.group.traverse((o) => ((o as THREE.Mesh).castShadow = false));
  group.add(ch.group);
  const boltGeo = new THREE.CylinderGeometry(0.0085, 0.0085, 0.007, 6);
  boltGeo.translate(0, 0.0035, 0);
  const bolts = new THREE.InstancedMesh(boltGeo, mats.bolt, ch.bolts.length);
  ch.bolts.forEach((m, i) => bolts.setMatrixAt(i, m));
  bolts.instanceMatrix.needsUpdate = true;
  bolts.name = 'buggy:bolts';
  bolts.receiveShadow = true;
  group.add(bolts);

  // ---------------------------------------------------------------- corners
  const wheelProto = buildWheelGeometry(mats);
  const corners: Corner[] = [];
  const tieRodGeo = new THREE.CylinderGeometry(0.012, 0.012, 1, 8);
  for (const [front, s] of [[true, -1], [true, 1], [false, -1], [false, 1]] as Array<[boolean, number]>) {
    const za = front ? -WHEELBASE / 2 : WHEELBASE / 2;
    const upper = armGroup(mats, s, false, shadows);
    upper.position.set(s * SUSP.innerX, SUSP.upperY, za);
    const lower = armGroup(mats, s, true, shadows);
    lower.position.set(s * SUSP.innerX, SUSP.lowerY, za);
    const upright = new THREE.Group();
    upright.position.set(s * SUSP.uprightX, WHEEL_RADIUS, za);
    const steer = new THREE.Group();
    steer.add(knuckleGroup(mats, s, shadows));
    const spin = new THREE.Group();
    spin.position.x = s * (TRACK / 2 - SUSP.uprightX);
    const wheel = wheelProto.clone();
    wheel.scale.x = s; // outboard face out on both sides
    wheel.traverse((o) => {
      if ((o as THREE.Mesh).isMesh) (o as THREE.Mesh).castShadow = shadows;
    });
    spin.add(wheel);
    steer.add(spin);
    upright.add(steer);
    const { shock, spring, shaft } = shockGroup(mats, shadows);
    const tieRod = new THREE.Mesh(tieRodGeo, mats.chrome);
    tieRod.castShadow = shadows;
    group.add(upper, lower, upright, shock, tieRod);
    const name = `${front ? 'F' : 'R'}${s < 0 ? 'L' : 'R'}`;
    upright.name = `wheel_${name}`;
    corners.push({ s, za, front, upper, lower, upright, steer, spin, shock, spring, shaft, tieRod, offset: 0 });
  }

  // ---------------------------------------------------------------- astronaut
  const astro = buildAstronaut(mats);
  astro.group.traverse((o) => {
    if ((o as THREE.Mesh).isMesh) (o as THREE.Mesh).castShadow = shadows;
  });
  group.add(astro.group);

  // ---------------------------------------------------------------- glow + dust
  const glowTex = softSprite('rgba(255,244,220,1)', 'rgba(255,200,150,0)', 64);
  const glowGeo = new THREE.BufferGeometry().setFromPoints([...ch.lightBarGlowPoints, ...ch.headlightPoints]);
  const glowMat = new THREE.PointsMaterial({
    map: glowTex,
    size: 0.35,
    sizeAttenuation: true,
    transparent: true,
    opacity: 0.25,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    color: 0xfff1d6,
  });
  const glow = new THREE.Points(glowGeo, glowMat);
  glow.name = 'buggy:glow';
  glow.renderOrder = 3;
  group.add(glow);

  const dust = new DustSystem();
  group.add(dust.points);
  dust.points.onBeforeRender = (renderer, _scene, camera) => {
    const h = renderer.getDrawingBufferSize(tmpV2).y;
    dust.setViewport(h, (camera as THREE.PerspectiveCamera).fov ?? 50);
  };
  const tmpV2 = new THREE.Vector2();

  // ---------------------------------------------------------------- animation state
  let wheelAngle = 0;
  let steerNow = 0;
  let boostLevel = 0;
  let t = 0;
  let bob = 0;
  let bobVel = 0;
  let lastMean = 0;
  const tmp = new THREE.Vector3();
  const tmp2 = new THREE.Vector3();
  const fwd = new THREE.Vector3();
  const up = new THREE.Vector3();
  const vel = new THREE.Vector3();
  const nq = new THREE.Quaternion();
  const DOWN = new THREE.Vector3(0, -1, 0);
  const UPV = new THREE.Vector3(0, 1, 0);
  const shockTop = (c: Corner) => tmp.set(c.s * SUSP.shockTop[0], SUSP.shockTop[1], c.za + SUSP.shockTop[2]);

  const pose = (s: BuggyAnimState) => {
    const dt = Math.min(0.1, Math.max(0, s.dt || 0));
    t += dt;
    const speed = Number.isFinite(s.speedMps) ? s.speedMps : 0;
    const sf = Math.min(1, Math.abs(speed) / 6);
    wheelAngle -= (speed / WHEEL_RADIUS) * dt;
    const target = THREE.MathUtils.clamp(s.steerDeg || 0, -30, 30) * THREE.MathUtils.DEG2RAD;
    steerNow += (target - steerNow) * Math.min(1, dt * 10);
    boostLevel += ((s.boost ? 1 : 0) - boostLevel) * Math.min(1, dt * (s.boost ? 6 : 2.5));

    const offs = s.wheelGroundOffsets ?? [0, 0, 0, 0];
    let mean = 0;
    corners.forEach((c, i) => {
      // small high-frequency texture from rolling over regolith + boost squat
      const rough = 0.006 * sf * Math.sin(t * (13 + i * 2.1) + i * 1.7);
      const squat = boostLevel * (c.front ? -0.012 : 0.03);
      const tgt = THREE.MathUtils.clamp((offs[i] || 0) + rough + squat, -MAX_TRAVEL, MAX_TRAVEL);
      c.offset += (tgt - c.offset) * Math.min(1, dt * 18);
      mean += c.offset / 4;
      const dy = c.offset;
      const ang = Math.asin(THREE.MathUtils.clamp(dy / ARM_L, -0.9, 0.9));
      c.upper.rotation.z = c.s * ang;
      c.lower.rotation.z = c.s * ang;
      c.upright.position.y = WHEEL_RADIUS + dy;
      const steerAng = (c.front ? -steerNow : steerNow * 0.4);
      c.steer.rotation.y = steerAng;
      c.spin.rotation.x = wheelAngle;
      // coil-over between the fixed top mount and the lower arm tab
      const top = shockTop(c).clone();
      const lowLocal = tmp2.set(c.s * ARM_L * SUSP.shockLowFrac, 0.045, 0).applyAxisAngle(new THREE.Vector3(0, 0, 1), c.s * ang);
      const low = lowLocal.add(c.lower.position);
      const d = low.clone().sub(top);
      const len = d.length();
      c.shock.position.copy(top);
      c.shock.quaternion.copy(nq.setFromUnitVectors(DOWN, d.normalize()));
      c.spring.scale.y = (len - 0.1) / (SHOCK_LEN0 - 0.1);
      c.shaft.position.y = -len;
      // tie rod: rack end (fixed) -> steering arm ball (rotates with steer)
      const rackEnd = new THREE.Vector3(c.s * 0.32, 0.44, c.za + 0.14);
      const armEnd = new THREE.Vector3(0, 0.02, 0.14).applyAxisAngle(UPV, steerAng).add(c.upright.position);
      const rd = armEnd.clone().sub(rackEnd);
      c.tieRod.position.copy(rackEnd).add(armEnd).multiplyScalar(0.5);
      c.tieRod.quaternion.setFromUnitVectors(UPV, rd.clone().normalize());
      c.tieRod.scale.set(1, rd.length(), 1);
    });

    // astronaut: damped spring driven by body heave + rolling texture
    const heave = (mean - lastMean) / Math.max(dt, 1e-3);
    lastMean = mean;
    const k = 140;
    const damp = 9;
    bobVel += (-k * bob - damp * bobVel - heave * 4) * dt;
    bob += bobVel * dt;
    bob = THREE.MathUtils.clamp(bob, -0.03, 0.03);
    const jitter = 0.005 * sf * Math.sin(t * 9.3) + 0.003 * sf * Math.sin(t * 15.7 + 1.3);
    astro.group.position.y = bob + jitter;
    astro.head.rotation.x = -0.06 + (bob + jitter) * 3 - boostLevel * 0.06;
    astro.head.rotation.y = steerNow * 0.25;

    // lights
    mats.lamp.emissiveIntensity = 1.6 + boostLevel * 3.5;
    glowMat.opacity = 0.18 + boostLevel * 0.6;
    glowMat.size = 0.3 + boostLevel * 0.35;

    // dust (world space)
    group.updateMatrixWorld(true);
    const W = group.matrixWorld;
    fwd.set(0, 0, -1).transformDirection(W);
    up.set(0, 1, 0).transformDirection(W);
    const dir = Math.sign(speed) || 1;
    for (const c of corners) {
      const base = c.front ? 5 : 11;
      const n = dust.take(base * Math.abs(speed) * (1 + boostLevel * 1.5), dt);
      if (n > 0) {
        tmp.set(c.s * TRACK / 2, 0.08, c.za + dir * WHEEL_RADIUS * 0.75).applyMatrix4(W);
        vel.copy(fwd).multiplyScalar(-dir * (0.35 * Math.abs(speed) + boostLevel * 2)).addScaledVector(up, 0.5 + 0.12 * Math.abs(speed));
        dust.emit(n, tmp, vel, 0.8, 0.4 + boostLevel * 0.3, 1.3 + boostLevel * 0.7, 0.3 + 0.2 * sf);
      }
    }
    if (boostLevel > 0.05 && Math.abs(speed) > 0.2) {
      const n = dust.take(110 * boostLevel, dt);
      if (n > 0) {
        tmp.set(0, 0.15, 1.6).applyMatrix4(W);
        vel.copy(fwd).multiplyScalar(-(1.2 + 0.4 * Math.abs(speed))).addScaledVector(up, 1.6);
        dust.emit(n, tmp, vel, 1.6, 1.0, 2.0, 0.45 * boostLevel);
      }
    }
    dust.update(dt, W);
  };

  // place everything at rest once
  pose({ speedMps: 0, steerDeg: 0, boost: false, dt: 0 });

  return {
    group,
    update: pose,
    dispose() {
      const geos = new Set<THREE.BufferGeometry>();
      group.traverse((o) => {
        const m = o as THREE.Mesh;
        if (m.geometry) geos.add(m.geometry);
      });
      for (const g of geos) g.dispose();
      for (const m of mats.all) m.dispose();
      for (const tx of mats.textures) tx.dispose();
      glowMat.dispose();
      glowTex.dispose();
      dust.dispose();
      bolts.dispose();
      group.removeFromParent();
    },
  };
}
