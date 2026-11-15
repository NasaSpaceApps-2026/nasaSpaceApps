// Pure helpers for the Explore tools: overlay URL building, site rectangle math, debounce, sol/LTST formatting, stats lines.
// No Cesium or DOM imports here, so the vitest suite (chat-dev) can run them in node.

export type OverlayKind = 'evidence' | 'geology' | 'slope' | 'lake' | 'sun' | 'viewshed' | 'envelope'
export type OverlayParams = Record<string, string | number | boolean | null | undefined>
export type Slot = 'main' | 'science'

export const OVERLAY_KINDS: OverlayKind[] = ['evidence', 'geology', 'slope', 'lake', 'sun', 'viewshed', 'envelope']
/** Science layers sit in their own slot so they can run under a tool overlay (lake, sun, reach, viewshed). */
export const SCIENCE_KINDS: OverlayKind[] = ['evidence', 'geology', 'slope']

export const slotOf = (k: OverlayKind): Slot => (SCIENCE_KINDS.includes(k) ? 'science' : 'main')
export const isKind = (k: unknown): k is OverlayKind => typeof k === 'string' && (OVERLAY_KINDS as string[]).includes(k)

export interface LegendItem { color: string; label: string }
export interface OverlayMeta {
  title: string
  legend: LegendItem[]
  source?: string
  caveat?: string
  stats?: Record<string, unknown>
  bounds?: { half_m?: number; lat0?: number; lon0?: number }
}

/** Base URL of the overlay service: ?overlay=... (or VITE_OVERLAY_URL) wins, else the query engine (devapi). */
export function overlayBase(search: string, envUrl: string | undefined, engine: string): string {
  let fromQuery: string | null = null
  try { fromQuery = new URLSearchParams(search).get('overlay') } catch { /* no query */ }
  return (fromQuery || envUrl || engine).replace(/\/+$/, '')
}

/** `<base>/overlay/<kind>.<png|json>?<sorted params>`. Empty / null / undefined params are dropped. */
export function overlayUrl(base: string, kind: OverlayKind, params: OverlayParams, ext: 'png' | 'json'): string {
  const q = Object.keys(params)
    .filter((k) => params[k] !== undefined && params[k] !== null && params[k] !== '')
    .sort()
    .map((k) => `${encodeURIComponent(k)}=${encodeURIComponent(String(params[k]))}`)
    .join('&')
  return `${base.replace(/\/+$/, '')}/overlay/${kind}.${ext}${q ? `?${q}` : ''}`
}

const MARS_R = 3396190

/** Lon/lat rectangle (radians) of the square site of half-width `halfM` metres centred on lat0/lon0 (degrees).
 * Same equirectangular frame as Site (contract 1): x east = R cos(lat0) dlon, y north = R dlat. */
export function siteRect(lat0Deg: number, lon0Deg: number, halfM: number, radius = MARS_R) {
  const lat0 = (lat0Deg * Math.PI) / 180, lon0 = (lon0Deg * Math.PI) / 180
  const dLon = halfM / (radius * Math.cos(lat0)), dLat = halfM / radius
  return { west: lon0 - dLon, east: lon0 + dLon, south: lat0 - dLat, north: lat0 + dLat }
}

export interface Debounced<A extends unknown[]> { (...a: A): void; flush: () => void; cancel: () => void }

/** Trailing debounce. `flush()` runs a pending call now (slider released); `cancel()` drops it. */
export function debounce<A extends unknown[]>(fn: (...a: A) => void, ms: number): Debounced<A> {
  let t: ReturnType<typeof setTimeout> | null = null
  let last: A | null = null
  const run = () => { t = null; const a = last; last = null; if (a) fn(...a) }
  const d = ((...a: A) => { last = a; if (t) clearTimeout(t); t = setTimeout(run, ms) }) as Debounced<A>
  d.flush = () => { if (t) { clearTimeout(t); run() } }
  d.cancel = () => { if (t) clearTimeout(t); t = null; last = null }
  return d
}

