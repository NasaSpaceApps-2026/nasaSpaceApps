/**
 * Rover physics v2: terrain-following body driven by forces, not slope rules.
 *
 * Each sub-step samples the ground under the four corner wheels for z, pitch and
 * roll, then integrates speed along the heading from:
 *   - gravity along the slope, -m g sin(pitch) (Mars g = 3.71 m/s^2);
 *   - motor force, limited by power (P / v) and by wheel grip (mu * m g cos(pitch));
 *   - rolling resistance, Crr * m g cos(pitch);
 *   - brakes when the throttle is released, also limited by grip.
 * So slopes get harder smoothly: the rover slows on climbs, coasts faster
 * downhill, spins out where grip runs out (about atan(mu - Crr)), and slides back
 * on ground steeper than its brakes can hold. There is no hard "blocked" state:
 * caution/no-go slopes from the profile are advisory (HUD + API planner policy).
 *
 * Time: `step(dt)` takes wall-clock seconds; motion runs in mission time,
 * dt * display_speed_factor * time_scale, so speeds are real (mission) m/s.
 *
 * Pure TypeScript; no DOM, no three.js.
 */

import { DEG, headingToDir, normalizeHeading, type SitePose } from './coords';
import type { HeightSampler } from './heightTiles';

export const MARS_G = 3.71;

export interface RoverProfile {
  /** Speed-controller limit on level ground, m/s. */
  flat_speed_mps: number;
  display_speed_factor: number;
  caution_slope_deg: number;
  nogo_slope_deg: number;
  /** Planner speed curve k (the API's estimate; the browser uses forces below). */
  speed_curve_k: number;
  /** Loaded vehicle mass, kg. */
  mass_kg: number;
  /** Total wheel motor power, W. */
  power_w: number;
  /** Wheel-soil grip coefficient (tractive force / normal force). */
  traction_mu: number;
  /** Rolling-resistance coefficient on regolith. */
  rolling_coeff: number;
  /** Motor power multiplier while boosting. */
  boost_power_factor?: number;
  /** Where the numbers come from; shown in the HUD. */
  source: string;
  /** Boosted flat speed, m/s; absent = no boost. */
  boost_speed_mps?: number;
  /** Car-like steering with this minimum turn radius, m; absent = turns in place (skid steer). */
  min_turn_radius_m?: number;
}

/** Built-in fallback, matching api/profiles.yaml crew_buggy as of Nov 14 2026. */
export const DEFAULT_BUGGY_PROFILE: RoverProfile = {
  flat_speed_mps: 3.6,
  boost_speed_mps: 5.0,
  display_speed_factor: 1,
  caution_slope_deg: 15,
  nogo_slope_deg: 25,
  speed_curve_k: 2.0,
  mass_kg: 700,
  power_w: 1500,
  traction_mu: 0.6,
  rolling_coeff: 0.08,
  boost_power_factor: 1.6,
  min_turn_radius_m: 3.1,
  source: 'Apollo LRV specs (built-in defaults; API /site not reachable)',
};

/** Built-in fallback, matching api/profiles.yaml rover_scout as of Nov 14 2026. */
export const DEFAULT_ROVER_PROFILE: RoverProfile = {
  flat_speed_mps: 0.04,
  display_speed_factor: 200,
  caution_slope_deg: 15,
  nogo_slope_deg: 20,
  speed_curve_k: 3.5,
  mass_kg: 1025,
  power_w: 40,
  traction_mu: 0.6,
  rolling_coeff: 0.08,
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
  const k = typeof p.speed_curve_k === 'number' ? p.speed_curve_k : s.speed_curve?.k;
  const d = key === 'crew_buggy' ? DEFAULT_BUGGY_PROFILE : DEFAULT_ROVER_PROFILE;
  return {
    flat_speed_mps: p.flat_speed_mps,
    display_speed_factor: p.display_speed_factor ?? DEFAULT_ROVER_PROFILE.display_speed_factor,
    caution_slope_deg: p.caution_slope_deg ?? DEFAULT_ROVER_PROFILE.caution_slope_deg,
    nogo_slope_deg: p.nogo_slope_deg ?? DEFAULT_ROVER_PROFILE.nogo_slope_deg,
    speed_curve_k: typeof k === 'number' ? k : DEFAULT_ROVER_PROFILE.speed_curve_k,
    source: `API /site profiles.${key}: ${p.source ?? 'assumed'}`,
    mass_kg: p.mass_kg ?? d.mass_kg,
    power_w: p.power_w ?? d.power_w,
    traction_mu: p.traction_mu ?? d.traction_mu,
    rolling_coeff: p.rolling_coeff ?? d.rolling_coeff,
    boost_power_factor: p.boost_power_factor ?? d.boost_power_factor,
    boost_speed_mps: p.boost_speed_mps,
    min_turn_radius_m: p.min_turn_radius_m,
  };
}

