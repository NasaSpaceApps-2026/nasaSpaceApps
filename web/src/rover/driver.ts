/**
 * RoverDriver ties physics, keyboard and the API link together and applies the
 * Contract 4 control rule:
 *   - manual: local physics owns the pose; it is sent as `rover.pose` at 5 Hz.
 *   - auto / fsd (from server `rover.state`): the viewer renders the server
 *     pose, interpolated; local physics is idle.
 *   - any drive key press while auto/fsd switches to manual at once, continues
 *     from the rendered pose and sends a pose immediately (the server treats a
 *     viewer pose as takeover and cancels its command).
 * Call `step(dt)` once per frame and render `driver.pose`.
 */

import type { SitePose } from './coords';
import type { KeyboardControls } from './controls';
import type { HeightSampler } from './heightTiles';
import { RemotePoseInterpolator, type LinkStatus, type RemoteRoverState, type RoverLink } from './link';
import { DEFAULT_ROVER_PROFILE, RoverPhysics, footprint, type InitialPose, type RoverParams, type RoverProfile, type RoverStatus } from './physics';

export type DriveMode = 'manual' | 'auto' | 'fsd';

export interface DriverPose extends SitePose {
  speed_mps: number;
  display_speed_mps: number;
  slope_deg: number;
  ahead_slope_deg: number;
  mode: DriveMode;
  status: RoverStatus | string;
  stop_reason: string | null;
}

export interface RoverDriverOptions {
  sampler: HeightSampler;
  profile?: RoverProfile;
  params?: Partial<RoverParams>;
  start?: InitialPose;
  controls?: KeyboardControls | null;
  link?: RoverLink | null;
  /** Pose send rate in manual mode (Contract 4: 5 Hz). */
  poseHz?: number;
  /** A non-auto server state further than this from the local pose is treated as a teleport. */
  teleportThresholdM?: number;
  /** After a takeover, ignore in-flight auto states for this long. */
  takeoverGraceMs?: number;
  /**
   * After the socket opens, wait this long before sending poses, so a server
   * already driving in auto/fsd can announce it first (a pose from the viewer
   * would otherwise count as a takeover).
   */
  connectHoldMs?: number;
}

const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());

export class RoverDriver {
  readonly physics: RoverPhysics;
  readonly interp = new RemotePoseInterpolator(150);
  mode: DriveMode = 'manual';
  pose: DriverPose;
  link: RoverLink | null;
  controls: KeyboardControls | null;

  private poseInterval: number;
  private lastSent = -Infinity;
  private teleportM: number;
  private graceMs: number;
  private takeoverAt = -Infinity;
  private holdUntil = -Infinity;
  private connectHoldMs: number;
  private unsub: Array<() => void> = [];
  private takeoverFns = new Set<() => void>();

  constructor(o: RoverDriverOptions) {
    this.physics = new RoverPhysics(o.sampler, o.profile ?? DEFAULT_ROVER_PROFILE, o.params, o.start);
    this.link = o.link ?? null;
    this.controls = o.controls ?? null;
    this.poseInterval = 1000 / (o.poseHz ?? 5);
    this.teleportM = o.teleportThresholdM ?? 25;
    this.graceMs = o.takeoverGraceMs ?? 1000;
    this.connectHoldMs = o.connectHoldMs ?? 1000;
    this.pose = this.fromPhysics();
    if (this.controls) this.unsub.push(this.controls.onTakeover(() => this.takeover()));
    if (this.link) {
      this.unsub.push(this.link.onState((s) => this.onRemoteState(s)));
      this.unsub.push(this.link.onStatus((s: LinkStatus) => {
        if (s === 'open') this.holdUntil = now() + this.connectHoldMs;
      }));
    }
  }

  get linkStatus(): LinkStatus | 'none' {
    return this.link ? this.link.status : 'none';
  }

  /** Fires when local input takes control back from auto/fsd. */
  onTakeover(fn: () => void): () => void {
    this.takeoverFns.add(fn);
    return () => this.takeoverFns.delete(fn);
  }

