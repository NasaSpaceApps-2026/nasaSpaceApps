/**
 * Physics v1: terrain-following rover (no suspension, no soil model).
 *
 * Each step samples ground height under the four corner wheels, sets the body
 * height to their mean and pitch/roll from the front-rear and left-right
 * differences, and enforces the profile's slope limits:
 *   - above caution_slope_deg the target speed is additionally scaled down;
 *   - the rover refuses to move (forward or back) onto ground steeper than
 *     nogo_slope_deg and reports status "blocked_slope".
 * Speed follows the profile curve speed = flat * exp(-k * |tan(slope)|), the
 * same shape the API planner/sim uses (api/profiles.yaml, served by GET /site).
 *
 * Time: `step(dt)` takes wall-clock seconds. Motion advances in mission time,
 * dt * display_speed_factor, so speeds below are real (mission) m/s and the
 * rover covers ground display_speed_factor times faster on screen, like the
 * API simulator. Turn rate and acceleration are display (wall-clock) limits.
 *
 * Pure TypeScript; no DOM, no three.js.
 */

import { DEG, headingToDir, normalizeHeading, type SitePose } from './coords';
import type { HeightSampler } from './heightTiles';

export interface RoverProfile {
  flat_speed_mps: number;
  display_speed_factor: number;
  caution_slope_deg: number;
  nogo_slope_deg: number;
  /** speed_curve.k from GET /site (global, not per profile). */
  speed_curve_k: number;
  /** Where the numbers come from; shown in the HUD. */
  source: string;
}

/** Built-in fallback, matching api/profiles.yaml rover_scout as of Nov 14 2026. */
export const DEFAULT_ROVER_PROFILE: RoverProfile = {
  flat_speed_mps: 0.04,
  display_speed_factor: 200,
  caution_slope_deg: 15,
  nogo_slope_deg: 20,
  speed_curve_k: 3.5,
  source: 'assumed (built-in defaults; API /site not reachable)',
};

/**
 * Build a RoverProfile from a GET /site response. Expects
 * `profiles.<rover_profile|rover_scout>` and `speed_curve.k`. Returns null if
 * the shape is not there.
 */
export function profileFromSite(site: unknown, name?: string): RoverProfile | null {
  const s = site as Record<string, any> | null;
  if (!s || typeof s !== 'object' || !s.profiles) return null;
  const key = name ?? s.rover_profile ?? 'rover_scout';
  const p = s.profiles[key];
  if (!p || typeof p.flat_speed_mps !== 'number') return null;
  const k = s.speed_curve?.k;
  return {
    flat_speed_mps: p.flat_speed_mps,
    display_speed_factor: p.display_speed_factor ?? DEFAULT_ROVER_PROFILE.display_speed_factor,
    caution_slope_deg: p.caution_slope_deg ?? DEFAULT_ROVER_PROFILE.caution_slope_deg,
    nogo_slope_deg: p.nogo_slope_deg ?? DEFAULT_ROVER_PROFILE.nogo_slope_deg,
    speed_curve_k: typeof k === 'number' ? k : DEFAULT_ROVER_PROFILE.speed_curve_k,
    source: `API /site profiles.${key}: ${p.source ?? 'assumed'}`,
  };
}

export interface RoverParams {
  /** Front-to-rear axle distance, m (Perseverance-like). */
  wheelbase_m: number;
  /** Left-to-right wheel centre distance, m. */
  track_m: number;
  /** Wall-clock seconds to accelerate from 0 to flat speed. */
  accel_time_s: number;
  /** Turn rate, display degrees per wall-clock second. */
  turn_rate_deg_s: number;
  /** Extra speed multiplier while slope > caution_slope_deg. */
  caution_speed_factor: number;
  /** How far beyond the leading axle to check the ground slope, m. */
  lookahead_m: number;
  /** Half-spacing for the finite-difference slope probe, m. */
  slope_probe_m: number;
  /** Largest wall-clock dt accepted per step, s (longer frames are clamped). */
  max_dt_s: number;
}

export const DEFAULT_ROVER_PARAMS: RoverParams = {
  wheelbase_m: 2.7,
  track_m: 2.3,
  accel_time_s: 0.6,
  turn_rate_deg_s: 40,
  caution_speed_factor: 0.5,
  lookahead_m: 0.5,
  slope_probe_m: 1.0,
  max_dt_s: 0.1,
};

export interface DriveInput {
  /** -1 (full reverse) .. 1 (full forward). */
  throttle: number;
  /** -1 (turn left) .. 1 (turn right). */
  steer: number;
}

export type RoverStatus = 'idle' | 'driving' | 'caution' | 'blocked_slope' | 'blocked_edge';

export interface RoverState extends SitePose {
  /** Signed mission speed, m/s (real-time equivalent; negative = reversing). */
  speed_mps: number;
  /** Signed on-screen speed, m per wall-clock second. */
  display_speed_mps: number;
  /** Slope of the plane through the four wheel contacts, deg. */
  slope_deg: number;
  /** Terrain slope just beyond the leading axle in the travel (or facing) direction, deg. */
  ahead_slope_deg: number;
  status: RoverStatus;
  /** Why the rover last stopped short of the command; null while unobstructed. */
  stop_reason: string | null;
  /** Mission (simulated) elapsed seconds while driving. */
  mission_time_s: number;
  odometer_m: number;
}