export interface RoverParams {
  /** Front-to-rear axle distance, m (Perseverance-like). */
  wheelbase_m: number;
  /** Left-to-right wheel centre distance, m. */
  track_m: number;
  /** Turn rate cap, display degrees per wall-clock second. */
  turn_rate_deg_s: number;
  /** Largest mission-time integration sub-step, s. */
  max_substep_s: number;
  /** Static roll-over angle (track/2 over CG height), deg; advisory. */
  tip_angle_deg: number;
  /** How far beyond the leading axle to check the ground slope, m. */
  lookahead_m: number;
  /** Half-spacing for the finite-difference slope probe, m. */
  slope_probe_m: number;
  /** Largest wall-clock dt accepted per step, s (longer frames are clamped). */
  max_dt_s: number;
  /** Wall-clock seconds of boost from a full charge. */
  boost_duration_s: number;
  /** Wall-clock seconds to recharge boost from empty (only while not boosting). */
  boost_recharge_s: number;
}

export const DEFAULT_ROVER_PARAMS: RoverParams = {
  wheelbase_m: 2.7,
  track_m: 2.3,
  turn_rate_deg_s: 40,
  max_substep_s: 0.05,
  tip_angle_deg: 45,
  lookahead_m: 0.5,
  slope_probe_m: 1.0,
  max_dt_s: 0.1,
  boost_duration_s: 4,
  boost_recharge_s: 8,
};

/** Buggy geometry and handling (LRV-like: 2.3 m wheelbase, 1.8 m track). */
export const BUGGY_PARAMS: Partial<RoverParams> = {
  wheelbase_m: 2.3,
  track_m: 1.8,
  turn_rate_deg_s: 60,
  tip_angle_deg: 50,
};

export interface DriveInput {
  /** -1 (full reverse) .. 1 (full forward). */
  throttle: number;
  /** -1 (turn left) .. 1 (turn right). */
  steer: number;
  /** Hold for boost (profiles with boost_speed_mps only). */
  boost?: boolean;
}

/**
 * idle | driving | caution (past caution slope) | steep (past the no-go policy slope; advisory)
 * | slipping (wheels at the grip limit, not gaining ground) | sliding (moving against the
 * command or with brakes on) | tip_risk (roll near roll-over) | blocked_edge (no terrain data).
 */
export type RoverStatus = 'idle' | 'driving' | 'caution' | 'steep' | 'slipping' | 'sliding' | 'tip_risk' | 'blocked_edge';

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
  /** Plain-language note on what is limiting the rover (grip, power, slope policy); null if nothing. */
  stop_reason: string | null;
  /** Current forces along the heading, N (for the HUD). */
  forces: { gravity: number; motor: number; resistance: number; grip_limit: number };
  /** Mission (simulated) elapsed seconds while driving. */
  mission_time_s: number;
  odometer_m: number;
  /** Current steering input, -1..1 (for wheel animation). */
  steer: number;
  boost_active: boolean;
  /** Boost charge, 0..1. */
  boost_charge: number;
  /** Viewer time-lapse multiplier on top of display_speed_factor. */
  time_scale: number;
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

/** Planner's speed estimate on a slope (same curve as the API); not used by the force model. */
export function speedForSlope(profile: RoverProfile, slope_deg: number): number {
  return profile.flat_speed_mps * Math.exp(-profile.speed_curve_k * Math.abs(Math.tan(slope_deg * DEG)));
}

