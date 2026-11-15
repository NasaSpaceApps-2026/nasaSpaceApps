// Draws overlay PNGs from the query engine over the site square (Cesium SingleTileImageryProvider), one main overlay
// (lake, sun, reach, viewshed) plus one optional science layer. Slider-friendly: the previous image stays up until the new
// one has loaded, stale answers are dropped, frames are cached (so the sun animation loops without refetching) and
// `prefetch` warms the next frame.
import * as Cesium from 'cesium'
import type { Site } from '../site'
import { overlayUrl, siteRect, slotOf, type OverlayKind, type OverlayMeta, type OverlayParams, type Slot } from './overlayApi'

export interface SlotState {
  kind: OverlayKind | null
  params: OverlayParams
  meta: OverlayMeta | null
  loading: boolean
  error: string | null
  opacity: number
}
export interface OverlayState { main: SlotState; science: SlotState }

interface Frame { blobUrl: string; meta: OverlayMeta }

const emptySlot = (opacity: number): SlotState => ({ kind: null, params: {}, meta: null, loading: false, error: null, opacity })
const CACHE_MAX = 40

export class OverlayManager {
  state: OverlayState = { main: emptySlot(0.7), science: emptySlot(0.65) }
  private layers: Record<Slot, Cesium.ImageryLayer | null> = { main: null, science: null }
  private tokens: Record<Slot, number> = { main: 0, science: 0 }
  private cache = new Map<string, Promise<Frame>>()
  private listeners = new Set<() => void>()
  private disposed = false

  private viewer: Cesium.Viewer
  private site: Site
  base: string

  constructor(viewer: Cesium.Viewer, site: Site, base: string) {
    this.viewer = viewer
    this.site = site
    this.base = base
  }

  subscribe(fn: () => void): () => void { this.listeners.add(fn); return () => { this.listeners.delete(fn) } }
  private emit() { this.state = { main: { ...this.state.main }, science: { ...this.state.science } }; this.listeners.forEach((f) => f()) }

  /** Fetch (or reuse) the PNG as a blob URL and its JSON sidecar. */
  private frame(kind: OverlayKind, params: OverlayParams): Promise<Frame> {
    const pngUrl = overlayUrl(this.base, kind, params, 'png')
    const hit = this.cache.get(pngUrl)
    if (hit) { this.cache.delete(pngUrl); this.cache.set(pngUrl, hit); return hit }
    const p = (async () => {
      const [png, meta] = await Promise.all([
        fetch(pngUrl).then((r) => { if (!r.ok) throw new Error(`overlay ${kind}: HTTP ${r.status}`); return r.blob() }),
        fetch(overlayUrl(this.base, kind, params, 'json')).then((r) => { if (!r.ok) throw new Error(`overlay ${kind}: HTTP ${r.status}`); return r.json() as Promise<OverlayMeta> }),
      ])
      return { blobUrl: URL.createObjectURL(png), meta: { ...meta, legend: meta.legend ?? [] } }
    })()
    this.cache.set(pngUrl, p)
    p.catch(() => { if (this.cache.get(pngUrl) === p) this.cache.delete(pngUrl) })
    while (this.cache.size > CACHE_MAX) {
      const oldest = this.cache.keys().next().value as string
      const f = this.cache.get(oldest)
      this.cache.delete(oldest)
      void f?.then((x) => { if (![...Object.values(this.layers)].some((l) => (l as unknown as { _atlasUrl?: string } | null)?._atlasUrl === x.blobUrl)) URL.revokeObjectURL(x.blobUrl) }, () => undefined)
    }
    return p
  }

  /** Warm the cache for a frame we expect to need next (sun animation, slider neighbours). */
  prefetch(kind: OverlayKind, params: OverlayParams) { void this.frame(kind, params).catch(() => undefined) }

  async show(kind: OverlayKind, params: OverlayParams = {}): Promise<OverlayMeta | null> {
    const slot = slotOf(kind)
    const token = ++this.tokens[slot]
    const keepOld = this.state[slot].kind === kind
    this.state[slot] = { ...this.state[slot], kind, params, loading: true, error: null, meta: keepOld ? this.state[slot].meta : null }
    this.emit()
    try {
      const f = await this.frame(kind, params)
      if (this.disposed || token !== this.tokens[slot]) return null
      const half = f.meta.bounds?.half_m && f.meta.bounds.half_m > 0 ? f.meta.bounds.half_m : this.site.manifest.size_m / 2
      const r = siteRect(this.site.manifest.lat0, this.site.manifest.lon0, half)
      const provider = await Cesium.SingleTileImageryProvider.fromUrl(f.blobUrl, { rectangle: new Cesium.Rectangle(r.west, r.south, r.east, r.north) })
      if (this.disposed || token !== this.tokens[slot]) return null
      const layer = new Cesium.ImageryLayer(provider, { alpha: this.state[slot].opacity })
      ;(layer as unknown as { _atlasUrl: string })._atlasUrl = f.blobUrl
      this.viewer.imageryLayers.add(layer)
      const old = this.layers[slot]
      this.layers[slot] = layer
      if (old) window.setTimeout(() => { if (!this.viewer.isDestroyed()) this.viewer.imageryLayers.remove(old, true) }, 250) // no flash between frames
      this.state[slot] = { ...this.state[slot], meta: f.meta, loading: false, error: null }
      this.emit()
      return f.meta
    } catch (e) {
      if (this.disposed || token !== this.tokens[slot]) return null
      const msg = e instanceof TypeError ? `Cannot reach the overlay service at ${this.base}. Is the query engine running?` : String((e as Error).message ?? e)
      this.state[slot] = { ...this.state[slot], loading: false, error: msg }
      this.emit()
      return null
    }
  }

  clear(which: Slot | OverlayKind | 'all' = 'all') {
    const slots: Slot[] = which === 'all' ? ['main', 'science'] : which === 'main' || which === 'science' ? [which] : [slotOf(which)]
    for (const s of slots) {
      this.tokens[s]++
      const l = this.layers[s]
      if (l && !this.viewer.isDestroyed()) this.viewer.imageryLayers.remove(l, true)
      this.layers[s] = null
      this.state[s] = emptySlot(this.state[s].opacity)
    }
    this.emit()
  }

  setOpacity(slot: Slot, a: number) {
    const v = Math.min(1, Math.max(0.05, a))
    this.state[slot] = { ...this.state[slot], opacity: v }
    const l = this.layers[slot]
    if (l) l.alpha = v
    this.emit()
  }

  /** Hide / restore without losing state (used when the viewer leaves the Jezero view). */
  setVisible(on: boolean) { for (const l of Object.values(this.layers)) if (l) l.show = on }

  dispose() {
    this.disposed = true
    this.clear('all')
    this.cache.forEach((p) => { void p.then((f) => URL.revokeObjectURL(f.blobUrl), () => undefined) })
    this.cache.clear()
    this.listeners.clear()
  }
}