export interface Footprint {
  z: number;
  pitch_deg: number;
  roll_deg: number;
  slope_deg: number;
  /** Heights at front-left, front-right, rear-left, rear-right wheels. */
  wheels: [number, number, number, number];
}

/** Ground contact of the four corner wheels and the resulting body pose. */
export function footprint(s: HeightSampler, x: number, y: number, heading_deg: number, p: RoverParams = DEFAULT_ROVER_PARAMS): Footprint {
  const [fx, fy] = headingToDir(heading_deg);
  const rx = fy; // right-hand vector: heading + 90 deg
  const ry = -fx;
  const a = p.wheelbase_m / 2;
  const b = p.track_m / 2;
  const fl = s.sampleHeight(x + fx * a - rx * b, y + fy * a - ry * b);
  const fr = s.sampleHeight(x + fx * a + rx * b, y + fy * a + ry * b);
  const rl = s.sampleHeight(x - fx * a - rx * b, y - fy * a - ry * b);
  const rr = s.sampleHeight(x - fx * a + rx * b, y - fy * a + ry * b);
  const gradFwd = ((fl + fr) - (rl + rr)) / 2 / p.wheelbase_m;
  const gradRight = ((fr + rr) - (fl + rl)) / 2 / p.track_m;
  return {
    z: (fl + fr + rl + rr) / 4,
    pitch_deg: Math.atan(gradFwd) / DEG,
    roll_deg: (0 - Math.atan(gradRight)) / DEG, // right side lower -> positive (0 - avoids -0)
    slope_deg: Math.atan(Math.hypot(gradFwd, gradRight)) / DEG,
    wheels: [fl, fr, rl, rr],
  };
}

/** Terrain slope magnitude at (x, y) by central differences, deg. */
export function terrainSlopeDeg(s: HeightSampler, x: number, y: number, h = 1.0): number {
  const gx = (s.sampleHeight(x + h, y) - s.sampleHeight(x - h, y)) / (2 * h);
  const gy = (s.sampleHeight(x, y + h) - s.sampleHeight(x, y - h)) / (2 * h);
  return Math.atan(Math.hypot(gx, gy)) / DEG;
}

/** Mission speed (m/s) on a given slope, before throttle and caution factor. */
export function speedForSlope(profile: RoverProfile, slope_deg: number): number {
  return profile.flat_speed_mps * Math.exp(-profile.speed_curve_k * Math.abs(Math.tan(slope_deg * DEG)));
}

export interface InitialPose {
  x: number;
  y: number;
  heading_deg?: number;
}

export class RoverPhysics {
  sampler: HeightSampler;
  profile: RoverProfile;
  params: RoverParams;
  private s: RoverState;

  constructor(sampler: HeightSampler, profile: RoverProfile = DEFAULT_ROVER_PROFILE, params: Partial<RoverParams> = {}, start: InitialPose = { x: 0, y: 0 }) {
    this.sampler = sampler;
    this.profile = profile;
    this.params = { ...DEFAULT_ROVER_PARAMS, ...params };
    this.s = {
      x: start.x, y: start.y, z: 0, heading_deg: normalizeHeading(start.heading_deg ?? 0), pitch_deg: 0, roll_deg: 0,
      speed_mps: 0, display_speed_mps: 0, slope_deg: 0, ahead_slope_deg: 0,
      status: 'idle', stop_reason: null, mission_time_s: 0, odometer_m: 0,
    };
    this.settle();
  }

  get state(): Readonly<RoverState> {
    return this.s;
  }

  /** Place the rover (teleport or hand-over from auto mode); speed resets to 0. */
  setPose(x: number, y: number, heading_deg: number): void {
    this.s.x = x;
    this.s.y = y;
    this.s.heading_deg = normalizeHeading(heading_deg);
    this.s.speed_mps = 0;
    this.s.display_speed_mps = 0;
    this.s.stop_reason = null;
    this.s.status = 'idle';
    this.settle();
  }

  setSampler(s: HeightSampler): void {
    this.sampler = s;
    this.settle();
  }

  /** Recompute z, pitch, roll and slopes for the current x, y, heading. */
  settle(): void {
    const f = footprint(this.sampler, this.s.x, this.s.y, this.s.heading_deg, this.params);
    this.s.z = f.z;
    this.s.pitch_deg = f.pitch_deg;
    this.s.roll_deg = f.roll_deg;
    this.s.slope_deg = f.slope_deg;
    this.s.ahead_slope_deg = this.aheadSlope(this.s.speed_mps < 0 ? -1 : 1);
  }

  private aheadSlope(dirSign: number): number {
    const [fx, fy] = headingToDir(this.s.heading_deg);
    const d = dirSign * (this.params.wheelbase_m / 2 + this.params.lookahead_m);
    return terrainSlopeDeg(this.sampler, this.s.x + fx * d, this.s.y + fy * d, this.params.slope_probe_m);
  }