/** Steady climbing speed on a slope from the force model (power, grip, speed cap), m/s. */
export function climbSpeed(profile: RoverProfile, slope_deg: number, boost = false): number {
  const th = slope_deg * DEG;
  const m = profile.mass_kg;
  const need = m * MARS_G * (Math.sin(th) + profile.rolling_coeff * Math.cos(th));
  const grip = profile.traction_mu * m * MARS_G * Math.cos(th);
  if (need >= grip) return 0;
  const cap = boost && profile.boost_speed_mps ? profile.boost_speed_mps : profile.flat_speed_mps;
  const p = profile.power_w * (boost ? profile.boost_power_factor ?? 1 : 1);
  return need <= 0 ? cap : Math.min(cap, p / need);
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
      steer: 0, boost_active: false, boost_charge: 1, time_scale: 1,
      forces: { gravity: 0, motor: 0, resistance: 0, grip_limit: 0 },
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

  /** Time-lapse multiplier (1 = profile rate). Clamped to 0.25..16. */
  setTimeScale(k: number): void {
    this.s.time_scale = Math.max(0.25, Math.min(16, k));
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

  private inside(x: number, y: number): boolean {
    return this.sampler.contains ? this.sampler.contains(x, y) : true;
  }

  step(dtWall: number, input: DriveInput): Readonly<RoverState> {
    const p = this.params;
    const prof = this.profile;
    const s = this.s;
    const dt = Math.min(Math.max(dtWall, 0), p.max_dt_s);
    const throttle = Math.max(-1, Math.min(1, input.throttle || 0));
    const steer = Math.max(-1, Math.min(1, input.steer || 0));
    s.steer = steer;

    // Boost: drains while held with throttle forward, recharges otherwise.
    const canBoost = !!prof.boost_speed_mps && !!input.boost && throttle > 0 && s.boost_charge > 0;
    s.boost_active = canBoost;
    if (canBoost) s.boost_charge = Math.max(0, s.boost_charge - dt / p.boost_duration_s);
    else s.boost_charge = Math.min(1, s.boost_charge + dt / p.boost_recharge_s);
    const cap = canBoost ? prof.boost_speed_mps! : prof.flat_speed_mps;
    const power = prof.power_w * (canBoost ? prof.boost_power_factor ?? 1 : 1);

    const rate = prof.display_speed_factor * s.time_scale; // mission seconds per wall second
    const tMission = dt * rate;
    const n = Math.max(1, Math.ceil(tMission / p.max_substep_s));
    const h = tMission / n;
    const m = prof.mass_kg;
    let gripLimited = false;
    let edge = false;
    let gravity = 0, motor = 0, resist = 0, grip = 0;

    for (let i = 0; i < n; i++) {
      // Steering: skid steer turns in place; car-like steering needs motion (yaw = v / R).
      if (steer !== 0) {
        const capDeg = p.turn_rate_deg_s * (h / Math.max(prof.display_speed_factor, 1e-9));
        const yaw = prof.min_turn_radius_m
          ? Math.max(-capDeg, Math.min(capDeg, (s.speed_mps / prof.min_turn_radius_m) * h / DEG * steer))
          : steer * capDeg;
        s.heading_deg = normalizeHeading(s.heading_deg + yaw);
      }

      const f = footprint(this.sampler, s.x, s.y, s.heading_deg, p);
      const th = f.pitch_deg * DEG; // + nose up
      const normal = m * MARS_G * Math.cos(th);
      grip = prof.traction_mu * normal;
      gravity = -m * MARS_G * Math.sin(th);
      const v = s.speed_mps;
      const vSign = Math.sign(v);
      resist = -vSign * prof.rolling_coeff * normal;

      // Wheel force: motor toward the command, or brakes if released / reversing direction.
      motor = 0;
      let braking = false;
      if (throttle !== 0 && (vSign === 0 || vSign === Math.sign(throttle))) {
        const dir = Math.sign(throttle);
        const avail = Math.abs(throttle) * Math.min(power / Math.max(Math.abs(v), 0.05 * cap), grip);
        // Speed controller: never push past the cap within this sub-step.
        const toCap = dir * (m * (dir * cap * Math.abs(throttle) - v) / h - gravity - resist);
        motor = dir * Math.min(avail, Math.max(0, toCap));
        gripLimited = avail >= grip - 1e-9 && toCap > avail;
      } else {
        // Brakes lock the wheels: grip replaces rolling resistance, and holds against gravity if it can.
        resist = 0;
        braking = true;
        const want = -(m * v / h) - gravity;
        motor = Math.max(-grip, Math.min(grip, want));
      }

      if (vSign === 0 && !braking) {
        // At rest, rolling resistance opposes whichever way the other forces push, up to Crr * N.
        const drive = motor + gravity;
        resist = -Math.sign(drive) * Math.min(prof.rolling_coeff * normal, Math.abs(drive));
      }
      let a = (motor + gravity + resist) / m;
      let vNew = v + a * h;
      // Rolling resistance alone never reverses the motion.
      if (vSign !== 0 && Math.sign(vNew) === -vSign && Math.abs(motor + gravity) <= Math.abs(resist)) vNew = 0;

      const ds = vNew * h;
      const [fx, fy] = headingToDir(s.heading_deg);
      const nx = s.x + fx * ds * Math.cos(th);
      const ny = s.y + fy * ds * Math.cos(th);
      if (!this.inside(nx, ny)) {
        edge = true;
        s.speed_mps = 0;
        break;
      }
      s.x = nx;
      s.y = ny;
      s.speed_mps = Math.abs(vNew) < 1e-6 ? 0 : vNew;
      s.odometer_m += Math.abs(ds);
      if (s.speed_mps !== 0 || throttle !== 0) s.mission_time_s += h;
    }

    const f = footprint(this.sampler, s.x, s.y, s.heading_deg, p);
    s.z = f.z;
    s.pitch_deg = f.pitch_deg;
    s.roll_deg = f.roll_deg;
    s.slope_deg = f.slope_deg;
    s.ahead_slope_deg = this.aheadSlope(s.speed_mps < 0 ? -1 : throttle < 0 ? -1 : 1);
    s.display_speed_mps = s.speed_mps * rate;
    s.forces = { gravity, motor, resistance: resist, grip_limit: grip };

    // Status and a plain-language note; nothing here stops the rover.
    const against = (throttle !== 0 && s.speed_mps * throttle < -1e-3) || (throttle === 0 && Math.abs(s.speed_mps) > 0.02 * Math.max(cap, 0.01));
    if (edge) {
      s.status = 'blocked_edge';
      s.stop_reason = 'edge of terrain data';
    } else if (Math.abs(s.roll_deg) > p.tip_angle_deg * 0.8) {
      s.status = 'tip_risk';
      s.stop_reason = `roll ${Math.abs(s.roll_deg).toFixed(0)}°, near roll-over (~${p.tip_angle_deg}°)`;
    } else if (against) {
      s.status = 'sliding';
      s.stop_reason = `sliding on ${s.slope_deg.toFixed(0)}°: gravity beats grip (limit ~${(Math.atan(prof.traction_mu) / DEG).toFixed(0)}°)`;
    } else if (throttle !== 0 && gripLimited && Math.abs(s.speed_mps) < 0.1 * cap) {
      s.status = 'slipping';
      s.stop_reason = `wheels slipping on ${s.slope_deg.toFixed(0)}°: grip limit reached`;
    } else if (s.slope_deg > prof.nogo_slope_deg) {
      s.status = 'steep';
      s.stop_reason = `${s.slope_deg.toFixed(0)}° is past the ${prof.nogo_slope_deg}° safety limit (advisory)`;
    } else if (s.slope_deg > prof.caution_slope_deg) {
      s.status = 'caution';
      s.stop_reason = null;
    } else {
      s.status = s.speed_mps === 0 ? 'idle' : 'driving';
      s.stop_reason = null;
    }
    return s;
  }
}
