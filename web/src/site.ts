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
}
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

const rad = Cesium.Math.toRadians
const deg = Cesium.Math.toDegrees

export class Site {
  readonly west: number; readonly east: number; readonly south: number; readonly north: number // radians
  readonly manifest: Manifest
  private readonly dem: Uint16Array
  private readonly meta: DemMeta
  private constructor(manifest: Manifest, dem: Uint16Array, meta: DemMeta) {
    this.manifest = manifest
    this.dem = dem
    this.meta = meta
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
    return new Site(manifest, new Uint16Array(buf), meta)
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

  /** Elevation in metres at lon/lat radians (bilinear on the 2 m grid); NaN outside the site. */
  elevation(lon: number, lat: number): number {
    const { width, height, res_m, base, step } = this.meta
    const [x, y] = this.lonLatToXY(lon, lat)
    const half = this.manifest.size_m / 2
    // pixel centres: col = (x + half)/res - 0.5, row = (half - y)/res - 0.5
    const cf = (x + half) / res_m - 0.5
    const rf = (half - y) / res_m - 0.5
    if (cf < 0 || rf < 0 || cf > width - 1 || rf > height - 1) return NaN
    const c0 = Math.floor(cf), r0 = Math.floor(rf)
    const c1 = Math.min(c0 + 1, width - 1), r1 = Math.min(r0 + 1, height - 1)
    const fc = cf - c0, fr = rf - r0
    const d = this.dem
    const v00 = d[r0 * width + c0], v01 = d[r0 * width + c1]
    const v10 = d[r1 * width + c0], v11 = d[r1 * width + c1]
    const v = (v00 * (1 - fc) + v01 * fc) * (1 - fr) + (v10 * (1 - fc) + v11 * fc) * fr
    return base + step * v
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

export { deg, rad }
