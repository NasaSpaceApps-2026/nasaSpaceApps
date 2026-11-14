// Real NASA/USGS vectors in the viewer: the Perseverance traverse and waypoints (Mars 2020, NASA/JPL) and
// official IAU place names (USGS Gazetteer). Built by pipeline/05_vectors.py and 05b_names.py.
import * as Cesium from 'cesium'
import { SPHERE, type Site } from './site'

export interface Place {
  id: string
  name: string
  kind: 'waypoint' | 'name'
  x: number
  y: number
  lat: number
  lon: number
  sol?: number
  heading?: number // degrees clockwise from north, toward the next waypoint
  diameterKm?: number
  source: string
  detail?: string
}

interface Feat { geometry: { coordinates: number[] }; properties: Record<string, unknown> }

export class PlacesLayer {
  places: Place[] = []
  private viewer: Cesium.Viewer
  private site: Site
  private lines: Cesium.Entity[] = []
  private points?: Cesium.PointPrimitiveCollection
  private wpLabels?: Cesium.LabelCollection
  private nameLabels?: Cesium.LabelCollection

  constructor(viewer: Cesium.Viewer, site: Site) {
    this.viewer = viewer
    this.site = site
  }

  async load() {
    const get = async (u: string) => {
      const r = await fetch(u)
      return r.ok ? ((await r.json()) as { features: Feat[] }) : { features: [] as Feat[] }
    }
    const [trav, pois, names] = await Promise.all([get('/data/traverse.geojson'), get('/data/pois.geojson'), get('/data/names.geojson')])
    const site = this.site, scene = this.viewer.scene

    // traverse polylines: densify to ~4 m and drape on our DEM (+0.6 m). Points with no terrain data
    // (beyond the site edge) are dropped and the line is split there, so nothing floats at height 0.
    const has = (x: number, y: number) => !Number.isNaN(site.elevationAtXY(x, y))
    const addRun = (run: Cesium.Cartesian3[]) => {
      if (run.length < 2) return
      this.lines.push(this.viewer.entities.add({ polyline: {
        positions: run, width: 3, arcType: Cesium.ArcType.NONE, material: Cesium.Color.fromCssColorString('#ffb020') } }))
    }
    for (const f of trav.features) {
      const xy = f.properties.xy as [number, number][] | undefined
      if (!xy || xy.length < 2) continue
      let run: Cesium.Cartesian3[] = []
      const push = (x: number, y: number) => {
        if (has(x, y)) run.push(site.position(x, y, 0.6))
        else { addRun(run); run = [] }
      }
      for (let i = 0; i < xy.length - 1; i++) {
        const [x0, y0] = xy[i], [x1, y1] = xy[i + 1]
        const n = Math.max(1, Math.ceil(Math.hypot(x1 - x0, y1 - y0) / 4))
        for (let k = 0; k < n; k++) push(x0 + ((x1 - x0) * k) / n, y0 + ((y1 - y0) * k) / n)
      }
      push(xy[xy.length - 1][0], xy[xy.length - 1][1])
      addRun(run)
    }

    // waypoints, ordered by sol, with a heading toward the next distinct position
    const wps = pois.features.map((f) => f.properties as Record<string, number & string>).sort((a, b) => a.sol - b.sol)
    this.points = scene.primitives.add(new Cesium.PointPrimitiveCollection())
    this.wpLabels = scene.primitives.add(new Cesium.LabelCollection())
    wps.forEach((p, i) => {
      let h = 0
      for (let j = i + 1; j < wps.length; j++) {
        const dx = wps[j].x - p.x, dy = wps[j].y - p.y
        if (Math.hypot(dx, dy) > 2) { h = (Math.atan2(dx, dy) * 180) / Math.PI; break }
      }
      const pl: Place = { id: p.id, name: p.name, kind: 'waypoint', x: p.x, y: p.y, lat: p.lat, lon: p.lon, sol: p.sol,
        heading: (h + 360) % 360, source: String(p.source_url), detail: String(p.description ?? '') }
      this.places.push(pl)
      if (!has(p.x, p.y)) return // waypoint just outside the terrain data: keep it searchable, do not draw it
      const at = site.position(p.x, p.y, 1.2)
      this.points!.add({ position: at, pixelSize: 6, color: Cesium.Color.fromCssColorString('#4de0ff'), outlineColor: Cesium.Color.BLACK, outlineWidth: 1 })
      this.wpLabels!.add({ position: site.position(p.x, p.y, 3), text: `sol ${p.sol}`, font: '12px sans-serif', fillColor: Cesium.Color.WHITE,
        outlineColor: Cesium.Color.BLACK, outlineWidth: 3, style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 700), disableDepthTestDistance: Number.POSITIVE_INFINITY })
    })

    // official place names; those inside the site sit on the terrain, others on the Mars globe
    this.nameLabels = scene.primitives.add(new Cesium.LabelCollection())
    for (const f of names.features) {
      const p = f.properties as Record<string, number & string>
      const inSite = !Number.isNaN(site.elevationAtXY(p.x, p.y))
      const pos = inSite ? site.position(p.x, p.y, 40) : Cesium.Cartesian3.fromDegrees(p.lon, p.lat, 0, SPHERE)
      this.places.push({ id: p.id, name: p.name, kind: 'name', x: p.x, y: p.y, lat: p.lat, lon: p.lon, diameterKm: p.diameter_km,
        source: String(p.source_url), detail: `${p.feature_type}, approved ${p.approved} by the IAU` })
      this.nameLabels.add({ position: pos, text: p.name, font: '600 15px sans-serif', fillColor: Cesium.Color.WHITE,
        outlineColor: Cesium.Color.BLACK, outlineWidth: 4, style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(3000, 8_000_000), disableDepthTestDistance: Number.POSITIVE_INFINITY })
    }
  }

  setTraverse(on: boolean) {
    this.lines.forEach((e) => (e.show = on))
    if (this.points) this.points.show = on
    if (this.wpLabels) this.wpLabels.show = on
  }

  setNames(on: boolean) {
    if (this.nameLabels) this.nameLabels.show = on
  }

  /** "sol 120", "120" -> nearest waypoint by sol; otherwise place-name substring match. */
  search(q: string): Place[] {
    const s = q.trim().toLowerCase()
    if (!s) return []
    const m = s.match(/^(?:sol\s*)?(\d+)$/)
    if (m) {
      const sol = Number(m[1])
      const wp = this.places.filter((p) => p.kind === 'waypoint')
      wp.sort((a, b) => Math.abs((a.sol ?? 0) - sol) - Math.abs((b.sol ?? 0) - sol))
      return wp.slice(0, 1)
    }
    return this.places.filter((p) => p.name.toLowerCase().includes(s)).slice(0, 6)
  }

  first(): Place | undefined {
    return this.places.find((p) => p.kind === 'waypoint')
  }
}
