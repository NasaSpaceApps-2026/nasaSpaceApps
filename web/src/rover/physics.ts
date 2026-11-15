/**
 * Rover physics v3: rigid-body vehicle with four spring-damper wheels.
 *
 * The chassis is one rigid body (position, velocity, orientation quaternion,
 * angular velocity; mass and a box inertia tensor). Every sub-step:
 *   - gravity, Mars g = 3.71 m/s^2 (no aerodynamic drag: Mars air is ~1% of Earth's);
 *   - for each wheel, the ground is sampled under the wheel (bilinear DEM + local
 *     normal); the suspension compression along the body's down axis gives a
 *     spring-damper force F = k x + c x' (never pulling; no force once the wheel
 *     hangs past full droop, a hard bump stop at full compression);
 *   - tyre forces in the ground plane, from the wheel load Fz: drive (power- and
 *     grip-limited motor torque, shared by load), rolling resistance Crr Fz, brakes,
 *     and lateral force opposing the slip velocity. Longitudinal + lateral force is
 *     capped by the friction circle mu Fz, so the cornering limit v^2/R <= mu g, climb
 *     limit and brake-hold limit all come out of the same contact model;
 *   - chassis corners touch the ground too (roll-overs, bottoming out).
 * Springs, dampers and contacts are solved as soft/hard velocity constraints
 * (sequential impulses, implicit spring-damper), so it is stable at any step and
 * at rest on slopes (static friction holds exactly within grip, no creep).
 * Jumps, landings and roll-overs need no special modes: they are what the body does.
 *
 * Time: `step(dt)` takes wall-clock seconds; motion runs in mission time,
 * dt * display_speed_factor * time_scale, so speeds are real (mission) m/s.
 *
 * Pure TypeScript; no DOM, no three.js. Numbers are logged in SCIENCE_VALIDITY.md.
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
  /** Motors hold the wheels when there is no throttle (rover drive); otherwise it coasts like a car in neutral. */
  auto_brake?: boolean;
  /** Where the numbers come from; shown in the HUD. */
  source: string;
  /** Boosted flat speed, m/s; absent = no boost. */
  boost_speed_mps?: number;
  /** Car-like steering with this minimum turn radius, m; absent = turns in place (skid steer). */
  min_turn_radius_m?: number;
  /** Sprung (heave) natural frequency of the suspension, Hz. */
  suspension_hz?: number;
  /** Suspension damping ratio (1 = critical). */
  damping_ratio?: number;
  /** Wheel travel from full droop to the bump stop, m. */
  suspension_travel_m?: number;
}

/** Built-in fallback, matching api/profiles.yaml crew_buggy as of Nov 14 2026. */
export const DEFAULT_BUGGY_PROFILE: RoverProfile = {
  flat_speed_mps: 12,
  boost_speed_mps: 20,
  display_speed_factor: 1,
  caution_slope_deg: 15,
  nogo_slope_deg: 25,
  speed_curve_k: 2.0,
  mass_kg: 700,
  power_w: 10000,
  traction_mu: 0.6,
  rolling_coeff: 0.08,
  boost_power_factor: 1.6,
  min_turn_radius_m: 3.1,
  suspension_hz: 1.15,
  damping_ratio: 0.38,
  suspension_travel_m: 0.25,
  source: 'assumed performance buggy (LRV-derived geometry; built-in defaults, API /site not reachable)',
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
  auto_brake: true,
  suspension_hz: 1.0,
  damping_ratio: 0.6,
  suspension_travel_m: 0.3,
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
    auto_brake: p.auto_brake ?? d.auto_brake,
    boost_speed_mps: p.boost_speed_mps,
    min_turn_radius_m: p.min_turn_radius_m,
    suspension_hz: p.suspension_hz ?? d.suspension_hz,
    damping_ratio: p.damping_ratio ?? d.damping_ratio,
    suspension_travel_m: p.suspension_travel_m ?? d.suspension_travel_m,
  };
}

