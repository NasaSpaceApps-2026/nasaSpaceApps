// Explore: a compact left drawer with six tools (ancient lake, sun and shadows, science layers, EVA reach, viewshed,
// rover history) and a guided tour. Overlays are PNGs from the query engine (OverlayManager); everything else is drawn
// with Cesium entities. Key E toggles the drawer, Esc leaves the active tool. window.atlasOverlay lets the copilot drive it.
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type MutableRefObject } from 'react'
import * as Cesium from 'cesium'
import type { Site } from '../site'
import { ENGINE, fetchPois } from '../interact/api'
import { ActiveOverlays } from './ActiveOverlays'
import { OverlayManager, type OverlayState } from './OverlayManager'
import { FALLBACK_LEVELS, clamp, debounce, fmtMetres, isKind, kindDefault, lakeLevelsOf, ltstLabel, overlayBase, solAt, statLine, type NamedLevel,
  type OverlayKind, type OverlayParams, type Slot } from './overlayApi'
import { parseTraverse, progressAt, samplesAt, samplesFromPois, traverseStats, type SampleSite, type TravSeg } from './historyData'
import { HistoryLayer, flyToXY, pinImage } from './scene'
import { TOUR_STOPS } from './tour'
import './explore.css'

export type ExploreTool = 'lake' | 'sun' | 'layers' | 'reach' | 'view' | 'history'

interface Props {
  viewer: Cesium.Viewer | null
  site: Site | null
  mode: 'mars' | 'jezero' | 'drive'
  open: boolean
  setOpen: (on: boolean) => void
  measureOn: boolean
  setMeasureOn: (on: boolean) => void
  /** Interactions calls this with site metres instead of opening the info card while a point is being picked. */
  pickRef: MutableRefObject<((xy: [number, number]) => void) | null>
  traverseOn: boolean
  setTraverseVisible: (on: boolean) => void
  enterSite: () => void
}

declare global {
  interface Window {
    atlasOverlay?: AtlasOverlayApi
  }
}
export interface AtlasOverlayApi {
  show: (kind: OverlayKind, params?: OverlayParams) => Promise<{ ok: boolean; error?: string }>
  clear: (kind?: OverlayKind) => void
  setOpacity: (kind: OverlayKind, opacity: number) => void
  state: () => OverlayState | null
  base: () => string | null
  kinds: () => OverlayKind[]
}

const TOOLS: { id: ExploreTool; label: string; help: string }[] = [
  { id: 'lake', label: 'Ancient lake', help: 'Flood today’s terrain to a published lake level.' },
  { id: 'sun', label: 'Sun & shadows', help: 'Where the Sun is, and what it shades, across one sol.' },
  { id: 'layers', label: 'Science layers', help: 'Minerals, geology and slope seen from orbit.' },
  { id: 'reach', label: 'Reach (EVA)', help: 'How far a spacewalk can go and still get back.' },
  { id: 'view', label: 'Viewshed', help: 'What can be seen from a point.' },
  { id: 'history', label: 'Rover history', help: 'Watch Perseverance’s traverse grow, sol by sol.' },
]

const CHIPS: { id: string; label: string; kind: OverlayKind; params: OverlayParams }[] = [
  { id: 'olivine', label: 'Olivine', kind: 'evidence', params: { layer: 'olivine' } },
  { id: 'carbonate', label: 'Carbonate', kind: 'evidence', params: { layer: 'carbonate' } },
  { id: 'clay', label: 'Clay', kind: 'evidence', params: { layer: 'fe_mg_smectite' } },
  { id: 'geology', label: 'Geology', kind: 'geology', params: {} },
  { id: 'slope', label: 'Slope hazard', kind: 'slope', params: {} },
]

const LANDING: [number, number] = [1844, -433]
const sleep = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms))
const isTyping = (t: EventTarget | null) => { const el = t as HTMLElement | null; return !!el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)) }

const EMPTY_STATE: OverlayState = {
  main: { kind: null, params: {}, meta: null, loading: false, error: null, opacity: 0.7 },
  science: { kind: null, params: {}, meta: null, loading: false, error: null, opacity: 0.65 },
}
const noopSub = () => () => undefined

