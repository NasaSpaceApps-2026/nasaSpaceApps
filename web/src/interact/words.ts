// Plain-words rendering of query-engine results (devapi POST /describe, GET /pois). Pure: no DOM, no Cesium, unit-tested
// from panels/chat/interact.test.ts. Every sentence is built only from fields the API returned.

export interface Evidence {
  layer?: string
  best_tier?: number
  best_tier_name?: string
  orbital_source?: string
  in_situ?: { samples?: { poi?: string; name?: string; citation?: string }[] }
}

export interface DescribeLoc {
  x: number
  y: number
  lat: number
  lon: number
  quality?: string
  values?: Record<string, { value?: number; units?: string; class?: string; tier?: number; tier_name?: string; unknown?: boolean }>
  evidence?: Record<string, Evidence>
  nearest_ground_truth?: { distance_m?: number; layer?: string; sample?: { name?: string; region?: string; in_situ_minerals?: string[] }; citation?: string }
}

export const MINERALS: [string, string][] = [
  ['carbonate', 'Carbonate'],
  ['olivine', 'Olivine'],
  ['fe_mg_smectite', 'Fe/Mg smectite clay'],
  ['mg_smectite_2', 'Mg smectite clay'],
  ['kaolinite', 'Kaolinite clay'],
  ['high_ca_pyroxene', 'High-Ca pyroxene'],
  ['low_ca_pyroxene', 'Low-Ca pyroxene'],
]

export type TierClass = 'confirmed' | 'strong' | 'weak' | 'inferred' | 'none' | 'nodata'

export interface MineralLine { layer: string; name: string; tier: TierClass; text: string }