export interface RoverParams {
  /** Front-to-rear axle distance, m. */
  wheelbase_m: number;
  /** Left-to-right wheel centre distance, m. */
  track_m: number;
  /** Wheel radius, m. */
  wheel_radius_m: number;
  /** Centre of mass above the body origin (the ground under the body at static ride height), m. */
  cg_height_m: number;
  /** Inertia box (length x width x height), m. */
  body_length_m: number;
  body_width_m: number;
  body_height_m: number;
  /** Chassis collision box: half-length / half-width at the bottom (belly) and top (cage/mast) corners, m; heights above the body origin. */
  hull_half_length_m: number;
  hull_half_width_m: number;
  hull_bottom_m: number;
  hull_top_m: number;
  hull_top_half_length_m: number;
  hull_top_half_width_m: number;
  /** Skid-steer turn rate, display degrees per wall-clock second. */
  turn_rate_deg_s: number;
  /** Steering slew, full lock per this many seconds (display time). */
  steer_lock_s: number;
  /** Rear wheels counter-steer fully below this speed and not at all above `rear_steer_off_mps` (4-wheel steer blend). */
  rear_steer_full_mps: number;
  rear_steer_off_mps: number;
  /** Largest wheel steer angle, deg. */
  max_steer_deg: number;
  /** Nominal mission-time sub-step, s (contacts and suspension). */
  max_substep_s: number;
  /** Sub-steps are at least this long when a step this short would move less than `substep_travel_m` at top speed (slow rovers). */
  substep_travel_m: number;
  /** Largest sub-step for slow rovers, s. */
  slow_substep_s: number;
  /** Most sub-steps per `step()` call; beyond it sub-steps get longer (the implicit solver stays stable). */
  max_substeps: number;
  /** Constraint solver iterations per sub-step. */
  solver_iterations: number;
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

/** Scout rover (Perseverance-like) geometry. */
export const DEFAULT_ROVER_PARAMS: RoverParams = {
  wheelbase_m: 2.7,
  track_m: 2.3,
  wheel_radius_m: 0.2625,
  cg_height_m: 1.15,
  body_length_m: 3.0,
  body_width_m: 2.7,
  body_height_m: 2.2,
  hull_half_length_m: 1.5,
  hull_half_width_m: 1.35,
  hull_bottom_m: 0.6,
  hull_top_m: 2.2,
  hull_top_half_length_m: 1.0,
  hull_top_half_width_m: 0.9,
  turn_rate_deg_s: 40,
  steer_lock_s: 0.4,
  rear_steer_full_mps: 3,
  rear_steer_off_mps: 8,
  max_steer_deg: 90,
  max_substep_s: 1 / 240,
  substep_travel_m: 0.05,
  slow_substep_s: 0.1,
  max_substeps: 400,
  solver_iterations: 6,
  tip_angle_deg: 45,
  lookahead_m: 0.5,
  slope_probe_m: 1.0,
  max_dt_s: 0.1,
  boost_duration_s: 6,
  boost_recharge_s: 10,
};

/** Buggy geometry and handling (LRV-like: 2.3 m wheelbase, 1.8 m track; matches BUGGY_DIMENSIONS in buggyModel.ts). */
export const BUGGY_PARAMS: Partial<RoverParams> = {
  wheelbase_m: 2.3,
  track_m: 1.8,
  wheel_radius_m: 0.42,
  cg_height_m: 0.75,
  body_length_m: 3.3,
  body_width_m: 2.17,
  body_height_m: 1.2,
  hull_half_length_m: 1.6,
  hull_half_width_m: 0.95,
  hull_bottom_m: 0.38,
  hull_top_m: 1.9,
  hull_top_half_length_m: 0.9,
  hull_top_half_width_m: 0.65,
  turn_rate_deg_s: 60,
  max_steer_deg: 35,
  tip_angle_deg: 50,
};

export interface DriveInput {
  /** -1 (full reverse) .. 1 (full forward). */
  throttle: number;
  /** -1 (turn left) .. 1 (turn right). */
  steer: number;
  /** Hold for boost (profiles with boost_speed_mps only). */
  boost?: boolean;
  /** Hold to brake (handbrake). */
  brake?: boolean;
}

/**
 * idle | driving | coasting | airborne (all wheels off the ground) | caution (past caution slope)
 * | steep (past the no-go policy slope; advisory) | slipping (tyres at the grip limit: wheelspin
 * or cornering limit) | sliding (moving against the command or with brakes on) | tip_risk (roll
 * near or past roll-over) | blocked_edge (no terrain data).
 */
export type RoverStatus = 'idle' | 'driving' | 'coasting' | 'airborne' | 'caution' | 'steep' | 'slipping' | 'sliding' | 'tip_risk' | 'blocked_edge';

/** One wheel, in FL, FR, RL, RR order. */
export interface WheelState {
  /** Suspension compression from full droop, m (0 = hanging, travel = on the bump stop). */
  compression_m: number;
  /** Wheel position relative to static ride height, m (+ = pushed up). Drives the 3D model. */
  offset_m: number;
  /** Normal load on the tyre, N. */
  load_n: number;
  in_contact: boolean;
  /** Wheel steer angle, deg (+ = pointing right). */
  steer_deg: number;
  /** Wheel spin, rad/s mission time (+ = rolling forward). */
  spin_rad_s: number;
}

export interface RoverState extends SitePose {
  /** Signed mission speed along the body's forward axis, m/s (real-time equivalent; negative = reversing). */
  speed_mps: number;
  /** Signed on-screen speed, m per wall-clock second. */
  display_speed_mps: number;
  /** Slope of the plane through the ground under the four wheels, deg. */
  slope_deg: number;
  /** Terrain slope just beyond the leading axle in the travel (or facing) direction, deg. */
  ahead_slope_deg: number;
  status: RoverStatus;
  /** Plain-language note on what is limiting the rover (grip, power, slope policy); null if nothing. */
  stop_reason: string | null;
  /** Forces along the heading, N (for the HUD): gravity, motor (or brakes), rolling resistance, total grip mu*sum(Fz). */
  forces: { gravity: number; motor: number; resistance: number; grip_limit: number };
  /** Mission (simulated) elapsed seconds while driving. */
  mission_time_s: number;
  odometer_m: number;
  /** Current steering input, -1..1. */
  steer: number;
  boost_active: boolean;
  /** Boost charge, 0..1. */
  boost_charge: number;
  /** Viewer time-lapse multiplier on top of display_speed_factor. */
  time_scale: number;
  /** All four wheels off the ground. */
  airborne: boolean;
  /** Vertical velocity of the centre of mass, m/s (+ up). */
  vz_mps: number;
  /** Seconds of the current (or last) flight, and the last landing's speed into the ground, m/s. */
  air_time_s: number;
  last_impact_mps: number;
  /** FL, FR, RL, RR. */
  wheels: WheelState[];
}

export interface Footprint {
  z: number;
  pitch_deg: number;
  roll_deg: number;
  slope_deg: number;
  /** Heights at front-left, front-right, rear-left, rear-right wheels. */
  wheels: [number, number, number, number];
}

/** Ground under the four corner wheels and the plane through them (terrain only, no suspension). */
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

/** Suspension constants derived from the profile: k = (m/4)(2 pi f)^2, c = 2 zeta sqrt(k m/4), static sag = m g / (4 k). */
export interface SuspensionConstants {
  k_n_per_m: number;
  c_ns_per_m: number;
  static_sag_m: number;
  travel_m: number;
  /** Principal moments of inertia (pitch about right axis, roll about forward axis, yaw), kg m^2. */
  inertia_kg_m2: [number, number, number];
}

export function suspensionConstants(profile: RoverProfile, params: RoverParams): SuspensionConstants {
  const m = profile.mass_kg;
  const f = profile.suspension_hz ?? 1.15;
  const zeta = profile.damping_ratio ?? 0.4;
  const travel = profile.suspension_travel_m ?? 0.25;
  const w = 2 * Math.PI * f;
  const k = (m / 4) * w * w;
  const c = 2 * zeta * Math.sqrt(k * (m / 4));
  const L = params.body_length_m, W = params.body_width_m, H = params.body_height_m;
  return {
    k_n_per_m: k,
    c_ns_per_m: c,
    static_sag_m: Math.min((m * MARS_G) / (4 * k), travel * 0.8),
    travel_m: travel,
    inertia_kg_m2: [(m / 12) * (L * L + H * H), (m / 12) * (W * W + H * H), (m / 12) * (L * L + W * W)],
  };
}

export interface InitialPose {
  x: number;
  y: number;
  heading_deg?: number;
}

/** Per-sub-step drive command (computed once per sub-step). */
interface Ctl {
  braked: boolean;
  driveTotal: number;
  /** Skid-steer yaw-rate command about body up, rad/s (NaN = car steering). */
  yawCmd: number;
  steerKappa: number;
  rearBlend: number;
  throttle: number;
  steerIn: number;
}

const WHEEL_X = [-1, 1, -1, 1]; // FL, FR, RL, RR: right sign
const WHEEL_Y = [1, 1, -1, -1]; // forward sign
const N_HULL = 8;

export class RoverPhysics {
  sampler: HeightSampler;
  profile: RoverProfile;
  params: RoverParams;
  private s: RoverState;

  // Rigid body (site frame): centre of mass, velocity, orientation (w, x, y, z), world angular velocity.
  private px = 0; private py = 0; private pz = 0;
  private vx = 0; private vy = 0; private vz = 0;
  private qw = 1; private qx = 0; private qy = 0; private qz = 0;
  private wx = 0; private wy = 0; private wz = 0;
  /** Rotation body -> world, row-major; columns are body right, forward, up. */
  private M = new Float64Array(9);
  private steerAct = 0;
  private settling = false;
  private lastSuspForce = new Float64Array(4);

