// Direct REST calls from the browser. Query engine (devapi) on :8001, rover API on :8000. Both send CORS `*`.
import type { DescribeLoc, Poi } from './words'

const loc = typeof location !== 'undefined' ? location.hostname : 'localhost'
export const ENGINE = (import.meta.env.VITE_ENGINE_URL as string | undefined) ?? `http://${loc}:8001`
export const ROVER_API = (import.meta.env.VITE_API_URL as string | undefined) ?? `http://${loc}:8000`

export class ApiError extends Error {}

async function call<T>(url: string, init?: RequestInit, signal?: AbortSignal): Promise<T> {
  let r: Response
  try {
    r = await fetch(url, { ...init, signal, headers: init?.body ? { 'content-type': 'application/json' } : undefined })
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new ApiError(`Cannot reach ${new URL(url).origin}. Is the stack running?`)
  }
  const body = (await r.json().catch(() => ({}))) as Record<string, unknown>
  if (!r.ok) {
    const d = (body.detail ?? body) as Record<string, unknown> | string
    const msg = typeof d === 'string' ? d : String(d.error ?? d.message ?? `HTTP ${r.status}`)
    throw new ApiError(msg)
  }
  return body as T
}

const post = <T,>(url: string, body: unknown, signal?: AbortSignal) => call<T>(url, { method: 'POST', body: JSON.stringify(body) }, signal)

export async function describeAt(x: number, y: number, signal?: AbortSignal): Promise<DescribeLoc> {
  const r = await post<{ locations: DescribeLoc[] }>(`${ENGINE}/describe`, { at: { x, y } }, signal)
  if (!r.locations?.length) throw new ApiError('No data at this point.')
  return r.locations[0]
}

export async function fetchPois(): Promise<Poi[]> {
  for (const base of [ENGINE, ROVER_API]) {
    try {
      const r = await call<{ pois: Poi[] }>(`${base}/pois?limit=100`)
      if (r.pois?.length) return r.pois
    } catch { /* try the next */ }
  }
  return []
}

export interface ProfileSample { d: number; x: number; y: number; z: number; slope_deg: number }
export interface ProfileResult { distance_m: number; ascent_m: number; descent_m: number; max_slope_deg: number; min_z: number; max_z: number; samples: ProfileSample[]; unknown_samples: number }

export function terrainProfile(pts: [number, number][], signal?: AbortSignal): Promise<ProfileResult> {
  let len = 0
  for (let i = 1; i < pts.length; i++) len += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
  const step = Math.max(1, Math.ceil(pts.length / 30))
  const keep = pts.filter((_, i) => i % step === 0 || i === pts.length - 1)
  return post<ProfileResult>(`${ENGINE}/terrain/profile`, { points: keep.map(([x, y]) => ({ x, y })), spacing_m: Math.max(2, len / 80) }, signal)
}

export interface WalkRoute { feasible: boolean; reason?: string | null; distance_m: number; duration_min: number; ascent_m: number; max_slope_deg: number; points: { x: number; y: number; z: number }[]; warnings?: string[] }

/** Walking route for the measure card: devapi (it also stores the route, which only the engine's own viewers see). */
export function walkRoute(a: [number, number], b: [number, number], signal?: AbortSignal): Promise<WalkRoute> {
  return post<WalkRoute>(`${ENGINE}/route`, { start: { x: a[0], y: a[1] }, goal: { x: b[0], y: b[1] }, profile: 'eva_walkable' }, signal)
}

export interface DriveStart { ok?: boolean; stop_reason?: string; started?: boolean; message?: string; hint?: string }

/** Real-time drive: wait=false returns at once and the rover API streams rover.state at 10 Hz (api/ares_api/rover.py). */
export function driveTo(x: number, y: number): Promise<DriveStart> {
  return post<DriveStart>(`${ROVER_API}/rover/command`, { cmd: 'goto', target: { x, y }, wait: false })
}

export interface MarkerRec { id: string; x: number; y: number; label?: string }
export const addMarker = (x: number, y: number, label?: string) => post<MarkerRec>(`${ROVER_API}/markers`, { x, y, label, created_by: 'user' })
export const listMarkers = () => call<{ markers: MarkerRec[] }>(`${ROVER_API}/markers`).then((r) => r.markers ?? [])
export const removeMarker = (id: string) => call<{ removed: string }>(`${ROVER_API}/markers/${encodeURIComponent(id)}`, { method: 'DELETE' })