function useOverlayState(mgr: OverlayManager | null): OverlayState {
  const sub = useCallback((cb: () => void) => (mgr ? mgr.subscribe(cb) : noopSub()), [mgr])
  return useSyncExternalStore(sub, () => mgr?.state ?? EMPTY_STATE)
}

export function ExplorePanel(p: Props) {
  const { viewer, site, mode, open, setOpen, measureOn, setMeasureOn, pickRef, traverseOn, setTraverseVisible, enterSite } = p
  const props = useRef(p)
  useEffect(() => { props.current = p })
  const [mgr, setMgr] = useState<OverlayManager | null>(null)
  const mgrBase = useRef('')
  const ov = useOverlayState(mgr)
  const [tool, setTool] = useState<ExploreTool | null>(null)
  const [levels, setLevels] = useState<NamedLevel[]>(FALLBACK_LEVELS)
  const [tourIdx, setTourIdx] = useState<number | null>(null)

  // tool parameters (kept when switching tools)
  const [level, setLevel] = useState(-2410)
  const [ltst, setLtst] = useState(12)
  const [todaySol] = useState(() => solAt(new Date()))
  const [sol, setSol] = useState(todaySol)
  const todaySolRef = useRef(todaySol)
  const [playSun, setPlaySun] = useState(false)
  const [start, setStart] = useState<[number, number] | null>(null)
  const [budgetH, setBudgetH] = useState(4)
  const [reserve, setReserve] = useState(0.2)
  const [vpt, setVpt] = useState<[number, number] | null>(null)
  const [eyeH, setEyeH] = useState(1.7)
  const [pick, setPick] = useState<'reach' | 'view' | null>(null)
  const [histSol, setHistSol] = useState(0)
  const [playHist, setPlayHist] = useState(false)
  const extra = useRef<Partial<Record<OverlayKind, OverlayParams>>>({})

  // ---- overlay manager + window API ------------------------------------------------------------------------------
  useEffect(() => {
    if (!viewer || !site) return
    const base = overlayBase(location.search, import.meta.env.VITE_OVERLAY_URL as string | undefined, ENGINE)
    const m = new OverlayManager(viewer, site, base)
    mgrBase.current = base
    // oxlint-disable-next-line react/set-state-in-effect
    setMgr(m)
    const ctl = new AbortController()
    void fetch(`${base}/overlay/kinds`, { signal: ctl.signal }).then((r) => (r.ok ? r.json() : null)).then((j) => {
      const lv = lakeLevelsOf(j)
      if (lv.length) setLevels(lv)
      const sd = kindDefault(j, 'sun', 'sol')
      if (sd !== undefined) setSol((cur) => (cur === todaySolRef.current ? sd : cur)) // backend's current mission sol
    }).catch(() => undefined)
    return () => { ctl.abort(); m.dispose(); setMgr(null) }
  }, [viewer, site])

  const show = useCallback((kind: OverlayKind, params: OverlayParams = {}) => mgr?.show(kind, { ...extra.current[kind], ...params }), [mgr])

  useEffect(() => {
    if (!mgr) return
    const api: AtlasOverlayApi = {
      async show(kind, params = {}) {
        if (!isKind(kind)) return { ok: false, error: `Unknown overlay "${String(kind)}". Kinds: evidence, geology, slope, lake, sun, viewshed, envelope.` }
        const pr = props.current
        if (pr.mode !== 'jezero') pr.enterSite()
        pr.setOpen(true)
        setTourIdx(null)
        const num = (v: unknown) => (v === undefined || v === null || v === '' ? undefined : Number(v))
        const { level: lv, level_m, ltst: lt, sol: so, x, y, h, budget_min, reserve: rs, ...rest } = params as Record<string, unknown>
        if (kind === 'lake') {
          setTool('lake')
          const v = num(lv ?? level_m)
          if (v !== undefined && Number.isFinite(v)) setLevel(v)
        } else if (kind === 'sun') {
          setTool('sun'); setPlaySun(false)
          if (num(lt) !== undefined) setLtst(clamp(num(lt)!, 5, 19))
          if (num(so) !== undefined) setSol(num(so)!)
        } else if (kind === 'viewshed') {
          setTool('view')
          if (num(x) !== undefined && num(y) !== undefined) setVpt([num(x)!, num(y)!])
          if (num(h) !== undefined) setEyeH(num(h)!)
        } else if (kind === 'envelope') {
          setTool('reach')
          if (num(x) !== undefined && num(y) !== undefined) setStart([num(x)!, num(y)!])
          if (num(budget_min) !== undefined) setBudgetH(clamp(num(budget_min)! / 60, 1, 8))
          if (num(rs) !== undefined) setReserve(clamp(num(rs)!, 0, 0.6))
        } else {
          setTool((t) => t ?? 'layers')
          void mgr.show(kind, params)
          return { ok: true }
        }
        extra.current[kind] = rest as OverlayParams
        return { ok: true }
      },
      clear(kind) { if (kind && isKind(kind)) mgr.clear(kind); else mgr.clear('all') },
      setOpacity(kind, a) { mgr.setOpacity(kind === 'evidence' || kind === 'geology' || kind === 'slope' ? 'science' : 'main', a) },
      state: () => mgr.state,
      base: () => mgr.base,
      kinds: () => ['evidence', 'geology', 'slope', 'lake', 'sun', 'viewshed', 'envelope'],
    }
    window.atlasOverlay = api
    return () => { if (window.atlasOverlay === api) delete window.atlasOverlay }
  }, [mgr])

  // ---- leaving Jezero: drop overlays, tools and the tour --------------------------------------------------------------
  useEffect(() => {
    if (mode === 'jezero') return
    mgr?.clear('all')
    // oxlint-disable-next-line react/set-state-in-effect
    setTool(null); setPick(null); setTourIdx(null); setPlaySun(false); setPlayHist(false); setOpen(false)
  }, [mode, mgr, setOpen])

  // ---- pins (reach start, viewpoint, tour stop) -------------------------------------------------------------------------
  const pins = useRef<Cesium.CustomDataSource | null>(null)
  useEffect(() => {
    if (!viewer) return
    const ds = new Cesium.CustomDataSource('atlas-explore')
    void viewer.dataSources.add(ds)
    pins.current = ds
    return () => { if (!viewer.isDestroyed()) viewer.dataSources.remove(ds, true); pins.current = null }
  }, [viewer])
  const setPin = useCallback((id: string, xy: [number, number] | null, letter: string, fill: string, size = 28) => {
    const ds = pins.current
    if (!ds || !site) return
    ds.entities.removeById(id)
    if (!xy) return
    ds.entities.add({ id, position: site.position(xy[0], xy[1], 3), billboard: { image: pinImage(letter, fill), width: size, height: size, disableDepthTestDistance: Number.POSITIVE_INFINITY } })
  }, [site])

  // ---- tool switch (or unmount): remove the previous tool's main overlay, pick mode, pins and timers
  useEffect(() => () => {
    mgr?.clear('main'); setPick(null); setPlaySun(false); setPlayHist(false)
    pins.current?.entities.removeById('reach-start'); pins.current?.entities.removeById('view-pt')
  }, [tool, mgr])

  // ---- debounced overlay requests (slider: fetch at input end, keep the last image meanwhile) ------------------------------
  /* oxlint-disable-next-line react/refs */
  const deb = useMemo(() => {
    if (!mgr) return null
    return {
      lake: debounce((lv: number) => { void show('lake', { level: lv }) }, 220),
      sun: debounce((h: number, s: number) => { void show('sun', { ltst: h, sol: s }) }, 220),
      reach: debounce((xy: [number, number], b: number, r: number) => { void show('envelope', { x: Math.round(xy[0]), y: Math.round(xy[1]), budget_min: Math.round(b * 60), reserve: r }) }, 260),
      view: debounce((xy: [number, number], h: number) => { void show('viewshed', { x: Math.round(xy[0]), y: Math.round(xy[1]), h }) }, 260),
    }
  }, [mgr, show])
  useEffect(() => () => { if (deb) Object.values(deb).forEach((d) => d.cancel()) }, [deb])

  const justSwitched = useRef(false)
  useEffect(() => { justSwitched.current = true }, [tool])
  useEffect(() => {
    if (!deb) return
    const go = () => { if (justSwitched.current) { justSwitched.current = false; return true } return false }
    if (tool === 'lake') { deb.lake(level); if (go()) deb.lake.flush() }
    else if (tool === 'sun' && !playSun) { deb.sun(ltst, sol); if (go()) deb.sun.flush() }
    else if (tool === 'reach' && start) { deb.reach(start, budgetH, reserve); if (go()) deb.reach.flush() }
    else if (tool === 'view' && vpt) { deb.view(vpt, eyeH); if (go()) deb.view.flush() }
    if (tool === 'reach') setPin('reach-start', start, 'S', '#ffffff')
    if (tool === 'view') setPin('view-pt', vpt, 'V', '#35e0ff')
  }, [deb, tool, level, ltst, sol, playSun, start, budgetH, reserve, vpt, eyeH, setPin])
  const flushAll = () => { if (deb) Object.values(deb).forEach((d) => d.flush()) }

  // ---- sun animation: step through the sol, waiting for each frame -------------------------------------------------------------
  const ltstRef = useRef(ltst)
  const solRef = useRef(sol)
  useEffect(() => { ltstRef.current = ltst; solRef.current = sol })
  useEffect(() => {
    if (!playSun || !mgr) return
    let alive = true
    void (async () => {
      let h = ltstRef.current >= 18.75 ? 5 : ltstRef.current
      while (alive) {
        setLtst(h)
        await show('sun', { ltst: h, sol: solRef.current })
        if (!alive) return
        if (mgr.state.main.error) { setPlaySun(false); return }
        const next = h + 0.5 > 19 ? 5 : h + 0.5
        mgr.prefetch('sun', { ...extra.current.sun, ltst: next, sol: solRef.current })
        await sleep(450)
        h = next
      }
    })()
    return () => { alive = false }
  }, [playSun, mgr, show])

  // ---- picking a point on the map ----------------------------------------------------------------------------------------------
  useEffect(() => {
    if (!pick) { pickRef.current = null; return }
    pickRef.current = (xy) => {
      if (pick === 'reach') setStart(xy); else setVpt(xy)
      setPick(null)
    }
    return () => { pickRef.current = null }
  }, [pick, pickRef])
  // oxlint-disable-next-line react/set-state-in-effect
  useEffect(() => { if (measureOn && pick) setPick(null) }, [measureOn, pick])
  const startPick = (k: 'reach' | 'view') => { if (pick === k) { setPick(null); return } setMeasureOn(false); setPick(k) }

  // ---- rover history ---------------------------------------------------------------------------------------------------------------
  const histData = useRef<{ segs: TravSeg[]; samples: SampleSite[] } | null>(null)
  const [hdata, setHdata] = useState<{ segs: TravSeg[]; samples: SampleSite[] } | null>(null)
  const [histReady, setHistReady] = useState<'idle' | 'loading' | 'ready' | 'err'>('idle')
  const hist = useRef<HistoryLayer | null>(null)
  const [range, setRange] = useState({ min: 0, max: 0, total: 0 })
  useEffect(() => {
    if (tool !== 'history' || !viewer || !site) return
    let dead = false
    setTraverseVisible(false)
    void (async () => {
      try {
        if (!histData.current) {
          setHistReady('loading')
          const [tj, pois] = await Promise.all([fetch(`${mgrBase.current}/traverse.geojson`).then((r) => { if (!r.ok) throw new Error('traverse'); return r.json() }), fetchPois()])
          histData.current = { segs: parseTraverse(tj), samples: samplesFromPois(pois as never) }
        }
        if (dead) return
        const { segs, samples } = histData.current
        setHdata(histData.current)
        if (!segs.length) throw new Error('no traverse')
        const st = traverseStats(segs)
        setRange({ min: st.minSol, max: st.maxSol, total: st.totalM })
        hist.current = new HistoryLayer(viewer, site, segs, samples)
        hist.current.setSol(st.maxSol)
        setHistSol(st.maxSol)
        setHistReady('ready')
        flyToXY(viewer, site, [-250, -450], 9500, -45, 2)
      } catch { if (!dead) setHistReady('err') }
    })()
    return () => { dead = true; hist.current?.dispose(); hist.current = null; setHistReady('idle'); setTraverseVisible(props.current.traverseOn) }
  }, [tool, viewer, site, setTraverseVisible])
  useEffect(() => { if (histReady === 'ready') hist.current?.setSol(histSol) }, [histSol, histReady])
  useEffect(() => { if (!tool) setTraverseVisible(traverseOn) }, [traverseOn, tool, setTraverseVisible])
  useEffect(() => {
    if (!playHist || histReady !== 'ready') return
    const step = Math.max(1, Math.round((range.max - range.min) / 110))
    let cur = histSol >= range.max ? range.min : histSol
    const t = window.setInterval(() => {
      cur = Math.min(range.max, cur + step)
      setHistSol(cur)
      if (cur >= range.max) { window.clearInterval(t); setPlayHist(false) }
    }, 70)
    return () => window.clearInterval(t)
    // histSol is read once at start; the interval owns the value afterwards
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [playHist, histReady, range])

  // ---- guided tour -------------------------------------------------------------------------------------------------------------------
  const startTour = () => {
    if (!mgr) return
    if (mode !== 'jezero') enterSite()
    setTool(null); setPick(null); setMeasureOn(false)
    mgr.clear('all')
    setTourIdx(0)
  }
  const exitTour = useCallback(() => setTourIdx(null), [])
  useEffect(() => {
    if (tourIdx === null || !viewer || !site || !mgr) return
    const stop = TOUR_STOPS[tourIdx]
    flyToXY(viewer, site, stop.at, stop.range_m, stop.pitch_deg, tourIdx === 0 ? 2.8 : 3.2)
    setPin('tour-stop', tourIdx === TOUR_STOPS.length - 1 ? null : stop.at, String(tourIdx + 1), '#e0794a', 34)
    if (!stop.overlay) mgr.clear('all')
    else { mgr.clear(stop.overlay.kind === 'lake' ? 'science' : 'main'); void mgr.show(stop.overlay.kind, stop.overlay.params ?? {}) }
  }, [tourIdx, viewer, site, mgr, setPin])
  const tourWasOn = useRef(false)
  useEffect(() => {
    if (tourIdx !== null) { tourWasOn.current = true; return }
    if (!tourWasOn.current) return
    tourWasOn.current = false
    mgr?.clear('all')
    pins.current?.entities.removeById('tour-stop')
  }, [tourIdx, mgr])

  const tourRef = useRef<number | null>(null)
  const toolRef = useRef<ExploreTool | null>(null)
  const playRef = useRef(false)
  useEffect(() => { toolRef.current = tool; playRef.current = playSun || playHist })
  // ---- keys ------------------------------------------------------------------------------------------------------------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const pr = props.current
      if (e.key === 'Escape') {
        if (tourRef.current !== null) { exitTour(); return }
        if (pickRef.current) { setPick(null); return }
        if (playRef.current) { setPlaySun(false); setPlayHist(false); return }
        if (toolRef.current) { setTool(null); return }
        if (pr.open) pr.setOpen(false)
        return
      }
      if (isTyping(e.target) || pr.mode !== 'jezero') return
      if (tourRef.current !== null) {
        if (e.key === 'ArrowRight') setTourIdx((i) => (i === null ? i : Math.min(TOUR_STOPS.length - 1, i + 1)))
        else if (e.key === 'ArrowLeft') setTourIdx((i) => (i === null ? i : Math.max(0, i - 1)))
        return
      }
      if (e.code === 'KeyE') pr.setOpen(!pr.open)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [exitTour, pickRef])
  useEffect(() => { tourRef.current = tourIdx })

  // ---- derived ----------------------------------------------------------------------------------------------------------------------------------
  const relief = useMemo(() => (site ? { min: Math.floor(site.relief.min), max: Math.ceil(site.relief.max) } : { min: -2640, max: -2290 }), [site])
  const mainStat = ov.main.kind ? statLine(ov.main.kind, ov.main.meta?.stats, { level: ov.main.kind === 'lake' ? Number(ov.main.params.level) : undefined, reliefMin: relief.min }) : ''
  const [hoverTick, setHoverTick] = useState<NamedLevel | null>(null)

  if (mode !== 'jezero' || !viewer || !site) return null
  const onClear = (s: Slot) => mgr?.clear(s)
  const onOpacity = (s: Slot, a: number) => mgr?.setOpacity(s, a)
  const hs = hdata ? { prog: progressAt(hdata.segs, histSol), samp: samplesAt(hdata.samples, histSol) } : null
  const pct = (lv: number) => (clamp(lv, relief.min, relief.max) - relief.min) / (relief.max - relief.min)
  const unreachable = ov.main.error && !ov.main.meta
  const pickHint = pick === 'reach' ? 'Click a start point on the map' : pick === 'view' ? 'Click the point to look from' : ''

  return (
    <>
      {pick && <div className="exhint" role="status"><span className="exhint__dot" aria-hidden="true" />{pickHint} · Esc to cancel</div>}

      {open && tourIdx === null && (
        <aside className="explore" aria-label="Explore tools">
          <header className="explore__head">
            <h2>Explore</h2>
            <button type="button" className="explore__x" onClick={() => setOpen(false)} aria-label="Close Explore (E)" title="Close (E)">&times;</button>
          </header>
          <button type="button" className="explore__tour" onClick={startTour} disabled={!mgr}>
            <span aria-hidden="true">&#9654;</span> Guided tour <small>6 stops</small>
          </button>
          <div className="explore__tabs" role="group" aria-label="Tools">
            {TOOLS.map((t) => (
              <button key={t.id} type="button" className="explore__tab" aria-pressed={tool === t.id} title={t.help}
                onClick={() => setTool(tool === t.id ? null : t.id)}>{t.label}</button>
            ))}
          </div>

          <div className="explore__body">
            {tool === null && (
              <ul className="explore__intro">
                {TOOLS.map((t) => <li key={t.id}><button type="button" onClick={() => setTool(t.id)}><strong>{t.label}</strong><span>{t.help}</span></button></li>)}
              </ul>
            )}

            {tool === 'lake' && (
              <section>
                <p className="explore__help">Flood today&rsquo;s terrain to a lake level. Hover a tick for the paper behind it.</p>
                <div className="exlake">
                  <div className="exslider">
                    <input type="range" min={relief.min} max={relief.max} step={1} value={level} aria-label="Lake level in metres"
                      onChange={(e) => setLevel(Number(e.target.value))} onPointerUp={flushAll} onKeyUp={flushAll} />
                    {levels.filter((l) => l.level >= relief.min && l.level <= relief.max).map((l) => (
                      <button key={l.id} type="button" className={`exslider__tick${hoverTick?.id === l.id ? ' is-on' : ''}`} style={{ left: `calc(8px + (100% - 16px) * ${pct(l.level)})` }}
                        aria-label={`${fmtMetres(l.level)}: ${l.label}`} onMouseEnter={() => setHoverTick(l)} onMouseLeave={() => setHoverTick(null)}
                        onFocus={() => setHoverTick(l)} onBlur={() => setHoverTick(null)} onClick={() => setLevel(l.level)} />
                    ))}
                  </div>
                  <div className="exslider__ends"><span>{fmtMetres(relief.min)}</span><b>{fmtMetres(level)}</b><span>{fmtMetres(relief.max)}</span></div>
                  <div className="exlake__tip" role="status">
                    {hoverTick ? (<><strong>{fmtMetres(hoverTick.level)}</strong> {hoverTick.label}{hoverTick.kind ? ` (${hoverTick.kind})` : ''}<br /><small>{hoverTick.citation}</small></>) : <small>Ticks mark published levels (docs/lake-levels.md).</small>}
                  </div>
                  <div className="exchips">
                    {[-2410, -2470, -2500].map((lv) => <button key={lv} type="button" className="exchip" aria-pressed={level === lv} onClick={() => { setLevel(lv); window.setTimeout(flushAll, 0) }}>{fmtMetres(lv)}</button>)}
                  </div>
                </div>
              </section>
            )}

            {tool === 'sun' && (
              <section>
                <p className="explore__help">Slide through the sol, or press play. Shadows come from the terrain model.</p>
                <div className="exrow">
                  <button type="button" className="explay" aria-pressed={playSun} onClick={() => setPlaySun(!playSun)} aria-label={playSun ? 'Pause' : 'Play the sol'}>{playSun ? '❚❚' : '▶'}</button>
                  <input type="range" min={5} max={19} step={0.25} value={ltst} aria-label="Local true solar time"
                    onChange={(e) => { setPlaySun(false); setLtst(Number(e.target.value)) }} onPointerUp={flushAll} onKeyUp={flushAll} />
                  <b className="exrow__val">{ltstLabel(ltst)}</b>
                </div>
                <div className="exslider__ends"><span>05:00</span><span>LTST</span><span>19:00</span></div>
                <label className="exfield">Sol
                  <input type="number" value={sol} min={0} max={9999} onChange={(e) => { const v = Math.round(Number(e.target.value)); if (Number.isFinite(v)) setSol(clamp(v, 0, 9999)) }} aria-label="Mission sol" />
                  <small>today is sol {todaySol}</small>
                </label>
              </section>
            )}

            {tool === 'layers' && (
              <section>
                <p className="explore__help">Pick one layer. It sits under the other tools. Orbit sees minerals at 36 m, so patchy is normal.</p>
                <div className="exchips">
                  {CHIPS.map((c) => {
                    const on = ov.science.kind === c.kind && (c.kind !== 'evidence' || ov.science.params.layer === c.params.layer)
                    return <button key={c.id} type="button" className="exchip" aria-pressed={on} onClick={() => (on ? mgr?.clear('science') : void show(c.kind, c.params))}>{c.label}</button>
                  })}
                </div>
              </section>
            )}

            {tool === 'reach' && (
              <section>
                <p className="explore__help">Where can a crew walk and still get home inside the budget, with the reserve held back?</p>
                <div className="exrow exrow--btns">
                  <button type="button" className="exbtn" aria-pressed={pick === 'reach'} onClick={() => startPick('reach')}>{pick === 'reach' ? 'Click the map…' : start ? 'Move start point' : 'Click a start point'}</button>
                  <button type="button" className="exbtn" onClick={() => setStart(LANDING)}>Use landing site</button>
                </div>
                <label className="exfield">Budget
                  <input type="range" min={1} max={8} step={0.5} value={budgetH} onChange={(e) => setBudgetH(Number(e.target.value))} onPointerUp={flushAll} onKeyUp={flushAll} aria-label="EVA budget in hours" />
                  <b>{budgetH} h</b>
                </label>
                <label className="exfield">Reserve
                  <select value={reserve} onChange={(e) => setReserve(Number(e.target.value))} aria-label="Reserve">
                    {[0.1, 0.2, 0.3].map((r) => <option key={r} value={r}>{Math.round(r * 100)}%{r === 0.2 ? ' (default)' : ''}</option>)}
                  </select>
                </label>
                {!start && <p className="explore__note">No start point yet.</p>}
              </section>
            )}

            {tool === 'view' && (
              <section>
                <p className="explore__help">Which ground can be seen from a point, for a given eye height.</p>
                <div className="exrow exrow--btns">
                  <button type="button" className="exbtn" aria-pressed={pick === 'view'} onClick={() => startPick('view')}>{pick === 'view' ? 'Click the map…' : vpt ? 'Move viewpoint' : 'Click a point'}</button>
                  <button type="button" className="exbtn" onClick={() => setVpt(LANDING)}>Use landing site</button>
                </div>
                <div className="exchips" role="group" aria-label="Eye height">
                  <button type="button" className="exchip" aria-pressed={eyeH === 1.7} onClick={() => setEyeH(1.7)}>1.7 m astronaut</button>
                  <button type="button" className="exchip" aria-pressed={eyeH === 2} onClick={() => setEyeH(2)}>2 m rover mast</button>
                </div>
                {!vpt && <p className="explore__note">No viewpoint yet.</p>}
              </section>
            )}

            {tool === 'history' && (
              <section>
                <p className="explore__help">Perseverance&rsquo;s drive inside this 8 km map, with each sample site appearing on its sol.</p>
                {histReady === 'loading' && <p className="explore__note"><span className="spinner" aria-hidden="true" /> Loading the traverse&hellip;</p>}
                {histReady === 'err' && <p className="exov__err" role="alert">Could not load the traverse data.</p>}
                {histReady === 'ready' && hs && (
                  <>
                    <div className="exrow">
                      <button type="button" className="explay" aria-pressed={playHist} onClick={() => setPlayHist(!playHist)} aria-label={playHist ? 'Pause' : 'Play the traverse'}>{playHist ? '❚❚' : '▶'}</button>
                      <input type="range" min={range.min} max={range.max} step={1} value={histSol} aria-label="Mission sol"
                        onChange={(e) => { setPlayHist(false); setHistSol(Number(e.target.value)) }} />
                      <b className="exrow__val">sol {histSol}</b>
                    </div>
                    <p className="exstat">{(hs.prog.driven_m / 1000).toFixed(1)} km driven in this map · {hs.samp.shown.length} of {hdata?.samples.length} samples sealed</p>
                    {hs.samp.latest && <p className="exstat exstat--sub">Latest: {hs.samp.latest.name}, sol {hs.samp.latest.sol}{hs.samp.latest.rock ? `, ${hs.samp.latest.rock.toLowerCase()}` : ''}{hs.samp.latest.region ? `, ${hs.samp.latest.region}` : ''}</p>}
                  </>
                )}
              </section>
            )}

            {tool && tool !== 'layers' && tool !== 'history' && mainStat && <p className="exstat" role="status">{mainStat}</p>}
            {tool && tool !== 'layers' && tool !== 'history' && unreachable && <p className="exov__err" role="alert">{ov.main.error}</p>}
            {tool === 'layers' && ov.science.error && !ov.science.meta && <p className="exov__err" role="alert">{ov.science.error}</p>}
          </div>

          <ActiveOverlays state={ov} onOpacity={onOpacity} onClear={onClear} />
        </aside>
      )}

      {tourIdx !== null && (
        <div className="extour" role="dialog" aria-label="Guided tour">
          <div className="extour__top">
            <span className="extour__n">Stop {tourIdx + 1} of {TOUR_STOPS.length}</span>
            <div className="extour__dots" aria-hidden="true">{TOUR_STOPS.map((s, i) => <i key={s.id} className={i === tourIdx ? 'is-on' : i < tourIdx ? 'is-done' : ''} />)}</div>
            <button type="button" className="explore__x" onClick={exitTour} aria-label="Exit tour (Esc)" title="Exit (Esc)">&times;</button>
          </div>
          <h3>{TOUR_STOPS[tourIdx].title}</h3>
          <p className="extour__cap">{TOUR_STOPS[tourIdx].caption}</p>
          {TOUR_STOPS[tourIdx].overlay?.kind === 'lake' && mainStat && <p className="exstat">{mainStat}</p>}
          <p className="extour__cite">Source: {TOUR_STOPS[tourIdx].cite}</p>
          <ActiveOverlays state={ov} onOpacity={onOpacity} onClear={onClear} compact />
          <div className="extour__nav">
            <button type="button" className="exbtn" onClick={() => setTourIdx(Math.max(0, tourIdx - 1))} disabled={tourIdx === 0}>Prev</button>
            <button type="button" className="exbtn exbtn--primary" onClick={() => (tourIdx === TOUR_STOPS.length - 1 ? exitTour() : setTourIdx(tourIdx + 1))}>{tourIdx === TOUR_STOPS.length - 1 ? 'Finish' : 'Next'}</button>
            <button type="button" className="exbtn" onClick={exitTour}>Exit</button>
          </div>
        </div>
      )}
    </>
  )
}