const SOL_S = 88775.244
const SOL0_MS = Date.UTC(2021, 1, 18, 20, 55, 0) // Mars 2020 touchdown = sol 0 (atlas_core/sun.py MARS2020_LANDING_UTC)
/** Mission sol number of a date (sol 0 = touchdown). */
export const solAt = (d: Date): number => Math.floor((d.getTime() - SOL0_MS) / 1000 / SOL_S)

/** 12.5 -> "12:30". */
export function ltstLabel(h: number): string {
  const total = Math.round(h * 60)
  const hh = Math.floor(total / 60) % 24, mm = total % 60
  return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`
}

export const fmtMetres = (m: number) => `${m < 0 ? '−' : ''}${Math.abs(Math.round(m)).toLocaleString('en-US')} m`

const num = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) ? v : undefined)
const humanise = (k: string) => k.replace(/_/g, ' ').replace(/\bkm2\b/, 'km\u00b2')
const fmtNum = (n: number) => (Math.abs(n) >= 100 ? Math.round(n).toLocaleString('en-US') : String(Math.round(n * 10) / 10))
const pct = (f: number | undefined) => (f === undefined ? undefined : Math.round(f * 100))

/** One readable line from the overlay's `stats` (key names as documented in devapi/OVERLAYS.md). A missing field just drops
 * its clause; if nothing known is present the scalar stats are listed as "key value". */
export function statLine(kind: OverlayKind, stats: Record<string, unknown> | undefined, ctx: { level?: number; reliefMin?: number } = {}): string {
  const s = stats ?? {}
  const generic = () => Object.entries(s).filter(([, v]) => num(v) !== undefined || (typeof v === 'string' && v.length < 40)).slice(0, 4)
    .map(([k, v]) => `${humanise(k)} ${typeof v === 'number' ? fmtNum(v) : v}`).join(' \u00b7 ')
  if (kind === 'lake') {
    const area = num(s.area_km2)
    if (area === undefined) return generic()
    const level = num(s.level_m) ?? ctx.level
    const deep = num(s.max_depth_m) ?? (level !== undefined && ctx.reliefMin !== undefined ? Math.max(0, level - ctx.reliefMin) : undefined)
    return `${level !== undefined ? `Lake level ${fmtMetres(level)}: ` : ''}${fmtNum(area)} km\u00b2 underwater${deep !== undefined ? `, deepest ${fmtNum(deep)} m` : ''}`
  }
  if (kind === 'sun') {
    const el = num(s.sun_elevation_deg)
    const clock = (hours: unknown, text: unknown) => (typeof hours === 'number' ? ltstLabel(hours) : typeof text === 'string' ? text.slice(0, 5) : undefined)
    const rise = clock(s.sunrise_ltst_hours, s.sunrise_ltst), set = clock(s.sunset_ltst_hours, s.sunset_ltst)
    const bits: string[] = []
    if (el !== undefined) bits.push(el > 0 ? `Sun ${fmtNum(el)}\u00b0 above the horizon` : `Sun ${fmtNum(-el)}\u00b0 below the horizon`)
    if (rise) bits.push(`sunrise ${rise}`)
    if (set) bits.push(`sunset ${set}`)
    const cast = num(s.cast_shadow_fraction)
    if (cast !== undefined) bits.push(`${Math.round(cast * 1000) / 10}% in cast shadow`)
    return bits.length ? bits.join(' \u00b7 ') : generic()
  }
  if (kind === 'envelope') {
    const area = num(s.area_km2), r = num(s.max_radius_m), use = num(s.usable_min)
    const bits: string[] = []
    if (area !== undefined) bits.push(`${fmtNum(area)} km\u00b2 reachable`)
    if (r !== undefined) bits.push(`farthest point ${fmtNum(r)} m away`)
    if (use !== undefined) bits.push(`${fmtNum(use)} min usable after the reserve`)
    return bits.length ? bits.join(' \u00b7 ') : generic()
  }
  if (kind === 'viewshed') {
    const area = num(s.visible_km2), frac = pct(num(s.visible_fraction)), far = num(s.farthest_visible_m)
    const bits: string[] = []
    if (area !== undefined) bits.push(`${fmtNum(area)} km\u00b2 visible`)
    if (frac !== undefined) bits.push(`${frac}% of the area in range`)
    if (far !== undefined) bits.push(`farthest visible point ${fmtNum(far)} m`)
    return bits.length ? bits.join(' \u00b7 ') : generic()
  }
  if (kind === 'slope') {
    const safe = pct(num(s.safe_fraction)), caut = pct(num(s.caution_fraction)), nogo = pct(num(s.no_go_fraction))
    return safe !== undefined && caut !== undefined && nogo !== undefined ? `${safe}% walkable \u00b7 ${caut}% caution \u00b7 ${nogo}% no-go (assumed limits)` : generic()
  }
  return ''
}

export interface NamedLevel { id: string; level: number; label: string; citation: string; kind?: string }

/** `named_levels` of the lake entry in /overlay/kinds: [{id, level_m, label, kind, citation}], sorted high to low. */
export function parseNamedLevels(raw: unknown): NamedLevel[] {
  if (!Array.isArray(raw)) return []
  const out: NamedLevel[] = []
  for (const r of raw as Record<string, unknown>[]) {
    const level = num(r?.level_m)
    if (level === undefined) continue
    out.push({ id: String(r.id ?? level), level, label: String(r.label ?? r.id ?? ''), citation: String(r.citation ?? ''), kind: typeof r.kind === 'string' ? r.kind : undefined })
  }
  return out.sort((a, b) => b.level - a.level)
}

/** Parameter default from /overlay/kinds, e.g. kindDefault(json, 'sun', 'sol'). */
export function kindDefault(kindsJson: unknown, kind: string, param: string): number | undefined {
  const k = ((kindsJson as { kinds?: { kind: string; params?: { name: string; default?: unknown }[] }[] } | null)?.kinds ?? []).find((x) => x.kind === kind)
  return num(k?.params?.find((p) => p.name === param)?.default)
}
export function lakeLevelsOf(kindsJson: unknown): NamedLevel[] {
  const k = ((kindsJson as { kinds?: { kind: string; named_levels?: unknown }[] } | null)?.kinds ?? []).find((x) => x.kind === 'lake')
  return parseNamedLevels(k?.named_levels)
}

const M24 = "Mangold et al., LPSC 2024 abstract 'Past variations of water level of Jezero paleolake'"
const V25 = "Villette et al., MTT 2025 abstract 'Modeling the hydrological evolution of Jezero's crater lake and its deltaic deposit'"
/** Offline copy of docs/lake-levels.md, used only until /overlay/kinds answers. */
export const FALLBACK_LEVELS: NamedLevel[] = [
  { id: 'model_max_fill_2243', level: -2243, kind: 'modeled', label: 'Model scenario: initial infilling (not an observed shoreline)', citation: V25 },
  { id: 'delta_top_2410', level: -2410, kind: 'measured (minimum)', label: 'Uppermost delta foresets observed so far (north of Belva crater)', citation: M24 },
  { id: 'eastern_breach_ca_2400', level: -2400, kind: 'approximate', label: 'Eastern breach (outlet), ca. -2400 m', citation: M24 },
  { id: 'delta_drying_start_2390', level: -2390, kind: 'modeled', label: 'Upper end of the modeled delta lake-level range', citation: V25 },
  { id: 'carew_castle_2445', level: -2445, kind: 'measured', label: 'Topset/foreset transition at Carew Castle (fan top)', citation: M24 },
  { id: 'pinestand_2448', level: -2448, kind: 'measured (minimum)', label: 'Tops of the foresets at Pinestand Mountain', citation: M24 },
  { id: 'fan_front_2470', level: -2470, kind: 'measured', label: 'Upper end of the fan-front transitions', citation: M24 },
  { id: 'kodiak_2490', level: -2490, kind: 'measured', label: 'Kodiak butte', citation: M24 },
  { id: 'delta_low_2500', level: -2500, kind: 'measured', label: 'Lowest delta level (Eastern Cape and Kodiak)', citation: M24 },
].sort((a, b) => b.level - a.level)

export const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))