/** "Farley et al. 2022, Science 377, doi:..." -> "Farley et al. 2022". */
export function shortCitation(c?: string): string {
  if (!c) return ''
  const m = /^(.*?\b(?:19|20)\d{2})/.exec(c)
  return (m ? m[1] : c.split(/[,;(]/)[0]).trim()
}

/** "Sample 5: Coulettes" -> "Sample 5". */
export function sampleLabel(name: string): string {
  return /^(Sample \d+)/.exec(name)?.[1] ?? name
}

export function mineralLines(loc: DescribeLoc): MineralLine[] {
  const out: MineralLine[] = []
  for (const [layer, name] of MINERALS) {
    const v = loc.values?.[layer]
    if (!v || v.unknown || typeof v.tier !== 'number') { out.push({ layer, name, tier: 'nodata', text: 'no orbital data here' }); continue }
    const tn = v.tier_name ?? ''
    if (tn === 'confirmed_in_situ') {
      const ev = loc.evidence?.[layer]?.in_situ?.samples ?? []
      const names = [...new Set(ev.map((s) => (s.name ? sampleLabel(s.name) : '')).filter(Boolean))]
      const cites = [...new Set(ev.map((s) => shortCitation(s.citation)).filter(Boolean))]
      const where = names.length ? ` at ${names.length > 2 ? names.slice(0, 2).join(', ') + ' and more' : names.join(' and ')}` : ''
      out.push({ layer, name, tier: 'confirmed', text: `confirmed by the rover${where}${cites.length ? ` (${cites.join('; ')})` : ''}` })
    } else if (tn === 'strong_orbital') out.push({ layer, name, tier: 'strong', text: 'seen from orbit (strong)' })
    else if (tn === 'weak_orbital') out.push({ layer, name, tier: 'weak', text: 'seen from orbit (weak)' })
    else if (tn === 'inferred_from_unit') out.push({ layer, name, tier: 'inferred', text: 'inferred from the geologic unit, not measured here' })
    else out.push({ layer, name, tier: 'none', text: 'not detected from orbit' })
  }
  const rank: Record<TierClass, number> = { confirmed: 0, strong: 1, weak: 2, inferred: 3, none: 4, nodata: 5 }
  return out.sort((a, b) => rank[a.tier] - rank[b.tier])
}

/** True when the whole mineral block has no data (the point is outside the evidence layers). */
export function noMineralData(lines: MineralLine[]): boolean {
  return lines.length > 0 && lines.every((l) => l.tier === 'nodata')
}

export function qualityWords(loc: DescribeLoc): string {
  const q = loc.values?.quality_flag?.class ?? loc.quality
  if (!q) return 'unknown'
  if (q === 'hirise_dtm') return 'good: measured HiRISE 2 m terrain model'
  if (/fill|interp/i.test(q)) return 'lower: elevation here is interpolated'
  return q.replace(/_/g, ' ')
}

export function fmtLatLon(lat: number, lon: number): string {
  const l = ((lon % 360) + 360) % 360 // Mars longitude, east positive, 0 to 360
  return `${Math.abs(lat).toFixed(4)}°${lat >= 0 ? 'N' : 'S'}  ${l.toFixed(4)}°E`
}

export function fmtMinutes(min: number): string {
  if (!Number.isFinite(min)) return 'n/a'
  if (min < 1) return '<1 min'
  if (min < 90) return `${Math.round(min)} min`
  return `${Math.floor(min / 60)} h ${Math.round(min % 60)} min`
}

export function fmtDist(m: number): string {
  return m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`
}

/** Terrain steepness in words, using the walking profile's published limits when given (assumed values elsewhere). */
export function slopeWords(deg: number, caution = 15, nogo = 25): string {
  if (deg >= nogo) return 'too steep to walk'
  if (deg >= caution) return 'steep: use caution'
  if (deg >= 8) return 'moderate'
  return 'gentle'
}

// ---- places (GET /pois) ----------------------------------------------------------------------------------------

export interface Poi {
  id: string
  name: string
  type: string
  x: number
  y: number
  description?: string
  source_url?: string
  sol?: number
  region?: string
  rock_type?: string
  in_situ_minerals?: string[]
  position_uncertainty_m?: number
}

export type PlaceKind = 'base' | 'landing' | 'region' | 'sample' | 'iau'

export interface NamedPlace {
  id: string
  name: string
  kind: PlaceKind
  x: number
  y: number
  priority: number
  /** Other POIs at the same spot (samples taken at one location). */
  also: Poi[]
  poi?: Poi
  iauDetail?: string
}

export function cleanPoiName(n: string): string {
  return n.replace(/\s*\((sampling area|assumed)\)\s*/i, '').replace(/^Mission base$/i, 'Mission base').trim()
}

/** Meaningful labels from the POI list: landing site (merged with the base), named regions, samples (grouped by spot). */
export function placesFromPois(pois: Poi[]): NamedPlace[] {
  const out: NamedPlace[] = []
  const landing = pois.find((p) => p.type === 'landmark' && /landing/i.test(p.name))
  const base = pois.find((p) => p.type === 'base')
  if (landing) out.push({ id: landing.id, name: landing.name, kind: 'landing', x: landing.x, y: landing.y, priority: 0, also: base ? [base] : [], poi: landing })
  // The assumed base gets its own, always-visible marker (kind 'base'); the landing label stays at the same spot.
  if (base) out.unshift({ id: base.id, name: base.name || 'Mission base (assumed)', kind: 'base', x: base.x, y: base.y, priority: -1, also: [], poi: base })
  for (const p of pois.filter((q) => q.type === 'named_region')) {
    out.push({ id: p.id, name: cleanPoiName(p.name), kind: 'region', x: p.x, y: p.y, priority: 1, also: [], poi: p })
  }
  const groups = new Map<string, Poi[]>()
  for (const p of pois.filter((q) => q.type === 'sample_site')) {
    const k = `${Math.round(p.x / 40)}:${Math.round(p.y / 40)}`
    groups.set(k, [...(groups.get(k) ?? []), p])
  }
  for (const g of groups.values()) {
    g.sort((a, b) => (a.sol ?? 0) - (b.sol ?? 0))
    const first = g[0]
    const nm = first.name.replace(/\s*\(aka[^)]*\)/i, '')
    const short = /^Sample (\d+):\s*(.+)$/.exec(nm)
    const name = short ? `${short[2]} · sample ${short[1]}${g.length > 1 ? ` +${g.length - 1}` : ''}` : nm
    out.push({ id: first.id, name, kind: 'sample', x: first.x, y: first.y, priority: 3, also: g.slice(1), poi: first })
  }
  return out
}

/** One or two sentences about a place, from its POI record and the samples taken in it. */
export function placeSummary(pl: NamedPlace, all: Poi[]): string[] {
  const lines: string[] = []
  const minerals = (ps: Poi[]) => [...new Set(ps.flatMap((p) => p.in_situ_minerals ?? []))]
  if (pl.kind === 'base') {
    lines.push('Assumed mission base for planning: there is no real base on Mars. Walk and drive limits are measured from here. It is placed at the Perseverance landing coordinates.')
  } else if (pl.kind === 'landing') {
    lines.push('Where Perseverance touched down (published coordinates). The planning base for walks and drives is assumed to be here.')
  } else if (pl.kind === 'region') {
    const samples = all.filter((p) => p.type === 'sample_site' && cleanPoiName(pl.name).toLowerCase() === (p.region ?? '').toLowerCase())
    if (samples.length) {
      const ids = samples.map((s) => /^Sample (\d+)/.exec(s.name)?.[1]).filter(Boolean)
      lines.push(`Perseverance took ${samples.length} sample${samples.length > 1 ? 's' : ''} here (${ids.join(', ')}).`)
      const m = minerals(samples)
      if (m.length) lines.push(`Confirmed by the rover in these samples: ${m.join(', ')}.`)
    }
    lines.push('The marker is the centre of the sample positions, not the region boundary.')
  } else if (pl.kind === 'sample') {
    for (const p of [pl.poi, ...pl.also].filter((q): q is Poi => !!q)) {
      const bits = [sampleLabel(p.name) + (p.name.includes(':') ? ` (${p.name.split(':')[1].trim().replace(/\s*\(aka[^)]*\)/i, '')})` : ''),
        p.sol != null ? `sol ${p.sol}` : '', p.rock_type && p.rock_type !== 'n/a' ? p.rock_type.toLowerCase() : '', p.region ? `in ${p.region}` : '']
      let s = bits.filter(Boolean).join(', ') + '.'
      if (p.in_situ_minerals?.length) s += ` Rover-confirmed: ${p.in_situ_minerals.join(', ')}.`
      lines.push(s)
    }
    const u = pl.poi?.position_uncertainty_m
    if (typeof u === 'number') lines.push(`Position is derived from the rover waypoint, about ±${Math.round(u)} m.`)
  } else if (pl.iauDetail) lines.push(pl.iauDetail + '.')
  return lines
}

export function placeQuestions(pl: NamedPlace): string[] {
  const n = pl.kind === 'sample' ? (pl.poi?.name.split(':')[1] ?? pl.name).replace(/\s*\(aka[^)]*\)/i, '').trim() : pl.name
  if (pl.kind === 'base') return ['Drive the buggy to the base', 'Find 3 flat landing pads within a 30 minute walk of base']
  if (pl.kind === 'landing') return ['Drive the buggy to the landing site', 'Find 3 flat landing pads within a 30 minute walk of base']
  if (pl.kind === 'iau') return [`What is ${n}?`, `Is ${n} a good place to land?`]
  return [`What did the rover find at ${n}?`, `Drive the buggy to ${n}`]
}
