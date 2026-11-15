// Crisp HTML place labels projected over the Cesium canvas (Cesium's own bitmap labels blur and garble at small sizes).
// One element per item, positioned every frame, shown/hidden by a throttled screen-space declutter in priority order.
import * as Cesium from 'cesium'

export interface LabelItem {
  id: string
  text: string
  /** Small second line / aria hint. */
  hint?: string
  pos: Cesium.Cartesian3
  priority: number
  kind: 'landing' | 'region' | 'sample' | 'iau' | 'pin' | 'target'
  /** Hide when the camera is farther than this many metres. */
  maxDistance?: number
}

const FONT = '600 12px system-ui, sans-serif'
let ctx: CanvasRenderingContext2D | null | undefined
function textW(t: string) {
  if (ctx === undefined) ctx = document.createElement('canvas').getContext('2d')
  if (!ctx) return t.length * 7
  ctx.font = FONT
  return ctx.measureText(t).width
}

interface Entry { item: LabelItem; el: HTMLButtonElement; w: number; vis: boolean }

export class PlaceLabels {
  private entries: Entry[] = []
  private off: () => void
  private last = 0
  private shown = new Set<string>()
  occlude = false // hide labels on the far side of the planet (globe view)
  quiet = false
  maxCount = 40
  private visible = true

  private viewer: Cesium.Viewer
  private host: HTMLElement
  private onPick: (item: LabelItem) => void

  constructor(viewer: Cesium.Viewer, host: HTMLElement, onPick: (item: LabelItem) => void) {
    this.viewer = viewer
    this.host = host
    this.onPick = onPick
    this.off = viewer.scene.postRender.addEventListener(() => this.frame())
  }

  dispose() {
    this.off()
    this.clear()
  }

  setVisible(v: boolean) {
    this.visible = v
    this.host.style.display = v ? '' : 'none'
  }

  clear() {
    for (const e of this.entries) e.el.remove()
    this.entries = []
    this.shown.clear()
  }

  set(items: LabelItem[]) {
    this.clear()
    for (const item of items) {
      const el = document.createElement('button')
      el.type = 'button'
      el.className = `plabel plabel--${item.kind}`
      el.setAttribute('aria-label', item.hint ? `${item.text}. ${item.hint}` : item.text)
      const dot = document.createElement('span')
      dot.className = 'plabel__dot'
      dot.setAttribute('aria-hidden', 'true')
      const t = document.createElement('span')
      t.className = 'plabel__text'
      t.textContent = item.text
      el.append(dot, t)
      el.style.display = 'none'
      el.addEventListener('click', (ev) => { ev.stopPropagation(); this.onPick(item) })
      el.addEventListener('pointerdown', (ev) => ev.stopPropagation())
      this.host.appendChild(el)
      this.entries.push({ item, el, w: textW(item.text) + 28, vis: false })
    }
    this.entries.sort((a, b) => a.item.priority - b.item.priority)
    this.last = 0
  }

  private frame() {
    if (!this.visible || !this.entries.length || this.viewer.isDestroyed()) return
    const scene = this.viewer.scene
    const W = scene.canvas.clientWidth, H = scene.canvas.clientHeight
    const camPos = this.viewer.camera.positionWC
    const redo = performance.now() - this.last > 120
    if (redo) this.last = performance.now()
    const camLen = Cesium.Cartesian3.magnitude(camPos)
    const camDir = this.occlude ? Cesium.Cartesian3.normalize(camPos, new Cesium.Cartesian3()) : undefined
    const placed: { x0: number; y0: number; x1: number; y1: number }[] = []
    const heightAbove = Math.max(60, this.viewer.camera.positionCartographic.height + 2500)
    const cap = this.maxCount * (heightAbove < 2500 ? 1 : heightAbove < 8000 ? 0.6 : 0.35)
    let n = 0
    for (const e of this.entries) {
      let show = !this.quiet
      let w: Cesium.Cartesian2 | undefined
      if (show) {
        if (e.item.maxDistance && Cesium.Cartesian3.distance(camPos, e.item.pos) > e.item.maxDistance) show = false
        if (show && camDir && Cesium.Cartesian3.dot(camDir, Cesium.Cartesian3.normalize(e.item.pos, new Cesium.Cartesian3())) < 3396190 / camLen) show = false
        if (show) {
          w = Cesium.SceneTransforms.worldToWindowCoordinates(scene, e.item.pos)
          if (!w || w.x < 6 || w.y < 28 || w.x > W - 6 || w.y > H - 6) show = false
        }
      }
      if (show && w) {
        if (redo) {
          const r = { x0: w.x - 6, y0: w.y - 10, x1: w.x - 6 + e.w, y1: w.y + 10 }
          const clash = placed.some((p) => r.x0 < p.x1 && p.x0 < r.x1 && r.y0 < p.y1 && p.y0 < r.y1)
          const limited = e.item.priority > 1 && n >= cap
          e.vis = !clash && !limited
          if (e.vis) { placed.push(r); n++ }
        } else if (e.vis) placed.push({ x0: w.x - 6, y0: w.y - 10, x1: w.x - 6 + e.w, y1: w.y + 10 })
      } else e.vis = false
      if (e.vis && w) {
        e.el.style.display = ''
        e.el.style.transform = `translate(${Math.round(w.x)}px, ${Math.round(w.y)}px)`
      } else if (e.el.style.display !== 'none') e.el.style.display = 'none'
    }
  }
}
