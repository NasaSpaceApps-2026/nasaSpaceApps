/**
 * WebSocket link to the Ares API (`WS /ws`, PRD Contract 4).
 *
 * - Sends `rover.pose` (viewer -> server) while the viewer owns the pose.
 * - Receives `rover.state` (server -> viewer); in auto/fsd mode the viewer only
 *   renders those poses, interpolated by RemotePoseInterpolator.
 * - Reconnects with exponential backoff; everything keeps working offline.
 */

import { headingDelta, normalizeHeading, type SitePose } from './coords';

export const DEFAULT_WS_URL = 'ws://localhost:8000/ws';

/** Server rover state (Contract 4). Unknown extra fields are kept. */
export interface RemoteRoverState extends SitePose {
  mode: 'idle' | 'manual' | 'auto' | 'fsd' | string;
  speed_mps?: number;
  odometer_m?: number;
  route_id?: string | null;
  status?: string;
  last_event?: string | null;
  fsd_armed?: boolean;
  [key: string]: unknown;
}

export interface PoseMessage {
  type: 'rover.pose';
  x: number;
  y: number;
  z: number;
  heading_deg: number;
  pitch_deg: number;
  roll_deg: number;
  speed_mps: number;
}

export type LinkStatus = 'connecting' | 'open' | 'closed';

export interface RoverLinkOptions {
  url?: string;
  minRetryMs?: number;
  maxRetryMs?: number;
  /** Connect immediately (default true). */
  autoConnect?: boolean;
}

type Fn<T> = (v: T) => void;

const r3 = (v: number) => Math.round(v * 1000) / 1000;
const r2 = (v: number) => Math.round(v * 100) / 100;

export function makePoseMessage(p: SitePose & { speed_mps: number }): PoseMessage {
  return {
    type: 'rover.pose',
    x: r3(p.x),
    y: r3(p.y),
    z: r3(p.z),
    heading_deg: r2(p.heading_deg),
    pitch_deg: r2(p.pitch_deg),
    roll_deg: r2(p.roll_deg),
    speed_mps: Math.round(p.speed_mps * 1e5) / 1e5,
  };
}

/** Extract a rover state from a `rover.state` message (flat or wrapped payload). */
export function parseRoverState(msg: any): RemoteRoverState | null {
  if (!msg || msg.type !== 'rover.state') return null;
  const s = msg.state ?? msg.data ?? msg.payload ?? msg;
  if (typeof s.x !== 'number' || typeof s.y !== 'number') return null;
  return {
    ...s,
    x: s.x,
    y: s.y,
    z: typeof s.z === 'number' ? s.z : NaN,
    heading_deg: s.heading_deg ?? 0,
    pitch_deg: s.pitch_deg ?? 0,
    roll_deg: s.roll_deg ?? 0,
    mode: s.mode ?? 'idle',
  };
}

export class RoverLink {
  readonly url: string;
  status: LinkStatus = 'closed';
  private ws: WebSocket | null = null;
  private retryMs: number;
  private minRetry: number;
  private maxRetry: number;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  private stateFns = new Set<Fn<RemoteRoverState>>();
  private msgFns = new Set<Fn<any>>();
  private statusFns = new Set<Fn<LinkStatus>>();

  constructor(opts: RoverLinkOptions = {}) {
    this.url = opts.url ?? DEFAULT_WS_URL;
    this.minRetry = opts.minRetryMs ?? 1000;
    this.maxRetry = opts.maxRetryMs ?? 15000;
    this.retryMs = this.minRetry;
    if (opts.autoConnect ?? true) this.connect();
  }

  get isOpen(): boolean {
    return this.status === 'open';
  }

  onState(fn: Fn<RemoteRoverState>): () => void {
    this.stateFns.add(fn);
    return () => this.stateFns.delete(fn);
  }

  /** Every parsed JSON message (any type), for callers that want more events. */
  onMessage(fn: Fn<any>): () => void {
    this.msgFns.add(fn);
    return () => this.msgFns.delete(fn);
  }

  onStatus(fn: Fn<LinkStatus>): () => void {
    this.statusFns.add(fn);
    return () => this.statusFns.delete(fn);
  }

  connect(): void {
    this.stopped = false;
    if (this.ws || typeof WebSocket === 'undefined') return;
    this.setStatus('connecting');
    let ws: WebSocket;
    try {
      ws = new WebSocket(this.url);
    } catch {
      this.scheduleRetry();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.retryMs = this.minRetry;
      this.setStatus('open');
    };
    ws.onmessage = (ev) => {
      let msg: any;
      try {
        msg = JSON.parse(typeof ev.data === 'string' ? ev.data : '');
      } catch {
        return;
      }
      for (const fn of this.msgFns) fn(msg);
      const st = parseRoverState(msg);
      if (st) for (const fn of this.stateFns) fn(st);
    };
    ws.onerror = () => {
      /* onclose follows; nothing to do */
    };
    ws.onclose = () => {
      this.ws = null;
      this.setStatus('closed');
      if (!this.stopped) this.scheduleRetry();
    };
  }

  close(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.ws?.close();
    this.ws = null;
    this.setStatus('closed');
  }

  send(obj: unknown): boolean {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify(obj));
    return true;
  }

  sendPose(p: SitePose & { speed_mps: number }): boolean {
    return this.send(makePoseMessage(p));
  }

  private scheduleRetry(): void {
    if (this.timer || this.stopped) return;
    const wait = this.retryMs * (0.8 + 0.4 * Math.random());
    this.retryMs = Math.min(this.maxRetry, this.retryMs * 2);
    this.timer = setTimeout(() => {
      this.timer = null;
      this.connect();
    }, wait);
  }

  private setStatus(s: LinkStatus): void {
    if (s === this.status) return;
    this.status = s;
    for (const fn of this.statusFns) fn(s);
  }
}

/**
 * Smooths 10 Hz server poses: renders `delayMs` in the past and interpolates
 * between the two bracketing samples (heading along the shortest arc).
 */
export class RemotePoseInterpolator {
  private buf: Array<{ t: number; s: RemoteRoverState }> = [];
  constructor(public delayMs = 150) {}

  push(s: RemoteRoverState, tMs: number): void {
    this.buf.push({ t: tMs, s });
    if (this.buf.length > 32) this.buf.splice(0, this.buf.length - 32);
  }

  clear(): void {
    this.buf = [];
  }

  get latest(): RemoteRoverState | null {
    return this.buf.length ? this.buf[this.buf.length - 1].s : null;
  }

  sample(nowMs: number): RemoteRoverState | null {
    const b = this.buf;
    if (!b.length) return null;
    const t = nowMs - this.delayMs;
    if (t <= b[0].t) return b[0].s;
    for (let i = b.length - 1; i > 0; i--) {
      if (b[i - 1].t <= t) {
        const a = b[i - 1];
        const c = b[i];
        if (t >= c.t) return c.s;
        const u = (t - a.t) / (c.t - a.t);
        const lerp = (p: number, q: number) => p + (q - p) * u;
        return {
          ...c.s,
          x: lerp(a.s.x, c.s.x),
          y: lerp(a.s.y, c.s.y),
          z: lerp(a.s.z, c.s.z),
          heading_deg: normalizeHeading(a.s.heading_deg + headingDelta(a.s.heading_deg, c.s.heading_deg) * u),
          pitch_deg: lerp(a.s.pitch_deg, c.s.pitch_deg),
          roll_deg: lerp(a.s.roll_deg, c.s.roll_deg),
        };
      }
    }
    return b[b.length - 1].s;
  }
}