  // Per-wheel scratch (one sub-step).
  private wc = new Uint8Array(4);
  private wComp = new Float64Array(4);
  private wUn = new Float64Array(4);
  private wN = new Float64Array(12);
  private wRw = new Float64Array(12);
  private wRp = new Float64Array(12);
  private wFw = new Float64Array(12);
  private wLw = new Float64Array(12);
  private wA = new Float64Array(4);
  private wKl = new Float64Array(4);
  private wKt = new Float64Array(4);
  private wDrive = new Float64Array(4);
  private wDriveEff = new Float64Array(4);
  private lamS = new Float64Array(4);
  private lamB = new Float64Array(4);
  private lamL = new Float64Array(4);
  private lamT = new Float64Array(4);
  private wSat = new Uint8Array(4);
  private wSteer = new Float64Array(4);
  private wSpin = new Float64Array(4);
  /** Braked-wheel anchors (world contact point where the tyre stopped); keeps a parked rover from creeping. */
  private anchor = new Float64Array(12);
  private anchorOk = new Uint8Array(4);
  private wBl = new Float64Array(4);
  private wBt = new Float64Array(4);
  // Hull contacts.
  private hc = new Uint8Array(N_HULL);
  private hN = new Float64Array(3 * N_HULL);
  private hR = new Float64Array(3 * N_HULL);
  private hT1 = new Float64Array(3 * N_HULL);
  private hT2 = new Float64Array(3 * N_HULL);
  private hPen = new Float64Array(N_HULL);
  private hLn = new Float64Array(N_HULL);
  private hL1 = new Float64Array(N_HULL);
  private hL2 = new Float64Array(N_HULL);
  // Ground sample scratch.
  private gh = 0; private gnx = 0; private gny = 0; private gnz = 1;
  private hullContacts = 0;

  constructor(sampler: HeightSampler, profile: RoverProfile = DEFAULT_ROVER_PROFILE, params: Partial<RoverParams> = {}, start: InitialPose = { x: 0, y: 0 }) {
    this.sampler = sampler;
    this.profile = profile;
    this.params = { ...DEFAULT_ROVER_PARAMS, ...params };
    this.s = {
      x: start.x, y: start.y, z: 0, heading_deg: normalizeHeading(start.heading_deg ?? 0), pitch_deg: 0, roll_deg: 0,
      speed_mps: 0, display_speed_mps: 0, slope_deg: 0, ahead_slope_deg: 0,
      status: 'idle', stop_reason: null, mission_time_s: 0, odometer_m: 0,
      steer: 0, boost_active: false, boost_charge: 1, time_scale: 1,
      airborne: false, vz_mps: 0, air_time_s: 0, last_impact_mps: 0,
      forces: { gravity: 0, motor: 0, resistance: 0, grip_limit: 0 },
      wheels: [0, 1, 2, 3].map(() => ({ compression_m: 0, offset_m: 0, load_n: 0, in_contact: false, steer_deg: 0, spin_rad_s: 0 })),
    };
    this.settle();
  }

  get state(): Readonly<RoverState> {
    return this.s;
  }

  /** Spring rate, damper rate, static sag, travel and inertia in use. */
  get suspension(): SuspensionConstants {
    return suspensionConstants(this.profile, this.params);
  }