  setProfile(p: RoverProfile): void {
    this.physics.profile = p;
  }

  /** Teleport locally (manual mode) and report it. */
  teleport(x: number, y: number, heading_deg = this.pose.heading_deg): void {
    this.physics.setPose(x, y, heading_deg);
    this.pose = this.fromPhysics();
    this.sendPose(true);
  }

  /** Switch to manual now (key press, or call it from UI). */
  takeover(): void {
    if (this.mode === 'manual') return;
    const p = this.pose;
    this.mode = 'manual';
    this.takeoverAt = now();
    this.interp.clear();
    this.physics.setPose(p.x, p.y, p.heading_deg);
    this.pose = this.fromPhysics();
    this.sendPose(true);
    for (const fn of this.takeoverFns) fn();
  }

  step(dtWall: number, input = this.controls?.getInput() ?? { throttle: 0, steer: 0 }): DriverPose {
    const t = now();
    if (this.mode === 'manual') {
      this.physics.step(dtWall, input);
      this.pose = this.fromPhysics();
      this.sendPose(false, t);
    } else {
      const s = this.interp.sample(t);
      if (s) this.pose = this.fromRemote(s);
    }
    return this.pose;
  }

  dispose(): void {
    for (const u of this.unsub) u();
    this.unsub = [];
  }

  private sendPose(force: boolean, t = now()): void {
    if (!this.link || (!force && (t - this.lastSent < this.poseInterval || t < this.holdUntil))) return;
    if (this.link.sendPose(this.pose)) this.lastSent = t;
  }

  private onRemoteState(s: RemoteRoverState): void {
    const t = now();
    const auto = s.mode === 'auto' || s.mode === 'fsd';
    if (auto) {
      if (t - this.takeoverAt < this.graceMs) return;
      if (!Number.isFinite(s.z)) s = { ...s, z: this.physics.sampler.sampleHeight(s.x, s.y) };
      this.mode = s.mode as DriveMode;
      this.interp.push(s, t);
      return;
    }
    if (this.mode !== 'manual') {
      // Auto drive ended on the server: continue locally from its final pose.
      this.mode = 'manual';
      this.interp.clear();
      this.physics.setPose(s.x, s.y, s.heading_deg);
      this.pose = { ...this.fromPhysics(), stop_reason: (s.last_event as string) ?? null };
      return;
    }
    // Manual/idle: adopt the server pose only on a jump (e.g. agent teleport).
    if (Math.hypot(s.x - this.pose.x, s.y - this.pose.y) > this.teleportM) {
      this.physics.setPose(s.x, s.y, s.heading_deg);
      this.pose = this.fromPhysics();
    }
  }

  private fromPhysics(): DriverPose {
    const s = this.physics.state;
    return {
      x: s.x, y: s.y, z: s.z, heading_deg: s.heading_deg, pitch_deg: s.pitch_deg, roll_deg: s.roll_deg,
      speed_mps: s.speed_mps, display_speed_mps: s.display_speed_mps, slope_deg: s.slope_deg,
      ahead_slope_deg: s.ahead_slope_deg, mode: 'manual', status: s.status, stop_reason: s.stop_reason,
    };
  }

  private fromRemote(s: RemoteRoverState): DriverPose {
    const speed = typeof s.speed_mps === 'number' ? s.speed_mps : 0;
    const f = footprint(this.physics.sampler, s.x, s.y, s.heading_deg, this.physics.params);
    return {
      x: s.x, y: s.y, z: s.z, heading_deg: s.heading_deg, pitch_deg: s.pitch_deg, roll_deg: s.roll_deg,
      speed_mps: speed, display_speed_mps: speed * this.physics.profile.display_speed_factor,
      slope_deg: f.slope_deg, ahead_slope_deg: NaN, mode: this.mode, status: s.status ?? s.mode,
      stop_reason: (s.last_event as string | null | undefined) ?? null,
    };
  }
}
