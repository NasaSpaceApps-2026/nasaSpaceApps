/**
 * Smooth chase camera: sits behind and above the rover along its heading
 * (ignores pitch/roll so the horizon stays level), eases toward that spot with
 * frame-rate independent damping, and never dips below the terrain.
 */

import * as THREE from 'three';
import { headingDelta, headingToDir, normalizeHeading, siteToThree, type SitePose } from './coords';
import type { HeightSampler } from './heightTiles';

export interface ChaseCameraOptions {
  /** Horizontal distance behind the rover, m. */
  distance?: number;
  /** Height above the rover, m. */
  height?: number;
  /** Look-at point height above the rover origin, m. */
  targetHeight?: number;
  /** Damping rate, 1/s (higher = snappier). */
  stiffness?: number;
  /** Minimum clearance above the ground, m. */
  minClearance?: number;
  sampler?: HeightSampler;
}

export class ChaseCamera {
  distance: number;
  height: number;
  targetHeight: number;
  stiffness: number;
  minClearance: number;
  sampler?: HeightSampler;
  /** Extra yaw around the rover (deg), e.g. from mouse drag. */
  orbitDeg = 0;

  private heading: number | null = null;
  private pos = new THREE.Vector3();
  private target = new THREE.Vector3();
  private init = false;

  camera: THREE.PerspectiveCamera;

  constructor(camera: THREE.PerspectiveCamera, o: ChaseCameraOptions = {}) {
    this.camera = camera;
    this.distance = o.distance ?? 14;
    this.height = o.height ?? 6;
    this.targetHeight = o.targetHeight ?? 1.2;
    this.stiffness = o.stiffness ?? 4;
    this.minClearance = o.minClearance ?? 1.5;
    this.sampler = o.sampler;
  }

  /** Jump to the rest position on the next update (e.g. after a teleport). */
  snap(): void {
    this.init = false;
  }

  update(dt: number, pose: SitePose): void {
    const a = this.init ? 1 - Math.exp(-this.stiffness * Math.max(0, dt)) : 1;
    // Smooth heading separately so turning in place swings the camera around.
    const h = pose.heading_deg + this.orbitDeg;
    this.heading = this.heading === null || !this.init ? h : normalizeHeading(this.heading + headingDelta(this.heading, h) * a);

    const [dx, dy] = headingToDir(this.heading);
    const cx = pose.x - dx * this.distance;
    const cy = pose.y - dy * this.distance;
    let cz = pose.z + this.height;
    if (this.sampler) {
      const g = this.sampler.sampleHeight(cx, cy);
      if (Number.isFinite(g)) cz = Math.max(cz, g + this.minClearance);
    }
    const desired = new THREE.Vector3(...siteToThree(cx, cy, cz));
    const look = new THREE.Vector3(...siteToThree(pose.x, pose.y, pose.z + this.targetHeight));

    if (!this.init) {
      this.pos.copy(desired);
      this.target.copy(look);
      this.init = true;
    } else {
      this.pos.lerp(desired, a);
      this.target.lerp(look, Math.min(1, a * 2));
    }
    this.camera.position.copy(this.pos);
    this.camera.lookAt(this.target);
  }
}
