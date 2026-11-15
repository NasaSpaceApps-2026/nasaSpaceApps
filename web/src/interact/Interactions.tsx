// Map interactions: info cards (terrain click, place labels, copilot highlights, globe pins), the measure tool,
// crisp place labels and highlight tooltips. Data comes straight from the REST APIs (interact/api.ts); the viewer
// shell (App.tsx) owns cameras and modes and is reached through the props below.
import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from 'react'
import * as Cesium from 'cesium'
import { SPHERE, type Site } from '../site'
import type { Place } from '../places'
import { BUBBLES, type Bubble } from '../bubbles'
import type { DockApi } from '../panels/chat/AtlasDock'
import type { Feature, RouteLine } from '../panels/chat/features'
import { PlaceLabels, type LabelItem } from './labels'
import { addMarker, ApiError, describeAt, driveTo, refreshPois, STATIC_POIS, removeMarker, terrainProfile, walkRoute,
  type ProfileResult, type WalkRoute } from './api'
import { fmtDist, fmtLatLon, fmtMinutes, mineralLines, noMineralData, placeQuestions, placeSummary, placesFromPois, qualityWords,
  slopeWords, type DescribeLoc, type NamedPlace, type Poi } from './words'
import './interact.css'

export interface Highlight { feature?: Feature; route?: RouteLine }
export interface InteractionsHandle {
  highlight: (h: Highlight | null, kind: 'click' | 'hover', sx: number, sy: number) => void
  planet: (c: { lat: number; lon: number; label?: string } | null) => void
  closeCard: () => void
}

interface Props {
  viewer: Cesium.Viewer | null
  site: Site | null
  mode: 'mars' | 'jezero' | 'drive'
  /** The viewer's camera mode: labels go quiet while the chase camera follows the rover. */
  cameraMode: string
  namesOn: boolean
  measureOn: boolean
  setMeasureOn: (on: boolean) => void
  getPlaces: () => Place[]
  dockApi: MutableRefObject<DockApi | null>
  note: (msg: string) => void
  onOwnDrive: () => void
  onJezero: () => void
  onDriveBubble: (b: Bubble) => void
  handleRef: MutableRefObject<InteractionsHandle | null>
  /** Explore: while set, the next terrain click (site metres) goes here instead of opening the info card. */
  pickRef?: MutableRefObject<((xy: [number, number]) => void) | null>
}

type CardSpec =
  | { k: 'terrain'; x: number; y: number }
  | { k: 'place'; place: NamedPlace }
  | { k: 'feature'; feature?: Feature; route?: RouteLine }
  | { k: 'pin'; title: string; lat: number; lon: number; bubble?: Bubble }
  | { k: 'measure' }

interface Measure { a: [number, number]; b?: [number, number]; profile?: ProfileResult; route?: WalkRoute; err?: string; loading: boolean }

const TOOL_WORDS: Record<string, string> = {
  find_locations: 'a landing-pad search', find_regions: 'a mineral-region search', find_sites: 'a mineral-region search', search_pois: 'a place search',
  nearest: 'a nearest-place search', place_marker: 'a marker', plan_route: 'a route plan', plan_science_traverse: 'a science traverse', list_markers: 'the marker list',
  get_set: 'a saved result set', set_view: 'a view change', assess_route: 'a route check', 'route.set': 'the rover route',
}
const KIND_WORDS: Record<Feature['kind'], string> = { spot: 'Candidate landing pad', region: 'Mineral region', poi: 'Point of interest', marker: 'Marker', stop: 'Route stop', target: 'Target' }

const rad2deg = (r: number) => (r * 180) / Math.PI
const D = (k: string, ...r: unknown[]) => [k, ...r].join(':')

function cardKey(c: CardSpec): string {
  switch (c.k) {
    case 'terrain': return D('t', c.x.toFixed(0), c.y.toFixed(0))
    case 'place': return D('p', c.place.id)
    case 'feature': return D('f', c.feature?.id ?? c.route?.id)
    case 'pin': return D('pin', c.title)
    default: return 'm'
  }
}

function pinImage(letter: string, fill: string): string {
  const c = document.createElement('canvas')
  c.width = c.height = 56
  const g = c.getContext('2d')!
  g.beginPath(); g.arc(28, 28, 22, 0, Math.PI * 2); g.fillStyle = fill; g.fill()
  g.lineWidth = 5; g.strokeStyle = '#14100e'; g.stroke()
  g.fillStyle = '#14100e'; g.font = '700 26px system-ui, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(letter, 28, 30)
  return c.toDataURL()
}