  /** Place the rover (teleport or hand-over from auto mode), at rest on its wheels. */
  setPose(x: number, y: number, heading_deg: number): void {
    this.s.x = x;
    this.s.y = y;
    this.s.heading_deg = normalizeHeading(heading_deg);
    this.s.stop_reason = null;
    this.s.status = 'idle';
    this.s.air_time_s = 0;
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

  /**
   * Put the body at rest on the terrain at the current x, y, heading: start from the
   * plane through the four wheel contacts at static sag, then let it settle on its
   * springs with the brakes on (critically damped) and zero the velocities.
   */
  settle(): void {
    const sc = this.suspension;
    const p = this.params;
    const s = this.s;
    const f = footprint(this.sampler, s.x, s.y, s.heading_deg, p);
    this.setOrientation(s.heading_deg, f.pitch_deg, f.roll_deg);
    const M = this.M;
    const lift = sc.static_sag_m * (1 - Math.cos(f.slope_deg * DEG)) + p.cg_height_m;
    this.px = s.x + M[2] * lift;
    this.py = s.y + M[5] * lift;
    this.pz = f.z + M[8] * lift;
    this.vx = this.vy = this.vz = this.wx = this.wy = this.wz = 0;
    this.steerAct = 0;
    this.lastSuspForce.fill((this.profile.mass_kg * MARS_G) / 4);
    const h = this.substepLength();
    const n = Math.min(400, Math.max(40, Math.ceil(1.0 / h)));
    const ctl: Ctl = { braked: true, driveTotal: 0, yawCmd: NaN, steerKappa: 0, rearBlend: 1, throttle: 0, steerIn: 0 };
    this.settling = true;
    for (let i = 0; i < n; i++) this.substep(h, ctl);
    this.settling = false;
    this.vx = this.vy = this.vz = this.wx = this.wy = this.wz = 0;
    this.wSpin.fill(0);
    this.writePose();
    s.speed_mps = 0;
    s.display_speed_mps = 0;
    s.vz_mps = 0;
    s.airborne = false;
    const g = footprint(this.sampler, s.x, s.y, s.heading_deg, p);
    s.slope_deg = g.slope_deg;
    s.ahead_slope_deg = this.aheadSlope(1);
  }

  private aheadSlope(dirSign: number): number {
    const [fx, fy] = headingToDir(this.s.heading_deg);
    const d = dirSign * (this.params.wheelbase_m / 2 + this.params.lookahead_m);
    return terrainSlopeDeg(this.sampler, this.s.x + fx * d, this.s.y + fy * d, this.params.slope_probe_m);
  }

  private inside(x: number, y: number): boolean {
    return this.sampler.contains ? this.sampler.contains(x, y) : true;
  }

  /** Nominal sub-step: 1/240 s, or longer for slow rovers (at most substep_travel_m per step at top speed). */
  private substepLength(): number {
    const p = this.params;
    const top = Math.max(this.profile.boost_speed_mps ?? 0, this.profile.flat_speed_mps, 1e-3);
    return Math.max(p.max_substep_s, Math.min(p.slow_substep_s, p.substep_travel_m / top));
  }

  // ------------------------------------------------------------------ rigid-body helpers

  private setOrientation(heading_deg: number, pitch_deg: number, roll_deg: number): void {
    // q = Rz(-heading) * Rx(pitch) * Ry(roll): yaw clockwise from north, nose up, right side down.
    const a = -heading_deg * DEG / 2, b = pitch_deg * DEG / 2, c = roll_deg * DEG / 2;
    const cz = Math.cos(a), sz = Math.sin(a), cx = Math.cos(b), sx = Math.sin(b), cy = Math.cos(c), sy = Math.sin(c);
    // qz * qx
    const w1 = cz * cx, x1 = cz * sx, y1 = sz * sx, z1 = sz * cx;
    // (qz qx) * qy, qy = (cy, 0, sy, 0)
    this.qw = w1 * cy - y1 * sy;
    this.qx = x1 * cy - z1 * sy;
    this.qy = w1 * sy + y1 * cy;
    this.qz = z1 * cy + x1 * sy;
    this.updateRot();
  }

  private updateRot(): void {
    const { qw: w, qx: x, qy: y, qz: z } = this;
    const M = this.M;
    M[0] = 1 - 2 * (y * y + z * z); M[1] = 2 * (x * y - w * z); M[2] = 2 * (x * z + w * y);
    M[3] = 2 * (x * y + w * z); M[4] = 1 - 2 * (x * x + z * z); M[5] = 2 * (y * z - w * x);
    M[6] = 2 * (x * z - w * y); M[7] = 2 * (y * z + w * x); M[8] = 1 - 2 * (x * x + y * y);
  }

  /** (r x d) . I^-1 (r x e) in world frame, using the body-frame principal inertia. */
  private angTerm(rx: number, ry: number, rz: number, dx: number, dy: number, dz: number, ex: number, ey: number, ez: number, I: [number, number, number]): number {
    const M = this.M;
    const ax = ry * dz - rz * dy, ay = rz * dx - rx * dz, az = rx * dy - ry * dx;
    const bx = ry * ez - rz * ey, by = rz * ex - rx * ez, bz = rx * ey - ry * ex;
    let t = 0;
    for (let c = 0; c < 3; c++) {
      const ab = M[c] * ax + M[3 + c] * ay + M[6 + c] * az;
      const bb = M[c] * bx + M[3 + c] * by + M[6 + c] * bz;
      t += (ab * bb) / I[c];
    }
    return t;
  }

  /** Apply impulse j at offset r from the centre of mass. */
  private impulse(jx: number, jy: number, jz: number, rx: number, ry: number, rz: number, invM: number, I: [number, number, number]): void {
    this.vx += jx * invM;
    this.vy += jy * invM;
    this.vz += jz * invM;
    const lx = ry * jz - rz * jy, ly = rz * jx - rx * jz, lz = rx * jy - ry * jx;
    const M = this.M;
    const b0 = (M[0] * lx + M[3] * ly + M[6] * lz) / I[0];
    const b1 = (M[1] * lx + M[4] * ly + M[7] * lz) / I[1];
    const b2 = (M[2] * lx + M[5] * ly + M[8] * lz) / I[2];
    this.wx += M[0] * b0 + M[1] * b1 + M[2] * b2;
    this.wy += M[3] * b0 + M[4] * b1 + M[5] * b2;
    this.wz += M[6] * b0 + M[7] * b1 + M[8] * b2;
  }

  /** Velocity of the body point at offset r, dotted with d. */
  private pointVel(rx: number, ry: number, rz: number, dx: number, dy: number, dz: number): number {
    const ux = this.vx + this.wy * rz - this.wz * ry;
    const uy = this.vy + this.wz * rx - this.wx * rz;
    const uz = this.vz + this.wx * ry - this.wy * rx;
    return ux * dx + uy * dy + uz * dz;
  }

  /** Ground height and unit normal at (x, y); normal from central differences over +-e. */
  private ground(x: number, y: number, e: number): void {
    const S = this.sampler;
    this.gh = S.sampleHeight(x, y);
    const gx = (S.sampleHeight(x + e, y) - S.sampleHeight(x - e, y)) / (2 * e);
    const gy = (S.sampleHeight(x, y + e) - S.sampleHeight(x, y - e)) / (2 * e);
    const inv = 1 / Math.sqrt(gx * gx + gy * gy + 1);
    this.gnx = -gx * inv;
    this.gny = -gy * inv;
    this.gnz = inv;
  }

  // ------------------------------------------------------------------ one sub-step

  /** Returns false if the body would leave the terrain data (it is stopped instead). */
  private substep(h: number, ctl: Ctl): boolean {
    const prof = this.profile;
    const p = this.params;
    const sc = suspensionConstants(prof, p);
    const I = sc.inertia_kg_m2;
    const m = prof.mass_kg;
    const invM = 1 / m;
    const mu = prof.traction_mu;
    const crr = prof.rolling_coeff;
    const r = p.wheel_radius_m;
    const sag = sc.static_sag_m;
    const travel = sc.travel_m;
    const k = sc.k_n_per_m;
    const cd = this.settling ? 2 * Math.sqrt(k * m / 4) : sc.c_ns_per_m;
    const gamma = k * h + cd;
    const M = this.M;

    // Gravity and the gyroscopic term (body frame: dw = -I^-1 (w x I w) h).
    this.vz -= MARS_G * h;
    {
      const b0 = M[0] * this.wx + M[3] * this.wy + M[6] * this.wz;
      const b1 = M[1] * this.wx + M[4] * this.wy + M[7] * this.wz;
      const b2 = M[2] * this.wx + M[5] * this.wy + M[8] * this.wz;
      const d0 = -((I[2] - I[1]) * b1 * b2) / I[0] * h;
      const d1 = -((I[0] - I[2]) * b2 * b0) / I[1] * h;
      const d2 = -((I[1] - I[0]) * b0 * b1) / I[2] * h;
      this.wx += M[0] * d0 + M[1] * d1 + M[2] * d2;
      this.wy += M[3] * d0 + M[4] * d1 + M[5] * d2;
      this.wz += M[6] * d0 + M[7] * d1 + M[8] * d2;
    }

    const Rx = M[0], Ry = M[3], Rz = M[6];
    const Fx = M[1], Fy = M[4], Fz = M[7];
    const Ux = M[2], Uy = M[5], Uz = M[8];
    const ox = this.px - Ux * p.cg_height_m, oy = this.py - Uy * p.cg_height_m, oz = this.pz - Uz * p.cg_height_m;
    const halfB = p.track_m / 2, halfA = p.wheelbase_m / 2;
    const vfwd = this.vx * Fx + this.vy * Fy + this.vz * Fz;
    const e = Math.max(0.2, r * 0.7);

    // --- wheel contacts and tyre axes
    let loadSum = 0;
    for (let i = 0; i < 4; i++) {
      this.lamS[i] = this.lamB[i] = this.lamL[i] = this.lamT[i] = 0;
      this.wSat[i] = 0;
      const bx = WHEEL_X[i] * halfB, by = WHEEL_Y[i] * halfA;
      // Steer angle (+ = right).
      let delta = 0;
      if (Number.isNaN(ctl.yawCmd)) {
        const yc = -halfA * (1 - ctl.rearBlend);
        const kap = ctl.steerKappa;
        delta = Math.atan2(kap * (by - yc), 1 - kap * bx);
      } else if (ctl.yawCmd !== 0 || vfwd !== 0) {
        // Point each wheel along its velocity for the commanded (forward speed, yaw rate): turn in place.
        const dx = -ctl.yawCmd * by, dy = vfwd + ctl.yawCmd * bx;
        if (Math.hypot(dx, dy) > 1e-9) {
          delta = Math.atan2(dx, dy);
          if (delta > Math.PI / 2) delta -= Math.PI;
          else if (delta < -Math.PI / 2) delta += Math.PI;
        } else delta = this.wSteer[i];
      }
      const md = p.max_steer_deg * DEG;
      delta = Math.max(-md, Math.min(md, delta));
      this.wSteer[i] = delta;

      // Wheel centre at full droop.
      const lz = r - sag;
      const W0x = ox + Rx * bx + Fx * by + Ux * lz;
      const W0y = oy + Ry * bx + Fy * by + Uy * lz;
      const W0z = oz + Rz * bx + Fz * by + Uz * lz;
      this.ground(W0x, W0y, e);
      const nx = this.gnx, ny = this.gny, nz = this.gnz;
      const un = Ux * nx + Uy * ny + Uz * nz;
      const s0 = (W0z - this.gh) * nz;
      const comp = un > 0.25 ? (r - s0) / un : -1;
      if (!(comp > 0)) {
        this.wc[i] = 0;
        this.wComp[i] = 0;
        this.anchorOk[i] = 0;
        continue;
      }
      this.wc[i] = 1;
      this.wComp[i] = comp;
      this.wUn[i] = un;
      const cl = Math.min(comp, travel);
      const Wx = W0x + Ux * cl, Wy = W0y + Uy * cl, Wz = W0z + Uz * cl;
      const rwx = Wx - this.px, rwy = Wy - this.py, rwz = Wz - this.pz;
      const rpx = rwx - nx * r, rpy = rwy - ny * r, rpz = rwz - nz * r;
      const j3 = 3 * i;
      this.wN[j3] = nx; this.wN[j3 + 1] = ny; this.wN[j3 + 2] = nz;
      this.wRw[j3] = rwx; this.wRw[j3 + 1] = rwy; this.wRw[j3 + 2] = rwz;
      this.wRp[j3] = rpx; this.wRp[j3 + 1] = rpy; this.wRp[j3 + 2] = rpz;
      // Tyre forward axis in the ground plane, lateral = n x forward.
      const sd = Math.sin(delta), cdl = Math.cos(delta);
      let fwx = Rx * sd + Fx * cdl, fwy = Ry * sd + Fy * cdl, fwz = Rz * sd + Fz * cdl;
      const fn = fwx * nx + fwy * ny + fwz * nz;
      fwx -= nx * fn; fwy -= ny * fn; fwz -= nz * fn;
      const fl = Math.hypot(fwx, fwy, fwz) || 1;
      fwx /= fl; fwy /= fl; fwz /= fl;
      const lwx = ny * fwz - nz * fwy, lwy = nz * fwx - nx * fwz, lwz = nx * fwy - ny * fwx;
      this.wFw[j3] = fwx; this.wFw[j3 + 1] = fwy; this.wFw[j3 + 2] = fwz;
      this.wLw[j3] = lwx; this.wLw[j3 + 1] = lwy; this.wLw[j3 + 2] = lwz;
      // The ground pushes along its normal n: a spring force F along the strut needs a normal force F / (U.n)
      // (the linkage carries the rest). Response of the compression rate to a strut impulse lambda:
      // d(cdot)/d(lambda) = -(1/m + (rw x n) I^-1 (rw x n)) / (U.n)^2.
      this.wA[i] = -(invM + this.angTerm(rwx, rwy, rwz, nx, ny, nz, nx, ny, nz, I)) / (un * un);
      this.wKl[i] = 1 / (invM + this.angTerm(rpx, rpy, rpz, fwx, fwy, fwz, fwx, fwy, fwz, I));
      this.wKt[i] = 1 / (invM + this.angTerm(rpx, rpy, rpz, lwx, lwy, lwz, lwx, lwy, lwz, I));
      // Static hold: a braked tyre that is not sliding is pulled back to where it stopped (removes solver creep).
      this.wBl[i] = this.wBt[i] = 0;
      if (ctl.braked) {
        const Px = this.px + rpx, Py = this.py + rpy, Pz = this.pz + rpz;
        if (this.anchorOk[i]) {
          const dx = Px - this.anchor[j3], dy = Py - this.anchor[j3 + 1], dz = Pz - this.anchor[j3 + 2];
          if (dx * dx + dy * dy + dz * dz > 0.05 * 0.05) this.anchorOk[i] = 0;
          else {
            this.wBl[i] = (-0.2 * (dx * fwx + dy * fwy + dz * fwz)) / h;
            this.wBt[i] = (-0.2 * (dx * lwx + dy * lwy + dz * lwz)) / h;
          }
        }
        if (!this.anchorOk[i]) {
          this.anchor[j3] = Px; this.anchor[j3 + 1] = Py; this.anchor[j3 + 2] = Pz;
          this.anchorOk[i] = 1;
        }
      } else this.anchorOk[i] = 0;
      loadSum += this.lastSuspForce[i];
    }

    // --- drive force, shared by wheel load (all-wheel drive)
    let nc = 0;
    for (let i = 0; i < 4; i++) nc += this.wc[i];
    for (let i = 0; i < 4; i++) {
      this.wDrive[i] = 0;
      this.wDriveEff[i] = 0;
      if (!this.wc[i] || ctl.driveTotal === 0) continue;
      const share = loadSum > 1e-6 ? this.lastSuspForce[i] / loadSum : 1 / nc;
      this.wDrive[i] = ctl.driveTotal * share * h;
    }

    // --- chassis corner contacts (belly, roll cage)
    this.hullContacts = 0;
    for (let j = 0; j < N_HULL; j++) {
      this.hc[j] = 0;
      const top = j >= 4;
      const sx = (j & 1 ? 1 : -1) * (top ? p.hull_top_half_width_m : p.hull_half_width_m);
      const sy = (j & 2 ? 1 : -1) * (top ? p.hull_top_half_length_m : p.hull_half_length_m);
      const sz = top ? p.hull_top_m : p.hull_bottom_m;
      const qx = ox + Rx * sx + Fx * sy + Ux * sz;
      const qy = oy + Ry * sx + Fy * sy + Uy * sz;
      const qz = oz + Rz * sx + Fz * sy + Uz * sz;
      const gz = this.sampler.sampleHeight(qx, qy);
      if (!(qz < gz)) continue;
      this.ground(qx, qy, 0.3);
      const nx = this.gnx, ny = this.gny, nz = this.gnz;
      this.hc[j] = 1;
      this.hullContacts++;
      this.hPen[j] = (gz - qz) * nz;
      const j3 = 3 * j;
      this.hN[j3] = nx; this.hN[j3 + 1] = ny; this.hN[j3 + 2] = nz;
      this.hR[j3] = qx - this.px; this.hR[j3 + 1] = qy - this.py; this.hR[j3 + 2] = qz - this.pz;
      // Tangents.
      let t1x = 1 - nx * nx, t1y = -nx * ny, t1z = -nx * nz;
      const tl = Math.hypot(t1x, t1y, t1z) || 1;
      t1x /= tl; t1y /= tl; t1z /= tl;
      this.hT1[j3] = t1x; this.hT1[j3 + 1] = t1y; this.hT1[j3 + 2] = t1z;
      this.hT2[j3] = ny * t1z - nz * t1y; this.hT2[j3 + 1] = nz * t1x - nx * t1z; this.hT2[j3 + 2] = nx * t1y - ny * t1x;
      this.hLn[j] = this.hL1[j] = this.hL2[j] = 0;
    }

    // Warm start: spring force from the last step, drive impulses.
    for (let i = 0; i < 4; i++) {
      if (!this.wc[i]) continue;
      const j3 = 3 * i;
      const comp = this.wComp[i];
      const ls = comp >= travel ? k * travel * h : Math.min(this.lastSuspForce[i] * h, k * travel * h * 2);
      this.lamS[i] = ls;
      const ln = ls / this.wUn[i];
      this.impulse(this.wN[j3] * ln, this.wN[j3 + 1] * ln, this.wN[j3 + 2] * ln, this.wRw[j3], this.wRw[j3 + 1], this.wRw[j3 + 2], invM, I);
      const ld = this.wDrive[i];
      this.lamL[i] = ld;
      if (ld !== 0) this.impulse(this.wFw[j3] * ld, this.wFw[j3 + 1] * ld, this.wFw[j3 + 2] * ld, this.wRp[j3], this.wRp[j3 + 1], this.wRp[j3 + 2], invM, I);
    }

    // --- sequential impulses
    const iters = p.solver_iterations;
    for (let it = 0; it < iters; it++) {
      for (let i = 0; i < 4; i++) {
        if (!this.wc[i]) continue;
        const j3 = 3 * i;
        const nx = this.wN[j3], ny = this.wN[j3 + 1], nz = this.wN[j3 + 2];
        const rwx = this.wRw[j3], rwy = this.wRw[j3 + 1], rwz = this.wRw[j3 + 2];
        const comp = this.wComp[i];
        const cdot = -this.pointVel(rwx, rwy, rwz, nx, ny, nz) / this.wUn[i];
        const a = this.wA[i];
        if (comp < travel) {
          // Implicit spring-damper: lambda/h = k x + (k h + c) xdot_new.
          const dl = (k * comp + gamma * cdot - this.lamS[i] / h) / (1 / h - gamma * a);
          const nl = Math.max(0, this.lamS[i] + dl);
          const d = nl - this.lamS[i];
          this.lamS[i] = nl;
          const dn = d / this.wUn[i];
          if (d !== 0) this.impulse(nx * dn, ny * dn, nz * dn, rwx, rwy, rwz, invM, I);
        } else {
          // Bump stop: no further compression (plus a gentle push-out of any overshoot).
          const bias = Math.min(1, (0.2 * (comp - travel)) / h);
          const dl = (cdot + bias) / -a;
          const nl = Math.max(0, this.lamB[i] + dl);
          const d = nl - this.lamB[i];
          this.lamB[i] = nl;
          const dn = d / this.wUn[i];
          if (d !== 0) this.impulse(nx * dn, ny * dn, nz * dn, rwx, rwy, rwz, invM, I);
        }

        // Tyre: longitudinal (drive +- rolling resistance, or brakes) and lateral, inside mu * Fz.
        const fzh = (this.lamS[i] + this.lamB[i]) / this.wUn[i]; // normal impulse
        const lim = mu * fzh;
        const rpx = this.wRp[j3], rpy = this.wRp[j3 + 1], rpz = this.wRp[j3 + 2];
        const fwx = this.wFw[j3], fwy = this.wFw[j3 + 1], fwz = this.wFw[j3 + 2];
        const lwx = this.wLw[j3], lwy = this.wLw[j3 + 1], lwz = this.wLw[j3 + 2];
        const vl = this.pointVel(rpx, rpy, rpz, fwx, fwy, fwz) - this.wBl[i];
        const vt = this.pointVel(rpx, rpy, rpz, lwx, lwy, lwz) - this.wBt[i];
        let nl = this.lamL[i] - vl * this.wKl[i];
        let driveSat = false;
        if (ctl.braked) nl = Math.max(-lim, Math.min(lim, nl));
        else {
          // Motor torque beyond mu * Fz just spins the wheel; rolling resistance acts on top.
          const ld = Math.max(-lim, Math.min(lim, this.wDrive[i]));
          driveSat = ld !== this.wDrive[i];
          this.wDriveEff[i] = ld;
          nl = Math.max(ld - crr * fzh, Math.min(ld + crr * fzh, nl));
        }
        let nt = this.lamT[i] - vt * this.wKt[i];
        const mag = Math.hypot(nl, nt);
        if (mag > lim) {
          const sc2 = lim / mag;
          nl *= sc2;
          nt *= sc2;
          this.wSat[i] = 1;
          if (it === iters - 1) this.anchorOk[i] = 0; // sliding: let go of the anchor
        } else this.wSat[i] = driveSat ? 1 : 0;
        const dL = nl - this.lamL[i], dT = nt - this.lamT[i];
        this.lamL[i] = nl;
        this.lamT[i] = nt;
        this.impulse(fwx * dL + lwx * dT, fwy * dL + lwy * dT, fwz * dL + lwz * dT, rpx, rpy, rpz, invM, I);
      }

      for (let j = 0; j < N_HULL; j++) {
        if (!this.hc[j]) continue;
        const j3 = 3 * j;
        const rx = this.hR[j3], ry = this.hR[j3 + 1], rz = this.hR[j3 + 2];
        const nx = this.hN[j3], ny = this.hN[j3 + 1], nz = this.hN[j3 + 2];
        const kn = invM + this.angTerm(rx, ry, rz, nx, ny, nz, nx, ny, nz, I);
        const vn = this.pointVel(rx, ry, rz, nx, ny, nz);
        const bias = Math.min(2, (0.2 * this.hPen[j]) / h);
        const nln = Math.max(0, this.hLn[j] + (bias - vn) / kn);
        const d = nln - this.hLn[j];
        this.hLn[j] = nln;
        if (d !== 0) this.impulse(nx * d, ny * d, nz * d, rx, ry, rz, invM, I);
        // Friction (scraping chassis), mu * normal impulse.
        const lim = mu * this.hLn[j];
        const t1x = this.hT1[j3], t1y = this.hT1[j3 + 1], t1z = this.hT1[j3 + 2];
        const t2x = this.hT2[j3], t2y = this.hT2[j3 + 1], t2z = this.hT2[j3 + 2];
        const k1 = invM + this.angTerm(rx, ry, rz, t1x, t1y, t1z, t1x, t1y, t1z, I);
        const k2 = invM + this.angTerm(rx, ry, rz, t2x, t2y, t2z, t2x, t2y, t2z, I);
        let a1 = this.hL1[j] - this.pointVel(rx, ry, rz, t1x, t1y, t1z) / k1;
        let a2 = this.hL2[j] - this.pointVel(rx, ry, rz, t2x, t2y, t2z) / k2;
        const mg = Math.hypot(a1, a2);
        if (mg > lim) { a1 *= lim / mg; a2 *= lim / mg; }
        const d1 = a1 - this.hL1[j], d2 = a2 - this.hL2[j];
        this.hL1[j] = a1;
        this.hL2[j] = a2;
        this.impulse(t1x * d1 + t2x * d2, t1y * d1 + t2y * d2, t1z * d1 + t2z * d2, rx, ry, rz, invM, I);
      }
    }

    // Skid steer (scout): the corner wheels point along the turn; the yaw rate itself is commanded.
    if (!Number.isNaN(ctl.yawCmd) && nc >= 2) {
      const wu = this.wx * Ux + this.wy * Uy + this.wz * Uz;
      const d = ctl.yawCmd - wu;
      this.wx += Ux * d;
      this.wy += Uy * d;
      this.wz += Uz * d;
    }

    // Book-keeping for the next step, wheel spin and forces.
    for (let i = 0; i < 4; i++) {
      if (!this.wc[i]) {
        this.lastSuspForce[i] = 0;
        this.wSpin[i] *= ctl.braked ? 0 : Math.exp(-h * 0.5);
        continue;
      }
      this.lastSuspForce[i] = (this.lamS[i] + this.lamB[i]) / h;
      const j3 = 3 * i;
      const vl = this.pointVel(this.wRp[j3], this.wRp[j3 + 1], this.wRp[j3 + 2], this.wFw[j3], this.wFw[j3 + 1], this.wFw[j3 + 2]);
      let spin = ctl.braked ? 0 : vl / r;
      if (!ctl.braked && this.wSat[i] && ctl.driveTotal !== 0) spin += Math.sign(ctl.driveTotal) * 2 / r; // wheelspin (visual)
      this.wSpin[i] = spin;
    }

    // Integrate: position, then orientation from the new angular velocity.
    const nxp = this.px + this.vx * h, nyp = this.py + this.vy * h, nzp = this.pz + this.vz * h;
    const nox = nxp - Ux * p.cg_height_m, noy = nyp - Uy * p.cg_height_m;
    if (!this.inside(nox, noy)) {
      this.vx = this.vy = this.vz = this.wx = this.wy = this.wz = 0;
      return false;
    }
    this.px = nxp; this.py = nyp; this.pz = nzp;
    const { wx, wy, wz } = this;
    const { qw, qx, qy, qz } = this;
    const hh = 0.5 * h;
    let w2 = qw + hh * (-wx * qx - wy * qy - wz * qz);
    let x2 = qx + hh * (wx * qw + wy * qz - wz * qy);
    let y2 = qy + hh * (wy * qw + wz * qx - wx * qz);
    let z2 = qz + hh * (wz * qw + wx * qy - wy * qx);
    const ql = 1 / Math.sqrt(w2 * w2 + x2 * x2 + y2 * y2 + z2 * z2);
    w2 *= ql; x2 *= ql; y2 *= ql; z2 *= ql;
    this.qw = w2; this.qx = x2; this.qy = y2; this.qz = z2;
    this.updateRot();
    return true;
  }

  /** Body pose (origin = ground point under the body at static ride height) into the public state. */
  private writePose(): void {
    const s = this.s;
    const M = this.M;
    const p = this.params;
    const sc = suspensionConstants(this.profile, p);
    s.x = this.px - M[2] * p.cg_height_m;
    s.y = this.py - M[5] * p.cg_height_m;
    s.z = this.pz - M[8] * p.cg_height_m;
    const fx = M[1], fy = M[4], fz = M[7];
    if (Math.abs(fx) + Math.abs(fy) > 1e-9) s.heading_deg = normalizeHeading(Math.atan2(fx, fy) / DEG);
    s.pitch_deg = Math.asin(Math.max(-1, Math.min(1, fz))) / DEG;
    s.roll_deg = Math.atan2(-M[6], M[8]) / DEG;
    s.vz_mps = this.vz;
    for (let i = 0; i < 4; i++) {
      const w = s.wheels[i];
      const c = this.wc[i] ? Math.min(this.wComp[i], sc.travel_m) : 0;
      w.compression_m = c;
      w.offset_m = c - sc.static_sag_m;
      w.load_n = this.wc[i] ? this.lastSuspForce[i] / this.wUn[i] : 0;
      w.in_contact = !!this.wc[i];
      w.steer_deg = this.wSteer[i] / DEG;
      w.spin_rad_s = this.wSpin[i];
    }
  }

  step(dtWall: number, input: DriveInput): Readonly<RoverState> {
    const p = this.params;
    const prof = this.profile;
    const s = this.s;
    const dt = Math.min(Math.max(dtWall, 0), p.max_dt_s);
    const throttle = Math.max(-1, Math.min(1, input.throttle || 0));
    const steerIn = Math.max(-1, Math.min(1, input.steer || 0));
    s.steer = steerIn;

    // Boost: drains while held with throttle forward, recharges otherwise.
    const canBoost = !!prof.boost_speed_mps && !!input.boost && throttle > 0 && s.boost_charge > 0;
    s.boost_active = canBoost;
    if (canBoost) s.boost_charge = Math.max(0, s.boost_charge - dt / p.boost_duration_s);
    else s.boost_charge = Math.min(1, s.boost_charge + dt / p.boost_recharge_s);
    const cap = canBoost ? prof.boost_speed_mps! : prof.flat_speed_mps;
    const power = prof.power_w * (canBoost ? prof.boost_power_factor ?? 1 : 1);

    const rate = prof.display_speed_factor * s.time_scale; // mission seconds per wall second
    const tMission = dt * rate;
    const hNom = this.substepLength();
    let n = Math.ceil(tMission / hNom - 1e-9);
    if (n > p.max_substeps) n = p.max_substeps;
    const h = n > 0 ? tMission / n : 0;
    const m = prof.mass_kg;
    const skid = !prof.min_turn_radius_m;
    const vStop = Math.max(0.02 * cap, 0.002);
    let edge = false;
    let gripLimited = false;
    let cornerLimited = false;
    let braked = false;
    let motorSum = 0, resistSum = 0;
    let landedImpact = -1;

    for (let i = 0; i < n; i++) {
      const M = this.M;
      const vfwd = this.vx * M[1] + this.vy * M[4] + this.vz * M[7];
      // Brakes: held, auto (scout motors hold), or throttle against the motion (S brakes, then reverses).
      const reversing = throttle !== 0 && vfwd * Math.sign(throttle) < -vStop;
      const brk = !!input.brake || reversing || (!!prof.auto_brake && throttle === 0 && (steerIn === 0 || !skid));
      let drive = 0;
      if (!brk && throttle !== 0) {
        const dir = Math.sign(throttle);
        let loads = 0;
        for (let w = 0; w < 4; w++) loads += this.lastSuspForce[w];
        const target = dir * cap * Math.abs(throttle);
        // Speed controller: full torque until within 5% of the target speed, then proportional (implicit in h,
        // so stable for any step), plus feed-forward of grade and rolling resistance.
        const gain = (prof.traction_mu * Math.max(loads, m * MARS_G * 0.25)) / (0.05 * cap);
        const ctlF = (gain * (target - vfwd)) / (1 + (gain * h) / m) + m * MARS_G * M[7] + dir * prof.rolling_coeff * loads;
        const fMax = Math.abs(throttle) * power / Math.max(Math.abs(vfwd), 0.05 * cap);
        drive = dir * Math.max(0, Math.min(fMax, dir * ctlF));
      }
      // Steering slews toward the input; car steering uses curvature, skid steer a yaw rate.
      const slew = h / (p.steer_lock_s * Math.max(prof.display_speed_factor, 1));
      this.steerAct += Math.max(-slew, Math.min(slew, steerIn - this.steerAct));
      const sp = Math.abs(vfwd);
      const ctl: Ctl = {
        braked: brk,
        driveTotal: drive,
        yawCmd: skid ? (-this.steerAct * p.turn_rate_deg_s * DEG) / Math.max(prof.display_speed_factor, 1e-9) : NaN,
        steerKappa: skid ? 0 : this.steerAct / prof.min_turn_radius_m!,
        rearBlend: Math.max(0, Math.min(1, (p.rear_steer_off_mps - sp) / (p.rear_steer_off_mps - p.rear_steer_full_mps))),
        throttle,
        steerIn,
      };
      const wasAir = s.airborne;
      // Speed into the ground before contact (for the landing report).
      const vInto = -this.vz;
      if (!this.substep(h, ctl)) {
        edge = true;
        break;
      }
      let contacts = 0, sat = 0, satDrive = 0;
      for (let w = 0; w < 4; w++) {
        contacts += this.wc[w];
        if (this.wSat[w]) {
          sat++;
          if (drive !== 0) satDrive++;
        }
      }
      s.airborne = contacts === 0 && this.hullContacts === 0;
      if (s.airborne) {
        if (!wasAir) s.air_time_s = 0;
        s.air_time_s += h;
      } else if (wasAir && s.air_time_s > 0.1) {
        landedImpact = Math.max(landedImpact, vInto);
      }
      if (brk) braked = true;
      if (satDrive >= 2 && steerIn === 0) gripLimited = true;
      if (sat >= 2 && steerIn !== 0 && sp > 2) cornerLimited = true;
      if (i === n - 1) {
        motorSum = 0;
        resistSum = 0;
        for (let w = 0; w < 4; w++) {
          if (!this.wc[w]) continue;
          if (brk) motorSum += this.lamL[w] / h;
          else {
            motorSum += this.wDriveEff[w] / h;
            resistSum += (this.lamL[w] - this.wDriveEff[w]) / h;
          }
        }
      }
      const speed3 = Math.hypot(this.vx, this.vy, this.vz);
      if (speed3 > 1e-3) s.odometer_m += speed3 * h;
      if (speed3 > 1e-3 || throttle !== 0) s.mission_time_s += h;
    }
    if (landedImpact >= 0) s.last_impact_mps = landedImpact;

    this.writePose();
    const M = this.M;
    const vfwd = this.vx * M[1] + this.vy * M[4] + this.vz * M[7];
    s.speed_mps = Math.abs(vfwd) < 1e-6 ? 0 : vfwd;
    s.display_speed_mps = s.speed_mps * rate;
    const f = footprint(this.sampler, s.x, s.y, s.heading_deg, p);
    s.slope_deg = f.slope_deg;
    s.ahead_slope_deg = this.aheadSlope(s.speed_mps < 0 ? -1 : throttle < 0 ? -1 : 1);
    let loads = 0;
    for (const w of s.wheels) loads += w.load_n;
    s.forces = { gravity: -m * MARS_G * M[7], motor: motorSum, resistance: resistSum, grip_limit: prof.traction_mu * loads };

    // Status and a plain-language note; nothing here stops the rover.
    const moving = Math.abs(s.speed_mps) > 0.02 * Math.max(prof.flat_speed_mps, 0.01);
    const against = (throttle !== 0 && !braked && s.speed_mps * throttle < -1e-3) || (braked && throttle === 0 && moving);
    const upZ = M[8];
    const vR = Math.abs(s.speed_mps);
    if (s.airborne && s.air_time_s > 0.1) {
      s.status = 'airborne';
      s.stop_reason = `airborne ${s.air_time_s.toFixed(1)} s, ${Math.max(0, s.z - f.z).toFixed(1)} m above ground`;
    } else if (edge) {
      s.status = 'blocked_edge';
      s.stop_reason = 'edge of terrain data';
    } else if (upZ < Math.cos(70 * DEG)) {
      s.status = 'tip_risk';
      s.stop_reason = `rolled over (roll ${s.roll_deg.toFixed(0)}°, pitch ${s.pitch_deg.toFixed(0)}°): reset the rover (R in the sandbox)`;
    } else if (Math.abs(s.roll_deg) > p.tip_angle_deg * 0.8) {
      s.status = 'tip_risk';
      s.stop_reason = `roll ${Math.abs(s.roll_deg).toFixed(0)}°, near roll-over (~${p.tip_angle_deg}°)`;
    } else if (against) {
      s.status = 'sliding';
      s.stop_reason = braked
        ? `sliding on ${s.slope_deg.toFixed(0)}° with brakes on: gravity beats grip (limit ~${(Math.atan(prof.traction_mu) / DEG).toFixed(0)}°)`
        : `rolling back on ${s.slope_deg.toFixed(0)}°: not enough grip or power to climb`;
    } else if (cornerLimited) {
      s.status = 'slipping';
      s.stop_reason = `tyres at the grip limit in the turn: at ${vR.toFixed(0)} m/s the tightest turn is ~${((vR * vR) / (prof.traction_mu * MARS_G)).toFixed(0)} m (v²/μg)`;
    } else if (throttle === 0 && !braked && moving) {
      s.status = 'coasting';
      s.stop_reason = s.speed_mps < 0 ? `rolling backwards downhill (hold Space to brake)` : null;
    } else if (throttle !== 0 && !braked && Math.abs(s.speed_mps) < 0.1 * cap) {
      s.status = 'slipping';
      s.stop_reason = gripLimited
        ? `wheels slipping on ${s.slope_deg.toFixed(0)}°: grip limit reached`
        : `stalled: cannot climb onto the ${Math.max(s.slope_deg, s.ahead_slope_deg).toFixed(0)}° slope ahead`;
    } else if (s.slope_deg > prof.nogo_slope_deg) {
      s.status = 'steep';
      s.stop_reason = `${s.slope_deg.toFixed(0)}° is past the ${prof.nogo_slope_deg}° safety limit (advisory)`;
    } else if (s.slope_deg > prof.caution_slope_deg) {
      s.status = 'caution';
      s.stop_reason = null;
    } else {
      s.status = moving ? 'driving' : 'idle';
      s.stop_reason = null;
    }
    if (n === p.max_substeps && tMission / n > hNom * 4 && !s.stop_reason) {
      s.stop_reason = `time-lapse x${(rate).toFixed(0)}: physics runs in ${(tMission / n).toFixed(2)} s steps`;
    }
    return s;
  }
}
