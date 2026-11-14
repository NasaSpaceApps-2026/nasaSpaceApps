// Site frame (contract 1): local metres, x east, y north, z elevation. The frame is
// equirectangular on a sphere, so lon/lat <-> x/y is an exact affine. All conversions live here.
import * as Cesium from 'cesium'

export const R = 3396190
export const SPHERE = new Cesium.Ellipsoid(R, R, R)

export interface LayerInfo {
  id: string
  title: string
  mission: string
  instrument: string
  kind: 'base' | 'overlay'
  max_level: number
  credit: string
  url: string
  legend?: [number | null, string][]
  core?: CoreInfo // full-resolution block inside the site (level-6 tiles)
}
export interface CoreInfo { level: number; tx0: number; ty0: number; n: number; x0: number; y_top: number; size_m: number; res_m: number }
export interface Manifest {
  site: string
  lat0: number
  lon0: number
  size_m: number
  height: { max_level: number; tile_px: number }
  layers: LayerInfo[]
  vectors: { pois: string; traverse: string }
}
interface DemMeta { width: number; height: number; res_m: number; base: number; step: number }
interface CoreMeta extends DemMeta { x0: number; y_top: number }

const rad = Cesium.Math.toRadians
const deg = Cesium.Math.toDegrees

export class Site {
  readonly west: number; readonly east: number; readonly south: number; readonly north: number // radians
  readonly manifest: Manifest
  private readonly dem: Uint16Array
  private readonly meta: DemMeta
  private readonly core?: { data: Uint16Array; meta: CoreMeta }
  private constructor(manifest: Manifest, dem: Uint16Array, meta: DemMeta, core?: { data: Uint16Array; meta: CoreMeta }) {
    this.manifest = manifest
    this.dem = dem
    this.meta = meta
    this.core = core
    const half = manifest.size_m / 2
    const dLon = half / (R * Math.cos(rad(manifest.lat0)))
    const dLat = half / R
    this.west = rad(manifest.lon0) - dLon
    this.east = rad(manifest.lon0) + dLon
    this.south = rad(manifest.lat0) - dLat
    this.north = rad(manifest.lat0) + dLat
  }

  static async load(): Promise<Site> {
    const manifest: Manifest = await (await fetch('/manifest.json')).json()
    const meta: DemMeta = await (await fetch('/web/dem2m.json')).json()
    const buf = await (await fetch('/web/dem2m.u16')).arrayBuffer()
    // Optional 1 m DEM for the full-resolution core block
    let core: { data: Uint16Array; meta: CoreMeta } | undefined
    try {
      const r = await fetch('/web/dem1m_core.json')
      if (r.ok) core = { meta: await r.json(), data: new Uint16Array(await (await fetch('/web/dem1m_core.u16')).arrayBuffer()) }
    } catch { /* core is optional */ }
    return new Site(manifest, new Uint16Array(buf), meta, core)
  }

  get rectangle(): Cesium.Rectangle {
    return new Cesium.Rectangle(this.west, this.south, this.east, this.north)
  }

  /** site metres -> lon/lat radians */
  xyToLonLat(x: number, y: number): [number, number] {
    const lat0 = rad(this.manifest.lat0)
    return [rad(this.manifest.lon0) + x / (R * Math.cos(lat0)), lat0 + y / R]
  }

  /** lon/lat radians -> site metres */
  lonLatToXY(lon: number, lat: number): [number, number] {
    const lat0 = rad(this.manifest.lat0)
    return [R * Math.cos(lat0) * (lon - rad(this.manifest.lon0)), R * (lat - lat0)]
  }

  /** Elevation in metres at lon/lat radians (bilinear; 1 m grid inside the core block, else 2 m); NaN outside. */
  elevation(lon: number, lat: number): number {
    const [x, y] = this.lonLatToXY(lon, lat)
    const c = this.core
    if (c) {
      const m = c.meta
      const cf = (x - m.x0) / m.res_m - 0.5, rf = (m.y_top - y) / m.res_m - 0.5
      if (cf >= 0 && rf >= 0 && cf <= m.width - 1 && rf <= m.height - 1) return bilinear(c.data, m, cf, rf)
    }
    const m = this.meta
    const half = this.manifest.size_m / 2
    const cf = (x + half) / m.res_m - 0.5, rf = (half - y) / m.res_m - 0.5
    if (cf < 0 || rf < 0 || cf > m.width - 1 || rf > m.height - 1) return NaN
    return bilinear(this.dem, m, cf, rf)
  }

  elevationAtXY(x: number, y: number): number {
    const [lon, lat] = this.xyToLonLat(x, y)
    return this.elevation(lon, lat)
  }

  /** ECEF position on the sphere for site metres plus height above terrain. */
  position(x: number, y: number, aboveGround = 0): Cesium.Cartesian3 {
    const [lon, lat] = this.xyToLonLat(x, y)
    const z = this.elevation(lon, lat)
    return Cesium.Cartesian3.fromRadians(lon, lat, (Number.isNaN(z) ? 0 : z) + aboveGround, SPHERE)
  }
}

function bilinear(d: Uint16Array, m: DemMeta, cf: number, rf: number): number {
  const w = m.width, c0 = Math.floor(cf), r0 = Math.floor(rf)
  const c1 = Math.min(c0 + 1, w - 1), r1 = Math.min(r0 + 1, m.height - 1)
  const fc = cf - c0, fr = rf - r0
  const v = (d[r0 * w + c0] * (1 - fc) + d[r0 * w + c1] * fc) * (1 - fr) + (d[r1 * w + c0] * (1 - fc) + d[r1 * w + c1] * fc) * fr
  return m.base + m.step * v
}

export { deg, rad }