export function Interactions(p: Props) {
  const { viewer, site, mode, namesOn, measureOn, setMeasureOn } = p
  const host = useRef<HTMLDivElement>(null)
  const cardEl = useRef<HTMLElement | null>(null)
  const [card, setCard] = useState<CardSpec | null>(null)
  const [tip, setTip] = useState<{ title: string; sub: string; x: number; y: number } | null>(null)
  const [target, setTarget] = useState<{ lat: number; lon: number; label?: string } | null>(null)
  const [meas, setMeas] = useState<Measure | null>(null)
  const [pois, setPois] = useState<Poi[]>([])
  const [places, setPlaces] = useState<NamedPlace[]>([])
  const [facts, setFacts] = useState<{ state: 'loading' | 'ok' | 'err'; loc?: DescribeLoc; err?: string }>({ state: 'loading' })
  const [routeProfile, setRouteProfile] = useState<ProfileResult | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const labels = useRef<PlaceLabels | null>(null)
  const pickers = useRef(new Map<string, CardSpec>())
  const props = useRef(p)
  props.current = p
  const cardRef = useRef(card)
  cardRef.current = card
  const measRef = useRef(meas)
  measRef.current = meas
  const measDs = useRef<Cesium.CustomDataSource | null>(null)

  // ---- named places (POIs + IAU names) -----------------------------------------------------------------------------
  useEffect(() => {
    if (!site) return
    let dead = false
    setPois(STATIC_POIS)
    void refreshPois().then((ps) => { if (!dead && ps) setPois(ps) })
    return () => { dead = true }
  }, [site])
  useEffect(() => {
    if (!site) return
    let tries = 0
    let dead = false
    let timer = 0
    const build = () => {
      if (dead) return
      const iau = props.current.getPlaces().filter((q) => q.kind === 'name')
      if (!iau.length && tries++ < 40) { timer = window.setTimeout(build, 500); return }
      const half = site.manifest.size_m / 2
      const iauPl: NamedPlace[] = iau.filter((q) => Math.abs(q.x) <= half && Math.abs(q.y) <= half)
        .map((q) => ({ id: 'iau:' + q.id, name: q.name, kind: 'iau' as const, x: q.x, y: q.y, priority: 2, also: [], iauDetail: `${q.detail ?? 'Official IAU name'}` }))
      setPlaces([...placesFromPois(pois), ...iauPl])
    }
    // Render POIs right away; IAU names are merged in once they have loaded (stale timers are cancelled).
    setPlaces(placesFromPois(pois))
    build()
    return () => { dead = true; window.clearTimeout(timer) }
  }, [site, pois])

  // ---- label overlay -----------------------------------------------------------------------------------------------
  useEffect(() => {
    if (!viewer || !host.current) return
    const pl = new PlaceLabels(viewer, host.current, (item) => {
      const spec = pickers.current.get(item.id)
      if (spec) setCard(spec)
    })
    labels.current = pl
    return () => { pl.dispose(); labels.current = null }
  }, [viewer])

  useEffect(() => {
    const pl = labels.current
    if (!pl || !site) return
    pickers.current.clear()
    const items: LabelItem[] = []
    if (mode === 'jezero') {
      for (const q of places) {
        const id = 'pl:' + q.id
        pickers.current.set(id, { k: 'place', place: q })
        items.push({ id, text: q.name, hint: q.kind === 'region' ? 'Sampling area' : q.kind === 'sample' ? 'Rover sample site' : q.kind === 'landing' ? 'Landing site' : q.kind === 'base' ? 'Assumed mission base' : 'IAU place name',
          pos: site.position(q.x, q.y, 30), priority: q.priority, kind: q.kind, ...(q.kind === 'base' ? { always: true, dy: -34 } : {}), maxDistance: q.kind === 'sample' ? 22000 : undefined })
      }
      pl.occlude = false
    } else if (mode === 'mars') {
      for (const b of BUBBLES) {
        const id = 'pin:' + b.id
        pickers.current.set(id, { k: 'pin', title: b.title, lat: b.lat, lon: b.lon, bubble: b })
        items.push({ id, text: b.title, hint: 'Street-view patch', pos: Cesium.Cartesian3.fromDegrees(eastLon(b.lon), b.lat, 0, SPHERE), priority: 0, kind: 'pin' })
      }
      if (target) {
        const id = 'pin:target'
        pickers.current.set(id, { k: 'pin', title: target.label ?? 'Target', lat: target.lat, lon: target.lon })
        items.push({ id, text: target.label ?? 'Target', hint: 'Copilot target', pos: Cesium.Cartesian3.fromDegrees(eastLon(target.lon), target.lat, 0, SPHERE), priority: -1, kind: 'target' })
      }
      pl.occlude = true
    }
    pl.set(items)
    pl.setVisible(items.length > 0 && (mode !== 'jezero' || namesOn))
  }, [viewer, site, mode, places, target, namesOn])
  useEffect(() => { if (labels.current) labels.current.quiet = p.cameraMode === 'rover' }, [p.cameraMode])
  useEffect(() => { if (mode !== 'mars') setTarget(null) }, [mode])

  // ---- card plumbing -----------------------------------------------------------------------------------------------
  const close = useCallback(() => { setCard(null); setMeas(null); setRouteProfile(null) }, [])
  useEffect(() => { if (mode === 'drive') close() }, [mode, close])

  const cardXY = (c: CardSpec): [number, number] | null => {
    if (c.k === 'terrain') return [c.x, c.y]
    if (c.k === 'place') return [c.place.x, c.place.y]
    if (c.k === 'feature') {
      if (c.feature) return [c.feature.x, c.feature.y]
      const pts = c.route?.points
      return pts?.length ? pts[Math.floor(pts.length / 2)] : null
    }
    return null
  }

  // Describe at the card's point (terrain, place and feature cards).
  useEffect(() => {
    if (!card) return
    const xy = card.k === 'measure' ? null : cardXY(card)
    if (!xy || (card.k === 'feature' && card.route)) { setFacts({ state: 'ok' }); return }
    setFacts({ state: 'loading' })
    const ctl = new AbortController()
    describeAt(xy[0], xy[1], ctl.signal).then((loc) => setFacts({ state: 'ok', loc }),
      (e) => { if ((e as Error).name !== 'AbortError') setFacts({ state: 'err', err: e instanceof ApiError ? e.message : 'Could not load data.' }) })
    if (card.k === 'feature' && card.route) return () => ctl.abort()
    return () => ctl.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [card ? cardKey(card) : ''])

  // Route card: elevation profile along the route.
  useEffect(() => {
    setRouteProfile(null)
    if (!card || card.k !== 'feature' || !card.route || card.route.points.length < 2) return
    const ctl = new AbortController()
    terrainProfile(card.route.points, ctl.signal).then(setRouteProfile, () => undefined)
    return () => ctl.abort()
  }, [card])

  // Anchor the card to its map point every frame (direct style writes, no React renders).
  useEffect(() => {
    if (!viewer || !site) return
    const off = viewer.scene.postRender.addEventListener(() => {
      const el = cardEl.current, hostEl = host.current, c = cardRef.current
      if (!el || !hostEl || !c) return
      let world: Cesium.Cartesian3 | undefined
      const xy = c.k === 'measure' ? (measRef.current?.b ?? measRef.current?.a) : c.k === 'pin' ? null : cardXY(c)
      if (xy) world = site.position(xy[0], xy[1], 4)
      else if (c.k === 'pin') world = Cesium.Cartesian3.fromDegrees(eastLon(c.lon), c.lat, 0, SPHERE)
      if (!world) return
      const w = Cesium.SceneTransforms.worldToWindowCoordinates(viewer.scene, world)
      const W = hostEl.clientWidth, H = hostEl.clientHeight, cw = el.offsetWidth, ch = el.offsetHeight
      let x = (w?.x ?? W / 2) + 20, y = (w?.y ?? H / 2) - 28
      if (x + cw > W - 8) x = (w?.x ?? W / 2) - 20 - cw
      x = Math.max(8, Math.min(W - cw - 8, x))
      y = Math.max(8, Math.min(H - ch - 8, y))
      el.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`
    })
    return () => off()
  }, [viewer, site])
  const setCardEl = useCallback((el: HTMLElement | null) => {
    cardEl.current = el
    if (el) el.focus({ preventScroll: true })
  }, [])

  // ---- map clicks --------------------------------------------------------------------------------------------------
  const terrainAt = useCallback((pos: Cesium.Cartesian2): [number, number] | null => {
    if (!viewer || !site) return null
    const ray = viewer.camera.getPickRay(pos)
    const hit = ray ? viewer.scene.globe.pick(ray, viewer.scene) : undefined
    if (!hit) return null
    const g = Cesium.Cartographic.fromCartesian(hit, SPHERE)
    return site.lonLatToXY(g.longitude, g.latitude)
  }, [viewer, site])

  const addMeasurePoint = useCallback((xy: [number, number]) => {
    if (!site || Number.isNaN(site.elevationAtXY(xy[0], xy[1]))) { p.note('Click inside the Jezero map to measure'); return }
    const cur = measRef.current
    if (!cur || cur.b) { setMeas({ a: xy, loading: false }); setCard(null); return }
    setMeas({ a: cur.a, b: xy, loading: true })
  }, [site, p])

  useEffect(() => {
    if (!viewer || !site) return
    const h = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas)
    h.setInputAction((ev: { position: Cesium.Cartesian2 }) => {
      const pr = props.current
      const picked = viewer.scene.pick(ev.position) as { id?: Cesium.Entity } | undefined
      const eid = typeof picked?.id?.id === 'string' ? picked.id.id : ''
      if (pr.mode === 'mars') {
        let bid = eid.startsWith('bubble:') ? eid.slice(7) : ''
        if (!bid) { // screen-space fallback (scene.pick misses the pin dot on some renderers)
          for (const q of BUBBLES) {
            const w = Cesium.SceneTransforms.worldToWindowCoordinates(viewer.scene, Cesium.Cartesian3.fromDegrees(eastLon(q.lon), q.lat, 0, SPHERE))
            if (w && Math.hypot(w.x - ev.position.x, w.y - ev.position.y) <= 16) bid = q.id
          }
        }
        if (bid) {
          const b = BUBBLES.find((q) => q.id === bid)
          if (b) setCard({ k: 'pin', title: b.title, lat: b.lat, lon: b.lon, bubble: b })
        } else setCard(null)
        return
      }
      if (pr.mode !== 'jezero') return
      if (pr.pickRef?.current) { // Explore is waiting for a point
        const xy = terrainAt(ev.position)
        if (xy && site && !Number.isNaN(site.elevationAtXY(xy[0], xy[1]))) pr.pickRef.current(xy)
        else pr.note('Click inside the Jezero map')
        return
      }
      if (!pr.measureOn && (/^(feat|halo|ring|route):/.test(eid) || pr.dockApi.current?.hit(ev.position))) return // the dock opens these (highlight card)
      const xy = terrainAt(ev.position)
      if (!xy) { setCard(null); return }
      if (pr.measureOn) addMeasurePoint(xy)
      else setCard({ k: 'terrain', x: xy[0], y: xy[1] })
    }, Cesium.ScreenSpaceEventType.LEFT_CLICK)
    h.setInputAction((ev: { endPosition: Cesium.Cartesian2 }) => {
      const pr = props.current
      const picked = viewer.scene.pick(ev.endPosition) as { id?: Cesium.Entity } | undefined
      const eid = typeof picked?.id?.id === 'string' ? picked.id.id : ''
      viewer.canvas.style.cursor = pr.measureOn || pr.pickRef?.current ? 'crosshair' : pr.mode === 'mars' && eid.startsWith('bubble:') ? 'pointer' : ''
    }, Cesium.ScreenSpaceEventType.MOUSE_MOVE)
    return () => { h.destroy(); if (!viewer.isDestroyed()) viewer.canvas.style.cursor = '' }
  }, [viewer, site, terrainAt, addMeasurePoint])

  // ---- measure tool ------------------------------------------------------------------------------------------------
  useEffect(() => {
    if (!viewer) return
    const ds = new Cesium.CustomDataSource('atlas-measure')
    void viewer.dataSources.add(ds)
    measDs.current = ds
    const holder = measDs
    return () => { if (!viewer.isDestroyed()) viewer.dataSources.remove(ds, true); holder.current = null }
  }, [viewer])

  useEffect(() => {
    if (!measureOn) { setMeas(null); setCard((c) => (c?.k === 'measure' ? null : c)) }
  }, [measureOn])

  // Draw A, B, the straight line and (when known) the walking route.
  useEffect(() => {
    const ds = measDs.current
    if (!ds || !site) return
    ds.entities.removeAll()
    if (!meas) return
    const at = (xy: [number, number], up = 2) => site.position(xy[0], xy[1], up)
    const pin = (id: string, xy: [number, number], letter: string, fill: string) => ds.entities.add({ id, position: at(xy, 3),
      billboard: { image: pinImage(letter, fill), width: 26, height: 26, disableDepthTestDistance: Number.POSITIVE_INFINITY } })
    pin('A', meas.a, 'A', '#ffffff')
    if (!meas.b) return
    pin('B', meas.b, 'B', '#e0794a')
    const n = Math.max(2, Math.ceil(Math.hypot(meas.b[0] - meas.a[0], meas.b[1] - meas.a[1]) / 8))
    const pos = Array.from({ length: n + 1 }, (_, i) => at([meas.a[0] + ((meas.b![0] - meas.a[0]) * i) / n, meas.a[1] + ((meas.b![1] - meas.a[1]) * i) / n]))
    ds.entities.add({ polyline: { positions: pos, width: 5, arcType: Cesium.ArcType.NONE,
      material: new Cesium.PolylineOutlineMaterialProperty({ color: Cesium.Color.WHITE, outlineWidth: 2, outlineColor: Cesium.Color.BLACK.withAlpha(0.85) }) } })
    if (meas.route?.points?.length) {
      ds.entities.add({ polyline: { positions: meas.route.points.map((q) => at([q.x, q.y], 1.5)), width: 4, arcType: Cesium.ArcType.NONE,
        material: new Cesium.PolylineDashMaterialProperty({ color: Cesium.Color.fromCssColorString('#35e0ff'), gapColor: Cesium.Color.TRANSPARENT, dashLength: 14 }) } })
    }
  }, [meas, site])

  const bKey = meas?.b ? `${meas.a}|${meas.b}` : ''
  useEffect(() => {
    const m = measRef.current
    if (!m?.b) return
    const a = m.a, b = m.b
    setCard({ k: 'measure' })
    const ctl = new AbortController()
    void Promise.allSettled([terrainProfile([a, b], ctl.signal), walkRoute(a, b, ctl.signal)]).then(([pr, rt]) => {
      if (ctl.signal.aborted) return
      setMeas((cur) => (cur && cur.b === b ? {
        ...cur, loading: false, profile: pr.status === 'fulfilled' ? pr.value : undefined, route: rt.status === 'fulfilled' ? rt.value : undefined,
        err: pr.status === 'rejected' && rt.status === 'rejected' ? (rt.reason instanceof ApiError ? rt.reason.message : 'Could not reach the query engine.') : undefined,
      } : cur))
    })
    return () => ctl.abort()
  }, [bKey])

  // ---- keys: M toggles the tool, Esc closes the card, then cancels the tool -----------------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      const typing = !!el && (el.isContentEditable || el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT')
      if (e.key === 'Escape') {
        if (cardRef.current) { close(); if (props.current.measureOn) props.current.setMeasureOn(false) }
        else if (props.current.measureOn) props.current.setMeasureOn(false)
        return
      }
      if (typing || e.ctrlKey || e.metaKey || e.altKey || e.code !== 'KeyM' || props.current.mode !== 'jezero') return
      props.current.setMeasureOn(!props.current.measureOn)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [close])

  // ---- handle for the shell and the dock ---------------------------------------------------------------------------
  useEffect(() => {
    p.handleRef.current = {
      highlight: (h, kind, sx, sy) => {
        if (kind === 'hover') {
          const pr = props.current
          if (!h || pr.mode !== 'jezero' || pr.measureOn) { setTip(null); return }
          const f = h.feature
          setTip(f ? { title: f.label, sub: `${KIND_WORDS[f.kind]}${f.tier ? ` · ${f.tier}` : ''}`, x: sx, y: sy }
            : { title: h.route?.label ?? 'Route', sub: 'Copilot route', x: sx, y: sy })
        } else if (h && !props.current.measureOn) { setTip(null); setCard({ k: 'feature', feature: h.feature, route: h.route }) }
      },
      planet: (c) => setTarget(c),
      closeCard: close,
    }
    return () => { p.handleRef.current = null }
  })

  // ---- actions -----------------------------------------------------------------------------------------------------
  const ask = (text: string) => {
    if (!p.dockApi.current?.ask(text)) p.note('The copilot is still answering. Try again in a moment.')
    else close()
  }
  const driveHere = async (x: number, y: number, label: string) => {
    setBusy('drive')
    p.onOwnDrive()
    try {
      const r = await driveTo(x, y)
      if (r.stop_reason && r.stop_reason !== 'arrived' && !r.started) p.note(r.message ?? `Could not start the drive (${r.stop_reason})`)
      else { p.note(`Driving to ${label}`); close(); setMeasureOn(false) }
    } catch (e) {
      p.note(e instanceof ApiError ? `Drive failed: ${e.message}` : 'Drive failed')
    } finally { setBusy(null) }
  }
  const dropMarker = async (x: number, y: number, label?: string) => {
    setBusy('marker')
    try {
      const m = await addMarker(x, y, label)
      p.dockApi.current?.showFeatures([{ id: String(m.id), kind: 'marker', x: m.x, y: m.y, label: m.label || label || 'Marker', source: 'place_marker' }])
      p.note('Marker dropped')
      close()
    } catch (e) { p.note(e instanceof ApiError ? `Marker failed: ${e.message}` : 'Marker failed') } finally { setBusy(null) }
  }
  const unMark = async (id: string) => {
    setBusy('marker')
    try { await removeMarker(id) } catch { /* already gone */ }
    p.dockApi.current?.hideFeature(id)
    setBusy(null)
    close()
  }
  const latLon = (x: number, y: number) => {
    const [lo, la] = site!.xyToLonLat(x, y)
    return fmtLatLon(rad2deg(la), rad2deg(lo))
  }

  // ---- render ------------------------------------------------------------------------------------------------------
  const body = useMemo(() => {
    if (!card || !site) return null
    return card
  }, [card, site])

  let content: React.ReactNode = null
  if (body && site) {
    const c = body
    if (c.k === 'terrain') {
      content = (
        <Card title="Terrain point" sub={`${latLon(c.x, c.y)}  ·  x ${c.x.toFixed(0)}, y ${c.y.toFixed(0)} m`} onClose={close} cardRef={setCardEl}
          actions={[
            { label: 'Drive here', primary: true, busy: busy === 'drive', onClick: () => driveHere(c.x, c.y, 'this point') },
            { label: 'Ask the copilot', onClick: () => ask(`Tell me about the point at (${c.x.toFixed(0)}, ${c.y.toFixed(0)}) m, ${latLon(c.x, c.y)}: terrain, geology, minerals, and whether it is safe to walk or drive there.`) },
            { label: 'Drop marker', busy: busy === 'marker', onClick: () => dropMarker(c.x, c.y) },
          ]}>
          <Facts facts={facts} />
        </Card>
      )
    } else if (c.k === 'place') {
      const pl = c.place
      const lines = placeSummary(pl, pois)
      content = (
        <Card title={pl.name} badge={pl.kind === 'region' ? 'Sampling area' : pl.kind === 'sample' ? 'Sample site' : pl.kind === 'landing' ? 'Landing site' : pl.kind === 'base' ? 'Assumed base' : 'IAU name'}
          sub={`${latLon(pl.x, pl.y)}`} onClose={close} cardRef={setCardEl}
          actions={[
            { label: 'Drive here', primary: true, busy: busy === 'drive', onClick: () => driveHere(pl.x, pl.y, pl.name) },
            { label: 'Ask the copilot', onClick: () => ask(placeQuestions(pl)[0]) },
            { label: 'Drop marker', busy: busy === 'marker', onClick: () => dropMarker(pl.x, pl.y, pl.name) },
          ]}>
          {lines.map((l, i) => <p key={i} className="icard__p">{l}</p>)}
          {pl.also.length > 0 && pl.kind === 'landing' && <p className="icard__p">Also the assumed mission base.</p>}
          <div className="icard__chips" role="group" aria-label="Suggested questions">
            {placeQuestions(pl).map((q) => <button key={q} type="button" className="icard__chip" onClick={() => ask(q)}>{q}</button>)}
          </div>
          {pl.poi?.source_url && <a className="icard__link" href={pl.poi.source_url} target="_blank" rel="noreferrer">Source</a>}
          <h4 className="icard__h">Terrain here</h4>
          <Facts facts={facts} />
        </Card>
      )
    } else if (c.k === 'feature') {
      const f = c.feature
      const r = c.route
      let len = 0
      if (r) for (let i = 1; i < r.points.length; i++) len += Math.hypot(r.points[i][0] - r.points[i - 1][0], r.points[i][1] - r.points[i - 1][1])
      const from = f ? TOOL_WORDS[f.source] ?? f.source : r ? TOOL_WORDS[r.source] ?? r.source : ''
      content = (
        <Card title={f?.label ?? r?.label ?? 'Route'} badge={f ? KIND_WORDS[f.kind] : 'Copilot route'} sub={f ? latLon(f.x, f.y) : `${fmtDist(len)} long`} onClose={close} cardRef={setCardEl}
          actions={f ? [
            { label: 'Drive here', primary: true, busy: busy === 'drive', onClick: () => driveHere(f.x, f.y, f.label) },
            { label: 'Ask the copilot', onClick: () => ask(`Tell me about "${f.label}" at (${f.x.toFixed(0)}, ${f.y.toFixed(0)}) m: why was it picked and is it safe to reach?`) },
            f.kind === 'marker'
              ? { label: 'Remove marker', busy: busy === 'marker', onClick: () => unMark(f.id) }
              : { label: 'Drop marker', busy: busy === 'marker', onClick: () => dropMarker(f.x, f.y, f.label) },
          ] : [
            { label: 'Ask the copilot', primary: true, onClick: () => ask('How long is the route on the map, how steep does it get, and can the crew do it within the EVA limit?') },
          ]}>
          <p className="icard__p">Found by {from}.</p>
          {f?.tier && <p className="icard__p">Result: {f.tier}.</p>}
          {f?.radius ? <p className="icard__p">Clear area: radius {Math.round(f.radius)} m around this point.</p> : null}
          {r && <p className="icard__p">From ({r.points[0].map(Math.round).join(', ')}) to ({r.points[r.points.length - 1].map(Math.round).join(', ')}) m.</p>}
          {r && routeProfile && (<><h4 className="icard__h">Elevation along the route</h4><Sparkline samples={routeProfile.samples.map((s) => ({ d: s.d, z: s.z, slope: s.slope_deg }))} />
            <p className="icard__p">Steepest {routeProfile.max_slope_deg.toFixed(1)}° ({slopeWords(routeProfile.max_slope_deg)}). Climb {Math.round(routeProfile.ascent_m)} m, descent {Math.round(routeProfile.descent_m)} m.</p></>)}
          {f && (<><h4 className="icard__h">Terrain here</h4><Facts facts={facts} /></>)}
        </Card>
      )
    } else if (c.k === 'pin') {
      const b = c.bubble
      content = (
        <Card title={c.title} badge={b ? 'Street-view patch' : 'Copilot target'} sub={fmtLatLon(c.lat, c.lon)} onClose={close} cardRef={setCardEl}
          actions={[
            ...(b ? [{ label: b.id === 'crater' ? 'Explore Jezero' : 'Explore Mons', primary: true, onClick: () => { close(); p.onDriveBubble(b) } }] : []),
            ...(b?.id === 'crater' ? [{ label: 'Site analysis', onClick: () => { close(); p.onJezero() } }] : []),
            { label: 'Fly in', onClick: () => { flyIn(viewer!, c.lat, c.lon, b ? 60_000 : 400_000); close() } },
            { label: 'Ask the copilot', onClick: () => ask(`Tell me about ${c.title} (${fmtLatLon(c.lat, c.lon)}).`) },
          ]}>
          <p className="icard__p">{b ? `A ${(b.sizeM / 1000).toFixed(1)} km square of high-resolution terrain you can walk or drive through. "Explore" opens that patch in street view with the buggy.` : 'Where the copilot pointed the globe.'}</p>
        </Card>
      )
    } else if (c.k === 'measure' && meas) {
      const straight = meas.b ? Math.hypot(meas.b[0] - meas.a[0], meas.b[1] - meas.a[1]) : 0
      const samples = meas.profile?.samples.map((s) => ({ d: s.d, z: s.z, slope: s.slope_deg }))
        ?? (meas.route ? routeSamples(meas.route) : undefined)
      content = (
        <Card title="Measure A to B" sub={meas.b ? `${latLon(meas.a[0], meas.a[1])}  to  ${latLon(meas.b[0], meas.b[1])}` : latLon(meas.a[0], meas.a[1])} onClose={() => { close(); setMeasureOn(false) }} cardRef={setCardEl}
          actions={meas.b ? [
            { label: 'Drive to B', primary: true, busy: busy === 'drive', onClick: () => driveHere(meas.b![0], meas.b![1], 'point B') },
            { label: 'Ask the copilot', onClick: () => ask(`How hard is the walk from (${meas.a[0].toFixed(0)}, ${meas.a[1].toFixed(0)}) to (${meas.b![0].toFixed(0)}, ${meas.b![1].toFixed(0)}) m? Check slope and time.`) },
            { label: 'Measure again', onClick: () => { setMeas(null); setCard(null) } },
          ] : []}>
          {!meas.b && <p className="icard__p">Click the end point B.</p>}
          {meas.b && (
            <>
              <dl className="icard__stats">
                <dt>Straight line</dt><dd>{fmtDist(straight)}</dd>
                <dt>Walking route</dt><dd>{meas.loading ? 'working…' : meas.route?.feasible ? `${fmtDist(meas.route.distance_m)} · ${fmtMinutes(meas.route.duration_min)} on foot` : meas.route ? 'no safe route' : 'unavailable'}</dd>
                <dt>Steepest</dt><dd>{meas.loading ? '…' : meas.route?.feasible ? `${meas.route.max_slope_deg.toFixed(1)}° (${slopeWords(meas.route.max_slope_deg)})` : meas.profile ? `${meas.profile.max_slope_deg.toFixed(1)}° on the straight line` : 'n/a'}</dd>
                {meas.route?.feasible && <><dt>Climb</dt><dd>{Math.round(meas.route.ascent_m)} m up</dd></>}
              </dl>
              {samples ? (<><h4 className="icard__h">Elevation along the line</h4><Sparkline samples={samples} /></>) : meas.loading ? <p className="icard__p">Loading the elevation profile…</p> : null}
              {meas.route && !meas.route.feasible && <p className="icard__warn">{meas.route.reason ?? 'The walking planner found no route.'}</p>}
              {meas.err && <p className="icard__warn">{meas.err}</p>}
              <p className="icard__note">Walking pace and slope limits are assumed values. The dashed cyan line is the planned walking route.</p>
            </>
          )}
        </Card>
      )
    }
  }

  return (
    <div ref={host} className={`interact${measureOn ? ' interact--measure' : ''}`}>
      {measureOn && !card && <div className="interact__hint" role="status">{meas && !meas.b ? 'Click the end point B' : 'Click the start point A'} · Esc to cancel</div>}
      {tip && !card && <div className="interact__tip" style={{ transform: `translate(${tip.x + 14}px, ${tip.y + 14}px)` }} role="tooltip"><strong>{tip.title}</strong><span>{tip.sub}</span></div>}
      {content}
    </div>
  )
}

function eastLon(lon: number): number {
  let w = lon
  while (w > 180) w -= 360
  while (w < -180) w += 360
  return w
}

function flyIn(v: Cesium.Viewer, lat: number, lon: number, h: number) {
  v.camera.flyTo({ destination: Cesium.Cartesian3.fromDegrees(eastLon(lon), lat, h, SPHERE), orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 }, duration: 2.5 })
}

function routeSamples(r: WalkRoute): { d: number; z: number; slope: number }[] {
  let d = 0
  return r.points.map((q, i) => { if (i) d += Math.hypot(q.x - r.points[i - 1].x, q.y - r.points[i - 1].y); return { d, z: q.z, slope: 0 } })
}

// ---- presentational pieces -----------------------------------------------------------------------------------------

interface Action { label: string; onClick: () => void; primary?: boolean; busy?: boolean }

function Card({ title, sub, badge, onClose, actions, children, cardRef }: { title: string; sub?: string; badge?: string; onClose: () => void; actions: Action[]; children: React.ReactNode; cardRef: (el: HTMLElement | null) => void }) {
  return (
    <section className="icard" ref={cardRef} tabIndex={-1} role="dialog" aria-label={title} onPointerDown={(e) => e.stopPropagation()}>
      <header className="icard__head">
        <div>
          <h3 className="icard__title">{title}</h3>
          {sub && <div className="icard__sub">{sub}</div>}
        </div>
        {badge && <span className="icard__badge">{badge}</span>}
        <button type="button" className="icard__x" onClick={onClose} aria-label="Close (Esc)" title="Close (Esc)">&times;</button>
      </header>
      <div className="icard__body">{children}</div>
      {actions.length > 0 && (
        <footer className="icard__actions">
          {actions.map((a) => (
            <button key={a.label} type="button" className={`icard__btn${a.primary ? ' icard__btn--primary' : ''}`} disabled={a.busy} onClick={a.onClick}>{a.busy ? '…' : a.label}</button>
          ))}
        </footer>
      )}
    </section>
  )
}

function Facts({ facts }: { facts: { state: 'loading' | 'ok' | 'err'; loc?: DescribeLoc; err?: string } }) {
  if (facts.state === 'loading') return <div className="icard__skeleton" aria-busy="true">Loading terrain data…</div>
  if (facts.state === 'err') return <p className="icard__warn">{facts.err ?? 'No data here.'} </p>
  const loc = facts.loc
  if (!loc) return null
  const v = loc.values ?? {}
  const el = v.elevation?.value, sl = v.slope_2m?.value
  const minerals = mineralLines(loc)
  const hits = minerals.filter((m) => m.tier !== 'none' && m.tier !== 'nodata')
  const none = minerals.filter((m) => m.tier === 'none')
  const gt = loc.nearest_ground_truth
  return (
    <>
      <dl className="icard__stats">
        <dt>Elevation</dt><dd>{typeof el === 'number' ? `${Math.round(el).toLocaleString()} m` : 'n/a'}</dd>
        <dt>Slope</dt><dd>{typeof sl === 'number' ? `${sl.toFixed(1)}° (${slopeWords(sl)})` : 'n/a'}</dd>
        <dt>Geologic unit</dt><dd>{v.geologic_unit?.class ?? 'n/a'}</dd>
        <dt>Data quality</dt><dd>{qualityWords(loc)}</dd>
      </dl>
      <h4 className="icard__h">Minerals</h4>
      {noMineralData(minerals) ? <p className="icard__p">No orbital data here.</p> : (
        <ul className="icard__min">
          {hits.map((m) => <li key={m.layer} className={`tier tier--${m.tier}`}><span className="tier__dot" aria-hidden="true" /><span><b>{m.name}:</b> {m.text}</span></li>)}
          {hits.length === 0 && <li className="tier tier--none"><span className="tier__dot" aria-hidden="true" /><span>No mineral detected here from orbit.</span></li>}
        </ul>
      )}
      {none.length > 0 && hits.length > 0 && <p className="icard__note">Not detected: {none.map((m) => m.name).join(', ')}.</p>}
      {gt?.sample?.name && typeof gt.distance_m === 'number' && gt.distance_m > 20 && (
        <p className="icard__note">Nearest rover-confirmed {gt.layer ?? 'mineral'}: {gt.sample.name} ({fmtDist(gt.distance_m)} away).</p>
      )}
    </>
  )
}

function Sparkline({ samples }: { samples: { d: number; z: number; slope: number }[] }) {
  const [hover, setHover] = useState<number | null>(null)
  if (samples.length < 2) return null
  const W = 280, H = 64, P = 4
  const zs = samples.map((s) => s.z), zmin = Math.min(...zs), zmax = Math.max(...zs)
  const dmax = samples[samples.length - 1].d || 1
  const span = Math.max(zmax - zmin, 2)
  const X = (d: number) => P + (d / dmax) * (W - 2 * P)
  const Y = (z: number) => H - P - ((z - zmin) / span) * (H - 2 * P)
  const line = samples.map((s, i) => `${i ? 'L' : 'M'}${X(s.d).toFixed(1)},${Y(s.z).toFixed(1)}`).join('')
  const hs = hover != null ? samples[hover] : null
  return (
    <div className="spark">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={`Elevation from ${Math.round(zmin)} to ${Math.round(zmax)} metres over ${Math.round(dmax)} metres`}
        onPointerMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect()
          const d = ((e.clientX - r.left) / r.width * W - P) / (W - 2 * P) * dmax
          let best = 0
          samples.forEach((s, i) => { if (Math.abs(s.d - d) < Math.abs(samples[best].d - d)) best = i })
          setHover(best)
        }} onPointerLeave={() => setHover(null)}>
        <path d={`${line}L${X(dmax)},${H - P}L${P},${H - P}Z`} className="spark__area" />
        <path d={line} className="spark__line" />
        {hs && <><line x1={X(hs.d)} x2={X(hs.d)} y1={P} y2={H - P} className="spark__cur" /><circle cx={X(hs.d)} cy={Y(hs.z)} r="3.5" className="spark__dot" /></>}
      </svg>
      <div className="spark__ax">
        {hs ? <span>{Math.round(hs.d)} m along · {Math.round(hs.z)} m{hs.slope ? ` · slope ${hs.slope.toFixed(1)}°` : ''}</span>
          : <><span>{Math.round(zmax).toLocaleString()} m</span><span>{Math.round(dmax)} m</span></>}
      </div>
    </div>
  )
}