  private aheadInside(dirSign: number): boolean {
    if (!this.sampler.contains) return true;
    const [fx, fy] = headingToDir(this.s.heading_deg);
    const d = dirSign * (this.params.wheelbase_m / 2 + this.params.lookahead_m);
    return this.sampler.contains(this.s.x + fx * d, this.s.y + fy * d);
  }

  step(dtWall: number, input: DriveInput): Readonly<RoverState> {
    const p = this.params;
    const prof = this.profile;
    const dt = Math.min(Math.max(dtWall, 0), p.max_dt_s);
    const s = this.s;
    const throttle = Math.max(-1, Math.min(1, input.throttle || 0));
    const steer = Math.max(-1, Math.min(1, input.steer || 0));

    // Turning (in place or while driving).
    if (steer !== 0) {
      s.heading_deg = normalizeHeading(s.heading_deg + steer * p.turn_rate_deg_s * dt);
    }

    // Target speed from slope under and ahead of the rover.
    const dirSign = throttle !== 0 ? Math.sign(throttle) : s.speed_mps !== 0 ? Math.sign(s.speed_mps) : 1;
    const ahead = this.aheadSlope(dirSign);
    const governing = Math.max(s.slope_deg, ahead);
    let target = Math.abs(throttle) * speedForSlope(prof, governing);
    const caution = governing > prof.caution_slope_deg;
    if (caution) target *= p.caution_speed_factor;
    target *= Math.sign(throttle);

    // Acceleration limit (braking twice as fast).
    const accel = (prof.flat_speed_mps / p.accel_time_s) * dt;
    const braking = Math.abs(target) < Math.abs(s.speed_mps) || Math.sign(target) !== Math.sign(s.speed_mps);
    const maxDv = braking ? 2 * accel : accel;
    s.speed_mps += Math.max(-maxDv, Math.min(maxDv, target - s.speed_mps));
    if (Math.abs(s.speed_mps) < 1e-6) s.speed_mps = 0;

    // Slope / edge gate on the direction of travel.
    let blocked: RoverStatus | null = null;
    if (s.speed_mps !== 0 || throttle !== 0) {
      const moveSign = s.speed_mps !== 0 ? Math.sign(s.speed_mps) : Math.sign(throttle);
      const aheadMove = moveSign === dirSign ? ahead : this.aheadSlope(moveSign);
      // Allow moves that get the rover off steep ground it already sits on.
      const escaping = s.slope_deg > prof.nogo_slope_deg && aheadMove < s.slope_deg - 0.5;
      if (!this.aheadInside(moveSign)) {
        blocked = 'blocked_edge';
        s.stop_reason = 'edge of terrain data';
      } else if (aheadMove > prof.nogo_slope_deg && !escaping) {
        blocked = 'blocked_slope';
        s.stop_reason = `ground ${moveSign > 0 ? 'ahead' : 'behind'} is ${aheadMove.toFixed(1)}°, above no-go limit ${prof.nogo_slope_deg}°`;
      }
    }

    if (blocked) {
      s.speed_mps = 0;
      s.status = blocked;
    } else {
      // Advance in mission time, sub-stepping so each move is <= 0.5 m.
      const dtMission = dt * prof.display_speed_factor;
      const dist = s.speed_mps * dtMission;
      if (dist !== 0) {
        const n = Math.max(1, Math.ceil(Math.abs(dist) / 0.5));
        const [fx, fy] = headingToDir(s.heading_deg);
        let moved = 0;
        for (let i = 0; i < n; i++) {
          const d = dist / n;
          const nx = s.x + fx * d;
          const ny = s.y + fy * d;
          const f = footprint(this.sampler, nx, ny, s.heading_deg, p);
          if (f.slope_deg > prof.nogo_slope_deg && f.slope_deg > s.slope_deg) {
            s.status = 'blocked_slope';
            s.stop_reason = `rover footprint would tilt ${f.slope_deg.toFixed(1)}°, above no-go limit ${prof.nogo_slope_deg}°`;
            s.speed_mps = 0;
            blocked = 'blocked_slope';
            break;
          }
          s.x = nx;
          s.y = ny;
          s.slope_deg = f.slope_deg;
          moved += Math.abs(d);
        }
        s.odometer_m += moved;
        s.mission_time_s += dtMission;
      }
      if (!blocked) {
        s.status = s.speed_mps === 0 ? 'idle' : caution ? 'caution' : 'driving';
        if (s.speed_mps !== 0) s.stop_reason = null;
      }
    }

    s.display_speed_mps = s.speed_mps * prof.display_speed_factor;
    const f = footprint(this.sampler, s.x, s.y, s.heading_deg, p);
    s.z = f.z;
    s.pitch_deg = f.pitch_deg;
    s.roll_deg = f.roll_deg;
    s.slope_deg = f.slope_deg;
    s.ahead_slope_deg = ahead;
    return s;
  }
}
