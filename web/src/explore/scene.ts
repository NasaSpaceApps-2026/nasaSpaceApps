// Cesium-side helpers for the Explore tools: pins, camera flights, and the rover-history layer.
import * as Cesium from 'cesium'
import type { Site } from '../site'
import { progressAt, type SampleSite, type TravSeg } from './historyData'

export function pinImage(letter: string, fill: string, size = 56): string {
  const c = document.createElement('canvas')
  c.width = c.height = size
  const g = c.getContext('2d')!
  const m = size / 2
  g.beginPath(); g.arc(m, m, size * 0.39, 0, Math.PI * 2); g.fillStyle = fill; g.fill()
  g.lineWidth = size * 0.09; g.strokeStyle = '#14100e'; g.stroke()
  if (letter) {
    g.fillStyle = '#14100e'; g.font = `700 ${Math.round(size * 0.46)}px system-ui, sans-serif`; g.textAlign = 'center'; g.textBaseline = 'middle'
    g.fillText(letter, m, m + size * 0.04)
  }
  return c.toDataURL()
}

/** Fly to a site-metre point from the south at a given pitch and range (m). */
export function flyToXY(viewer: Cesium.Viewer, site: Site, at: [number, number], range: number, pitchDeg: number, duration = 2.5, heading = 0) {
  viewer.camera.cancelFlight()
  viewer.camera.flyToBoundingSphere(new Cesium.BoundingSphere(site.position(at[0], at[1], 0), Math.min(range * 0.2, 800)), {
    offset: new Cesium.HeadingPitchRange(Cesium.Math.toRadians(heading), Cesium.Math.toRadians(pitchDeg), range),
    duration,
  })
}

const has = (site: Site, x: number, y: number) => !Number.isNaN(site.elevationAtXY(x, y))

/** The Perseverance traverse grown sol by sol, with sample-site pins that appear on their sol. */
export class HistoryLayer {
  private ds = new Cesium.CustomDataSource('atlas-history')
  private segEnts: Cesium.Entity[][] = []
  private sampleEnts: Cesium.Entity[] = []
  private rover: Cesium.Entity
  private count = 0
  private lastSol = Number.POSITIVE_INFINITY

  private viewer: Cesium.Viewer
  private site: Site
  private segs: TravSeg[]
  private samples: SampleSite[]

  constructor(viewer: Cesium.Viewer, site: Site, segs: TravSeg[], samples: SampleSite[]) {
    this.viewer = viewer
    this.site = site
    this.segs = segs
    this.samples = samples
    void viewer.dataSources.add(this.ds)
    const material = () => new Cesium.PolylineOutlineMaterialProperty({ color: Cesium.Color.fromCssColorString('#fff0a8'), outlineWidth: 1.5, outlineColor: Cesium.Color.fromCssColorString('#3a1608').withAlpha(0.8) })
    for (const g of segs) {
      const ents: Cesium.Entity[] = []
      let run: Cesium.Cartesian3[] = []
      const flush = () => {
        if (run.length >= 2) ents.push(this.ds.entities.add({ show: false, polyline: { positions: run, width: 4, arcType: Cesium.ArcType.NONE, material: material() } }))
        run = []
      }
      const push = (x: number, y: number) => { if (has(site, x, y)) run.push(site.position(x, y, 0.6)); else flush() }
      for (let i = 0; i < g.xy.length - 1; i++) {
        const [x0, y0] = g.xy[i], [x1, y1] = g.xy[i + 1]
        const n = Math.max(1, Math.ceil(Math.hypot(x1 - x0, y1 - y0) / 4))
        for (let k = 0; k < n; k++) push(x0 + ((x1 - x0) * k) / n, y0 + ((y1 - y0) * k) / n)
      }
      const last = g.xy[g.xy.length - 1]
      push(last[0], last[1])
      flush()
      this.segEnts.push(ents)
    }
    const pin = pinImage('', '#35e0ff', 40)
    for (const s of samples) {
      if (!has(site, s.x, s.y)) { this.sampleEnts.push(this.ds.entities.add({ show: false })); continue }
      this.sampleEnts.push(this.ds.entities.add({
        show: false, position: site.position(s.x, s.y, 3),
        billboard: { image: pin, width: 18, height: 18, disableDepthTestDistance: Number.POSITIVE_INFINITY },
        label: { text: `sol ${s.sol}`, font: '600 12px system-ui, sans-serif', fillColor: Cesium.Color.WHITE, outlineColor: Cesium.Color.BLACK, outlineWidth: 3,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE, pixelOffset: new Cesium.Cartesian2(0, -16), distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 2500),
          disableDepthTestDistance: Number.POSITIVE_INFINITY },
      }))
    }
    const first = segs[0]?.xy[0] ?? [0, 0]
    this.rover = this.ds.entities.add({ show: false, position: site.position(first[0], first[1], 2),
      point: { pixelSize: 13, color: Cesium.Color.fromCssColorString('#e0794a'), outlineColor: Cesium.Color.WHITE, outlineWidth: 2.5, disableDepthTestDistance: Number.POSITIVE_INFINITY } })
  }

  /** Show everything up to and including `sol`. Only entities that change are touched. */
  setSol(sol: number) {
    if (sol === this.lastSol) return
    this.lastSol = sol
    const { count } = progressAt(this.segs, sol)
    const [a, b] = count >= this.count ? [this.count, count] : [count, this.count]
    for (let i = a; i < b; i++) for (const e of this.segEnts[i]) e.show = count > i
    this.count = count
    // Labels crowd each other in the overview: only the newest sample is always labelled, the rest within 2.5 km.
    const newest = this.samples.reduce((k, s, i) => (s.sol <= sol && (k < 0 || s.sol >= this.samples[k].sol) ? i : k), -1)
    const near = new Cesium.DistanceDisplayCondition(0, 2500)
    this.samples.forEach((s, i) => {
      const e = this.sampleEnts[i]
      e.show = s.sol <= sol
      if (e.label) e.label.distanceDisplayCondition = (i === newest ? undefined : near) as unknown as Cesium.Property
    })
    if (count > 0) {
      const g = this.segs[count - 1]
      const end = g.xy[g.xy.length - 1]
      this.rover.position = new Cesium.ConstantPositionProperty(this.site.position(end[0], end[1], 2))
      this.rover.show = true
    } else this.rover.show = false
    this.viewer.scene.requestRender()
  }

  dispose() { if (!this.viewer.isDestroyed()) this.viewer.dataSources.remove(this.ds, true) }
}
