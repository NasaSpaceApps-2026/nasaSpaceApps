// Rover history: pure data helpers (no Cesium), shared by the Explore panel and the unit tests.

export interface TravSeg { sol: number; length_m: number; xy: [number, number][] }
export interface SampleSite { id: string; name: string; sol: number; x: number; y: number; rock?: string; region?: string }

export interface TraverseStats { minSol: number; maxSol: number; totalM: number }

/** GET /traverse.geojson: one LineString (properties.x / y per vertex, site metres) plus Point waypoints with `sol` and
 * `vertex_index`. Waypoint i owns the vertices (previous vertex_index .. its vertex_index]. Runs are split where the line
 * jumps (the backend clips it to the site, so consecutive vertices can be far apart). */
export function parseTraverse(json: unknown, maxGapM = 120): TravSeg[] {
  const feats = (json as { features?: { geometry?: { type?: string }; properties?: Record<string, unknown> }[] } | null)?.features ?? []
  const line = feats.find((f) => f.geometry?.type === 'LineString')?.properties
  const xs = line?.x as number[] | undefined, ys = line?.y as number[] | undefined
  if (!xs || !ys || xs.length < 2) return []
  const wps = feats.filter((f) => f.geometry?.type === 'Point' && typeof f.properties?.sol === 'number' && typeof f.properties?.vertex_index === 'number')
    .map((f) => ({ sol: f.properties!.sol as number, vi: Math.min(xs.length - 1, f.properties!.vertex_index as number) })).sort((a, b) => a.sol - b.sol || a.vi - b.vi)
  const out: TravSeg[] = []
  let from = 0
  for (const w of wps) {
    if (w.vi <= from) continue
    let run: [number, number][] = [[xs[from], ys[from]]]
    const flush = () => {
      if (run.length >= 2) { let len = 0; for (let i = 1; i < run.length; i++) len += Math.hypot(run[i][0] - run[i - 1][0], run[i][1] - run[i - 1][1]); out.push({ sol: w.sol, length_m: len, xy: run }) }
    }
    for (let i = from + 1; i <= w.vi; i++) {
      if (Math.hypot(xs[i] - xs[i - 1], ys[i] - ys[i - 1]) > maxGapM) { flush(); run = [] }
      run.push([xs[i], ys[i]])
    }
    flush()
    from = w.vi
  }
  return out
}

export function traverseStats(segs: TravSeg[]): TraverseStats {
  return { minSol: segs.length ? segs[0].sol : 0, maxSol: segs.length ? segs[segs.length - 1].sol : 0, totalM: segs.reduce((s, g) => s + g.length_m, 0) }
}

/** Metres driven up to and including `sol`, and how many segments are already on the map. */
export function progressAt(segs: TravSeg[], sol: number): { driven_m: number; count: number } {
  let driven = 0, count = 0
  for (const g of segs) { if (g.sol > sol) break; driven += g.length_m; count++ }
  return { driven_m: driven, count }
}

/** Samples sealed by `sol`, in order, plus the newest one. */
export function samplesAt(samples: SampleSite[], sol: number): { shown: SampleSite[]; latest: SampleSite | null } {
  const shown = samples.filter((s) => s.sol <= sol).sort((a, b) => a.sol - b.sol)
  return { shown, latest: shown.length ? shown[shown.length - 1] : null }
}

interface PoiLike { id: string; name: string; type: string; sol?: number; x: number; y: number; rock_type?: string; region?: string }
export function samplesFromPois(pois: PoiLike[]): SampleSite[] {
  return pois.filter((p) => p.type === 'sample_site' && typeof p.sol === 'number')
    .map((p) => ({ id: p.id, name: p.name.replace(/\s*\(aka[^)]*\)/i, ''), sol: p.sol as number, x: p.x, y: p.y, rock: p.rock_type, region: p.region }))
    .sort((a, b) => a.sol - b.sol)
}
