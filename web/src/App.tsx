import { useEffect, useRef, useState } from 'react'
import * as Cesium from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'
import { R, Site, SPHERE, deg } from './site'
import { siteImagery, siteTerrain } from './providers'
import { CesiumRover } from './cesiumRover'
import type { DriverPose } from './rover/driver'
import { PlacesLayer, type Place } from './places'
import { BUBBLES, type Bubble } from './bubbles'
import { dropOnBubbles, type StreetDrop } from './pegman'
import { AtlasDock, type DriveInfo, type DockApi } from './panels/chat/AtlasDock'
import { Interactions, type InteractionsHandle } from './interact/Interactions'
import { TopBar, type LayerRow, type Mode as UiMode } from './ui/TopBar'
import { StatusBar } from './ui/StatusBar'
import { NoWebGL } from './ui/NoWebGL'
import { ExplorePanel } from './explore/ExplorePanel'
import './App.css'

type SurfaceView = 'planet' | 'street'

const MARS_ION_ASSET = 3644333
type Mode = 'site' | 'ground' | 'rover' | 'planet'
let roverCtl: CesiumRover | null = null // set once the viewer is up; used by fly()
let placesCtl: PlacesLayer | null = null
let marsTileset: Cesium.Cesium3DTileset | null = null
let bubblePrimitives: Cesium.GroundPrimitive[] = []
let bubbleMarkers: Cesium.Entity[] = []
let siteImageryShown: boolean[] | null = null
const marsBasemapLayers = new Set<Cesium.ImageryLayer>()

const MARS_MOSAIC =
  'https://trek.nasa.gov/tiles/Mars/EQ/Mars_Viking_MDIM21_ClrMosaic_global_232m/1.0.0/default/default028mm/{z}/{y}/{x}.jpg'

function addMarsBasemap(v: Cesium.Viewer) {
  const layer = new Cesium.ImageryLayer(new Cesium.UrlTemplateImageryProvider({
    url: MARS_MOSAIC,
    tilingScheme: new Cesium.GeographicTilingScheme({
      ellipsoid: SPHERE,
      numberOfLevelZeroTilesX: 2,
      numberOfLevelZeroTilesY: 1,
    }),
    maximumLevel: 6,
    credit: 'NASA / USGS Viking MDIM 2.1 color mosaic, served by Mars Trek',
  }))
  v.imageryLayers.add(layer, 0)
  marsBasemapLayers.add(layer)
}

function eastLon(lon: number): number {
  let wrapped = lon
  while (wrapped > 180) wrapped -= 360
  while (wrapped < -180) wrapped += 360
  return wrapped
}

function lookAtBubble(v: Cesium.Viewer, lat: number, lon: number, height: number) {
  v.camera.setView({
    destination: Cesium.Cartesian3.fromDegrees(eastLon(lon), lat, height, SPHERE),
    orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 },
  })
}
let roverPoseHud: ((p: DriverPose) => void) | null = null

function ensureRover(v: Cesium.Viewer, s: Site): CesiumRover {
  if (roverCtl) return roverCtl
  roverCtl = new CesiumRover(v, s)
  if (roverPoseHud) roverCtl.onPose = roverPoseHud
  const w = placesCtl?.first()
  if (w) roverCtl.driver.teleport(w.x, w.y, w.heading ?? 0)
  ;(window as unknown as { __rover?: CesiumRover }).__rover = roverCtl
  return roverCtl
}

function releaseRover() {
  if (!roverCtl) return
  roverCtl.dispose()
  roverCtl = null
  delete (window as unknown as { __rover?: CesiumRover }).__rover
}

function webglAvailable(): boolean {
  try {
    const c = document.createElement('canvas')
    return !!(c.getContext('webgl2') || c.getContext('webgl'))
  } catch {
    return false
  }
}

function pickMarsLatLon(viewer: Cesium.Viewer, clientX: number, clientY: number): { latDeg: number; lonDeg: number } | null {
  const rect = viewer.canvas.getBoundingClientRect()
  const win = new Cesium.Cartesian2(clientX - rect.left, clientY - rect.top)
  let cartesian: Cesium.Cartesian3 | undefined = viewer.scene.pickPosition(win)
  if (!Cesium.defined(cartesian)) cartesian = viewer.camera.pickEllipsoid(win, SPHERE) ?? undefined
  if (!Cesium.defined(cartesian)) return null
  const carto = Cesium.Cartographic.fromCartesian(cartesian, SPHERE)
  return { latDeg: Cesium.Math.toDegrees(carto.latitude), lonDeg: Cesium.Math.toDegrees(carto.longitude) }
}

const PEG_SNAP_PX = 48

/** Bubble pin nearest to a screen point (client px), if within PEG_SNAP_PX and on the visible side of the globe.
 * The patches are ~2 km on a 3,400 km planet: at globe zoom they are sub-pixel, so the drop must snap to the pin. */
function nearestBubble(viewer: Cesium.Viewer, clientX: number, clientY: number): { bubble: Bubble; sx: number; sy: number } | null {
  const rect = viewer.canvas.getBoundingClientRect()
  const cam = viewer.camera.positionWC
  let best: { bubble: Bubble; sx: number; sy: number; d: number } | null = null
  for (const b of BUBBLES) {
    const pos = Cesium.Cartesian3.fromDegrees(eastLon(b.lon), b.lat, 0, SPHERE)
    const dot = Cesium.Cartesian3.dot(Cesium.Cartesian3.normalize(cam, new Cesium.Cartesian3()), Cesium.Cartesian3.normalize(pos, new Cesium.Cartesian3()))
    if (dot < R / Cesium.Cartesian3.magnitude(cam)) continue // behind the limb
    const w = Cesium.SceneTransforms.worldToWindowCoordinates(viewer.scene, pos)
    if (!w) continue
    const sx = rect.left + w.x, sy = rect.top + w.y
    const d = Math.hypot(sx - clientX, sy - clientY)
    if (d <= PEG_SNAP_PX && (!best || d < best.d)) best = { bubble: b, sx, sy, d }
  }
  return best
}

function resetPegman(el: HTMLDivElement | null) {
  if (!el) return
  el.style.left = ''
  el.style.top = ''
  el.style.right = ''
  el.style.bottom = ''
}

export default function App() {
  const box = useRef<HTMLDivElement>(null)
  const [site, setSite] = useState<Site | null>(null)
  const [viewer, setViewer] = useState<Cesium.Viewer | null>(null)
  const [layers, setLayers] = useState<Record<string, { on: boolean; layer: Cesium.ImageryLayer }>>({})
  const [toast, setToast] = useState('')
  const [cursor, setCursor] = useState('')
  const [drive, setDrive] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [driveInfo, setDriveInfo] = useState<DriveInfo | null>(null)
  const wasActive = useRef(false)
  const [glFail, setGlFail] = useState<string | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const creditsEl = useRef<HTMLDivElement>(null)
  const [vec, setVec] = useState({ traverse: true, names: true })
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<Place[]>([])
  const initialMode = ((new URLSearchParams(location.search).get('view') as Mode) || 'planet')
  const [cameraMode, setCameraMode] = useState<Mode>(initialMode)
  const [surfaceView, setSurfaceView] = useState<SurfaceView>('planet')
  const [streetDrop, setStreetDrop] = useState<StreetDrop | null>(null)
  const pegRef = useRef<HTMLDivElement>(null)
  const pegDrag = useRef(false)
  const [pegOver, setPegOver] = useState<{ bubble: Bubble; sx: number; sy: number } | null>(null)
  const [pegging, setPegging] = useState(false)
  const streetFrame = useRef<HTMLIFrameElement>(null)
  const [measureOn, setMeasureOn] = useState(false)
  const [exploreOpen, setExploreOpen] = useState(false)
  const pickRef = useRef<((xy: [number, number]) => void) | null>(null) // Explore: next terrain click goes here instead of the info card
  const interRef = useRef<InteractionsHandle | null>(null)
  const dockApi = useRef<DockApi | null>(null)
  const ownDriveAt = useRef(0) // last time THIS tab asked for a drive (chat drive tool or "Drive here")
  const autoFollowed = useRef(false)
  const modeNow = useRef(initialMode)

  useEffect(() => {
    let dead = false
    let v: Cesium.Viewer | undefined
    ;(async () => {
      if (!webglAvailable()) { setGlFail('WebGL context unavailable'); return }
      const s = await Site.load()
      if (dead || !box.current) return
      // Everything uses the sphere of contract 1 so site metres map exactly to lon/lat.
      Cesium.Ellipsoid.default = SPHERE
      const token = import.meta.env.VITE_CESIUM_ION_TOKEN as string | undefined
      if (token) Cesium.Ion.defaultAccessToken = token
      try {
      v = new Cesium.Viewer(box.current, {
        baseLayer: false,
        terrainProvider: siteTerrain(s),
        geocoder: false,
        baseLayerPicker: false,
        sceneModePicker: false,
        homeButton: false,
        navigationHelpButton: false,
        animation: false,
        timeline: false,
        fullscreenButton: false,
        infoBox: false,
        selectionIndicator: false,
        showRenderLoopErrors: false,
        creditContainer: creditsEl.current ?? undefined,
      })
      } catch (e) {
        setGlFail(String(e instanceof Error ? e.message : e).slice(0, 200))
        return
      }
      const scene = v.scene
      addMarsBasemap(v)
      // Visible while the mosaic tiles are still arriving. The ion tileset hides this globe.
      scene.globe.baseColor = Cesium.Color.fromCssColorString('#8c3d28')
      scene.globe.showGroundAtmosphere = false
      // Sky: Cesium's atmosphere shader doesn't draw below the sphere surface (the Jezero floor is
      // ~2.5 km under it), so tint the background directly: dusty tan at the ground, black in space.
      if (scene.skyAtmosphere) scene.skyAtmosphere.show = false
      const sky = Cesium.Color.fromCssColorString('#a88866'), space = Cesium.Color.fromCssColorString('#05060a')
      scene.preRender.addEventListener(() => {
        const t = Cesium.Math.clamp((scene.camera.positionCartographic.height + 3000) / 120000, 0, 1)
        Cesium.Color.lerp(sky, space, Math.sqrt(t), scene.backgroundColor)
      })
      scene.fog.enabled = false
      scene.globe.depthTestAgainstTerrain = true

      // The site terrain provider is flat outside the site, and a transparent globe still writes
      // depth, which hid every part of the Mars tileset below that flat surface (the dark spots).
      // So draw the globe only inside the site rectangle, and cut the tileset out under it.
      // Tileset hole: the site rectangle shrunk by INSET metres, so the tileset and our terrain
      // overlap slightly at the seam instead of leaving a black gap where neither draws.
      const INSET = s.ctx ? 800 : 200
      const ring = () => {
        const dLon = INSET / (R * Math.cos(Cesium.Math.toRadians(s.manifest.lat0))), dLat = INSET / R
        return Cesium.Cartesian3.fromRadiansArray(
          [s.extent.west + dLon, s.extent.south + dLat, s.extent.east - dLon, s.extent.south + dLat,
           s.extent.east - dLon, s.extent.north - dLat, s.extent.west + dLon, s.extent.north - dLat], SPHERE)
      }
      // Globe clipping uses planes, not ClippingPolygon: polygon clipping removes whole globe tiles
      // and crashes Cesium's TerrainFillMesh (northIndicesWestToEast undefined) during flights.
      // Meridian planes are exact. Latitude edges are constant-z planes chosen so the globe can
      // never extend PAST the site (north uses the lowest terrain radius, south the highest):
      // beyond the site the globe is flat and writes depth, hiding the Mars tileset (black strip).
      // That trims up to ~(relief * tan(lat)) m inside the north/south edges, which the shrunken
      // tileset hole (INSET) covers.
      if (!new URLSearchParams(location.search).has('noglobeclip')) {
        const zS = (R + s.relief.max) * Math.sin(s.extent.south), zN = (R + s.relief.min) * Math.sin(s.extent.north)
        const w = s.extent.west, e = s.extent.east
        scene.globe.clippingPlanes = new Cesium.ClippingPlaneCollection({
          unionClippingRegions: true, // clip anything outside the rectangle
          planes: [
            new Cesium.ClippingPlane(new Cesium.Cartesian3(-Math.sin(w), Math.cos(w), 0), 0),
            new Cesium.ClippingPlane(new Cesium.Cartesian3(Math.sin(e), -Math.cos(e), 0), 0),
            new Cesium.ClippingPlane(new Cesium.Cartesian3(0, 0, 1), -zS),
            new Cesium.ClippingPlane(new Cesium.Cartesian3(0, 0, -1), zN),
          ],
        })
      }
      let tilesetRef: Cesium.Cesium3DTileset | undefined

      const next: Record<string, { on: boolean; layer: Cesium.ImageryLayer }> = {}
      for (const L of s.ctx?.manifest.layers ?? []) {
        // NASA CTX context imagery drawn first (below) so HiRISE draws over it inside the site
        const layer = new Cesium.ImageryLayer(siteImagery(s, L.id, L.max_level, undefined,
          { prefix: '/ctx/tiles', sizeM: s.ctx!.manifest.size_m, rect: s.ctxRectangle }))
        v.imageryLayers.add(layer)
        next['ctx_' + L.id] = { on: true, layer }
      }
      for (const L of s.manifest.layers) {
        const layer = new Cesium.ImageryLayer(siteImagery(s, L.id, L.max_level, L.core))
        layer.show = L.kind === 'base'
        if (L.kind === 'overlay') layer.alpha = 0.8
        else { layer.saturation = 0.78; layer.contrast = 1.12 } // false-colour ortho: a touch less neon, more relief
        v.imageryLayers.add(layer)
        next[L.id] = { on: layer.show, layer }
      }
      setLayers(next)

      if (token) {
        try {
          const ts = await Cesium.Cesium3DTileset.fromIonAssetId(MARS_ION_ASSET)
          ts.maximumScreenSpaceError = 8
          ts.clippingPolygons = new Cesium.ClippingPolygonCollection({
            polygons: [new Cesium.ClippingPolygon({ positions: ring() })],
          })
          v.scene.primitives.add(ts)
          tilesetRef = ts
          marsTileset = ts
        } catch (e) {
          console.warn('Mars globe tileset failed', (e as Error).message)
        }
      }

      // Cursor readout for the status strip: lat/lon always, site metres and elevation inside the site.
      let lastMove = 0
      v.canvas.addEventListener('pointermove', (ev) => {
        const now = performance.now()
        if (now - lastMove < 100) return
        lastMove = now
        const ll = pickMarsLatLon(v!, ev.clientX, ev.clientY)
        if (!ll) { setCursor(''); return }
        const lon = Cesium.Math.toRadians(ll.lonDeg), lat = Cesium.Math.toRadians(ll.latDeg)
        let txt = `${Math.abs(ll.latDeg).toFixed(4)}°${ll.latDeg >= 0 ? 'N' : 'S'}  ${Math.abs(eastLon(ll.lonDeg)).toFixed(4)}°${eastLon(ll.lonDeg) >= 0 ? 'E' : 'W'}`
        const z = s.elevation(lon, lat)
        if (!Number.isNaN(z)) {
          const [x, y] = s.lonLatToXY(lon, lat)
          txt += `  ·  elev ${z.toFixed(0)} m  ·  x ${x.toFixed(0)} y ${y.toFixed(0)}`
        }
        setCursor(txt)
      })
      v.canvas.addEventListener('pointerleave', () => setCursor(''))
      scene.renderError.addEventListener((_sc: unknown, err: unknown) => {
        ;(window as unknown as { __renderError?: string }).__renderError = String(err)
        console.error('cesium renderError', err)
        if (/webgl|context/i.test(String(err))) setGlFail(String(err).slice(0, 200))
      })
      const loadTimer = window.setTimeout(() => setLoaded(true), 30000)
      // Deterministic "fully streamed" signal for screenshot checks (tab title + window flag).
      let stable = 0
      scene.postRender.addEventListener(() => {
        const done = scene.globe.tilesLoaded && (!tilesetRef || tilesetRef.tilesLoaded)
        stable = done ? stable + 1 : 0
        if (stable === 12) { window.clearTimeout(loadTimer); setLoaded(true) }
        if (stable === 45 && !(window as unknown as { __ready?: boolean }).__ready) {
          ;(window as unknown as { __ready?: boolean }).__ready = true
          document.title = 'READY'
        }
      })
      ;(window as unknown as { __viewer?: Cesium.Viewer }).__viewer = v
      const bootMode = (new URLSearchParams(location.search).get('view') as Mode) || 'planet'
      let lastHud = 0
      roverPoseHud = (p) => {
        const now = performance.now()
        if (now - lastHud < 250) return
        lastHud = now
        setDrive(`Rover ${p.status}${p.stop_reason ? ' (' + p.stop_reason + ')' : ''} · ${p.display_speed_mps.toFixed(1)} m/s · slope ${p.slope_deg.toFixed(1)}°`)
      }
      if (bootMode !== 'planet') ensureRover(v, s)
      // Real Perseverance traverse/waypoints and IAU names; start the rover at the first real waypoint.
      placesCtl = new PlacesLayer(v, s)
      placesCtl.load().then(() => {
        const w = placesCtl!.first()
        if (w && roverCtl) roverCtl.driver.teleport(w.x, w.y, w.heading ?? 0)
      }).catch((e) => console.error('places', e))
      setSite(s)
      setViewer(v)
      fly(v, s, bootMode, 0)
      setCameraMode(bootMode)
    })().catch((e) => {
      console.error('viewer init failed', e)
      setLoadErr(String(e instanceof Error ? e.message : e).slice(0, 200))
    })
    return () => {
      dead = true
      v?.destroy()
    }
  }, [])

  const toggle = (id: string) => {
    if (id.startsWith('vec:')) {
      const k = id.slice(4) as 'traverse' | 'names'
      const on = !vec[k]
      if (k === 'traverse') placesCtl?.setTraverse(on); else placesCtl?.setNames(on)
      setVec({ ...vec, [k]: on })
      return
    }
    const cur = layers[id]
    if (!cur) return
    cur.layer.show = !cur.on
    setLayers({ ...layers, [id]: { ...cur, on: !cur.on } })
  }

  const note = (msg: string) => {
    setToast(msg)
    window.setTimeout(() => setToast((t) => (t === msg ? '' : t)), 3500)
  }

  function leaveStreet() {
    setStreetDrop(null)
    setSurfaceView('planet')
    resetPegman(pegRef.current)
  }

  function driveAt(lat: number, lon: number, title: string) {
    if (!viewer) return
    const drop = dropOnBubbles(lat, lon)
    if (!drop) {
      note('No street view here')
      return
    }
    lookAtBubble(viewer, lat, lon, 15000)
    releaseRover()
    setStreetDrop(drop)
    setSurfaceView('street')
    setCameraMode('planet')
    void title
  }

  const search = (q: string) => { setQuery(q); setHits(placesCtl ? placesCtl.search(q) : []) }

  useEffect(() => { modeNow.current = cameraMode }, [cameraMode])
  useEffect(() => { flyPlanetRef.current = flyPlanet })
  // Street-view on Jezero highlights Jezero; Olympus street view highlights Mons (mode id "drive").
  const uiMode: UiMode = surfaceView === 'street'
    ? (streetDrop?.world === 'olympus' ? 'drive' : 'jezero')
    : cameraMode === 'planet' ? 'mars' : 'jezero'

  const goMars = () => {
    if (!viewer || !site) return
    if (surfaceView === 'street') leaveStreet()
    fly(viewer, site, 'planet', 2)
    setCameraMode('planet')
  }
  const goJezero = (duration = 2.5) => {
    if (!viewer || !site) return
    if (surfaceView === 'street') leaveStreet()
    fly(viewer, site, 'site', duration)
    setCameraMode('site')
  }
  const goMons = () => {
    if (surfaceView === 'street' && streetDrop?.world === 'olympus') return
    const b = BUBBLES.find((x) => x.id === 'olympus') ?? BUBBLES[1]
    driveAt(b.lat, b.lon, b.title)
  }
  const setMode = (m: UiMode) => { if (m === 'mars') goMars(); else if (m === 'jezero') goJezero(); else goMons() }
  const modeRef = useRef(setMode)
  useEffect(() => { modeRef.current = setMode })

  // 1/2/3 switch modes (also when the street-view iframe has focus: it forwards these keys, see onFrameLoad).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const el = e.target as HTMLElement | null
      if (el && (el.isContentEditable || el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT')) return
      const m = ({ Digit1: 'mars', Digit2: 'jezero', Digit3: 'drive' } as Record<string, UiMode>)[e.code]
      if (m) modeRef.current(m)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const inSite = (p: Place) => {
    if (!site) return false
    const half = site.manifest.size_m / 2
    return Math.abs(p.x) <= half && Math.abs(p.y) <= half
  }
  const goTo = (p: Place) => {
    if (!viewer || !site) return
    setHits([])
    setQuery(p.name)
    if (surfaceView === 'street') leaveStreet()
    if (p.kind === 'waypoint') {
      ensureRover(viewer, site).driver.teleport(p.x, p.y, p.heading ?? 0)
      fly(viewer, site, 'rover', 0)
      setCameraMode('rover')
    } else {
      // Names inside the site are shown in Jezero, everything else on the globe (the site view clips the globe to the site).
      const target: Mode = inSite(p) ? 'site' : 'planet'
      if ((target === 'planet') !== (cameraMode === 'planet') || cameraMode === 'rover') {
        fly(viewer, site, target, 0)
        setCameraMode(target)
      } else roverCtl?.setChase(false)
      viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(p.lon, p.lat, Math.max(target === 'site' ? 3000 : 15000, (p.diameterKm ?? 5) * 2500), SPHERE),
        orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 }, duration: 2,
      })
    }
  }

  const resetView = () => goJezero(2)

  /** Camera follows the rover (smooth chase). Used by the status-strip toggle, by drives, and by "watch in Jezero". */
  const startFollow = () => {
    if (!viewer || !site) return
    if (surfaceView === 'street') leaveStreet()
    if (cameraMode === 'planet' || surfaceView === 'street') fly(viewer, site, 'site', 0)
    fly(viewer, site, 'rover', 0)
    setCameraMode('rover')
  }
  const stopFollow = () => {
    if (!viewer || !site) return
    roverCtl?.setChase(false)
    setCameraMode('site')
    applyControls(viewer, 'site', site)
  }
  const autoFollow = () => { if (surfaceView !== 'street') startFollow() } // street view keeps its view; the dock offers "watch in Jezero"
  const followRef = useRef({ startFollow, stopFollow, autoFollow })
  useEffect(() => { followRef.current = { startFollow, stopFollow, autoFollow } })

  // A drive started (rover.state went auto/fsd): follow it only when THIS tab asked for it (chat drive tool or "Drive here").
  // When it ends, fly back out to an overview that shows the whole route.
  const onDrive = (d: DriveInfo) => {
    const was = wasActive.current
    wasActive.current = d.active
    if (d.active && !was && Date.now() - ownDriveAt.current < 120000) {
      autoFollowed.current = true
      followRef.current.autoFollow()
    }
    if (d.route_pts && d.route_pts.length > 1) lastRoute.current = d.route_pts
    if (!d.active && was && autoFollowed.current) {
      autoFollowed.current = false
      if (modeNow.current === 'rover') window.setTimeout(() => flyOverview(), 1200)
    }
    if (d.active || was) setDriveInfo(d)
    if (!d.active && was) window.setTimeout(() => setDriveInfo((cur) => (cur && !cur.active ? null : cur)), 6000)
  }
  const lastRoute = useRef<[number, number][]>([])
  useEffect(() => { if (import.meta.env.DEV) (window as unknown as { __planetCamera?: unknown }).__planetCamera = (c: Parameters<typeof flyPlanet>[0]) => flyPlanetRef.current(c) }, [])
  /** Smoothly leave the chase camera and frame the route (plus the rover) from a site-overview angle. */
  const flyOverview = () => {
    if (!viewer || !site) return
    const pts = lastRoute.current
    const pose = roverCtl?.driver.pose
    roverCtl?.setChase(false)
    setCameraMode('site')
    applyControls(viewer, 'site', site)
    const world = [...pts.filter((_, i) => i % 5 === 0), ...(pose ? [[pose.x, pose.y] as [number, number]] : [])].map(([x, y]) => site.position(x, y, 0))
    if (!world.length) return
    const bs = Cesium.BoundingSphere.fromPoints(world)
    viewer.camera.flyToBoundingSphere(bs, { offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-45), Math.min(25000, Math.max(1200, bs.radius * 2.8 + 200))), duration: 3 })
  }
  const flyPlanetRef = useRef<(c: { lat: number; lon: number; height_m?: number; label?: string }) => void>(() => undefined)
  const flyPlanet = (c: { lat: number; lon: number; height_m?: number; label?: string }) => {
    if (!viewer || !site) return
    if (surfaceView === 'street') leaveStreet()
    if (modeNow.current !== 'planet') { fly(viewer, site, 'planet', 0); setCameraMode('planet') }
    window.setTimeout(() => {
      viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(eastLon(c.lon), c.lat, c.height_m ?? 3_500_000, SPHERE),
        orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 }, duration: 3,
      })
      interRef.current?.planet({ lat: c.lat, lon: c.lon, label: c.label })
    }, 80)
  }

  // Dragging the map while following hands the camera back to the user.
  useEffect(() => {
    if (!viewer) return
    const el = viewer.canvas
    let down: { x: number; y: number } | null = null
    const onDown = (e: PointerEvent) => { down = { x: e.clientX, y: e.clientY } }
    const onMove = (e: PointerEvent) => {
      if (!down || !(e.buttons & 3)) return
      if (Math.hypot(e.clientX - down.x, e.clientY - down.y) > 6 && roverCtl?.chase) followRef.current.stopFollow()
    }
    const onUp = () => { down = null }
    el.addEventListener('pointerdown', onDown)
    el.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => { el.removeEventListener('pointerdown', onDown); el.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp) }
  }, [viewer])
  const toggleGround = () => {
    if (!viewer || !site) return
    const next: Mode = cameraMode === 'ground' ? 'site' : 'ground'
    fly(viewer, site, next, 2)
    setCameraMode(next)
  }

  const layerRows: LayerRow[] = []
  if (site) {
    const row = (id: string, L: { title: string; mission?: string; instrument?: string; credit?: string }, on: boolean): LayerRow => {
      const fc = /false colou?r/i.test(L.title)
      return { id, title: L.title.replace(/\s*\(false colou?r\)/i, ''), sub: [L.mission, L.instrument].filter(Boolean).join(' '), on, falseColour: fc, title_attr: L.credit }
    }
    for (const L of site.ctx?.manifest.layers ?? []) layerRows.push(row('ctx_' + L.id, L, layers['ctx_' + L.id]?.on ?? false))
    for (const L of site.manifest.layers) layerRows.push(row(L.id, L, layers[L.id]?.on ?? false))
    layerRows.push({ id: 'vec:traverse', title: 'Perseverance traverse and waypoints', sub: 'Mars 2020, NASA/JPL', on: vec.traverse })
    layerRows.push({ id: 'vec:names', title: 'Place names', sub: 'IAU/USGS Gazetteer', on: vec.names })
  }

  const showPegman = cameraMode === 'planet' && surfaceView === 'planet' && viewer && !glFail

  const onPegPointerDown = (e: React.PointerEvent) => {
    if (!showPegman) return
    e.preventDefault()
    pegDrag.current = true
    setPegging(true)
    e.currentTarget.setPointerCapture(e.pointerId)
    const el = pegRef.current
    if (el) {
      el.style.right = 'auto'
      el.style.bottom = 'auto'
      el.style.left = `${e.clientX - 16}px`
      el.style.top = `${e.clientY - 16}px`
    }
  }

  const onPegPointerMove = (e: React.PointerEvent) => {
    if (!pegDrag.current || !pegRef.current) return
    pegRef.current.style.left = `${e.clientX - 16}px`
    pegRef.current.style.top = `${e.clientY - 16}px`
    setPegOver(viewer ? nearestBubble(viewer, e.clientX, e.clientY) : null)
  }

  const onPegPointerUp = (e: React.PointerEvent) => {
    if (!pegDrag.current) return
    pegDrag.current = false
    setPegging(false)
    setPegOver(null)
    try { e.currentTarget.releasePointerCapture(e.pointerId) } catch { /* already released */ }
    resetPegman(pegRef.current)
    if (!viewer || e.type === 'pointercancel') return
    const ll = pickMarsLatLon(viewer, e.clientX, e.clientY)
    let drop = ll ? dropOnBubbles(ll.latDeg, ll.lonDeg) : null
    if (!drop) {
      const near = nearestBubble(viewer, e.clientX, e.clientY)
      if (near) drop = { world: near.bubble.id, x: 0, y: 0, heading: 0 }
    }
    if (!drop) {
      note('Not on a drivable patch. Drop the pegman on the Jezero or Olympus pin (or click a pin).')
      return
    }
    releaseRover()
    setStreetDrop(drop)
    setSurfaceView('street')
  }

  const sandboxSrc = streetDrop
    ? `/sandbox/?world=${encodeURIComponent(streetDrop.world)}&x=${streetDrop.x}&y=${streetDrop.y}&heading=0&link=0`
    : ''

  useEffect(() => {
    if (!viewer) return
    const driving = surfaceView === 'street'
    viewer.useDefaultRenderLoop = !driving
    if (!driving) {
      viewer.resize()
      viewer.scene.requestRender()
    }
  }, [viewer, surfaceView])

  /** Same-origin iframe: quiet the sandbox HUD and forward the mode keys (1/2/3) to this page. */
  const onFrameLoad = () => {
    const win = streetFrame.current?.contentWindow
    const doc = win?.document
    if (!win || !doc) return
    const st = doc.createElement('style')
    st.textContent = `#data { display: none !important; }
      .panel { background: rgba(22,18,15,.86) !important; border-color: #38302a !important; border-radius: 8px !important; }
      #worlds { top: 12px !important; right: 12px !important; } #hud { top: 12px !important; left: 12px !important; }
      #help { bottom: 12px !important; right: 12px !important; }`
    doc.head.appendChild(st)
    // Keep only Jezero/Mons street worlds; rename the switcher labels for the globe UI.
    const trim = () => doc.querySelectorAll<HTMLAnchorElement>('#worlds a').forEach((a) => {
      const t = (a.textContent ?? '').trim().toLowerCase()
      if (t === 'crater') a.textContent = 'jezero'
      else if (t === 'olympus') a.textContent = 'mons'
      else if (t !== 'jezero' && t !== 'mons') a.style.display = 'none'
    })
    trim()
    const worlds = doc.getElementById('worlds')
    if (worlds) new MutationObserver(trim).observe(worlds, { childList: true, subtree: true })
    doc.addEventListener('keydown', (e) => {
      if (e.code === 'Digit1' || e.code === 'Digit2' || e.code === 'Digit3') {
        window.dispatchEvent(new KeyboardEvent('keydown', { code: e.code, key: e.key }))
      }
    })
  }

  useEffect(() => {
    if (surfaceView !== 'street') return
    const frame = streetFrame.current
    const focusFrame = () => {
      frame?.focus()
      try { frame?.contentWindow?.focus() } catch { /* focus is allowed cross-origin */ }
    }
    focusFrame()
    const later = window.setTimeout(focusFrame, 500)
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      if (el && (el.isContentEditable || el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT')) return
      frame?.contentWindow?.postMessage({
        type: 'ares-drive-key',
        code: e.code,
        down: e.type === 'keydown',
        repeat: e.repeat,
      }, window.location.origin)
      if (e.type === 'keydown' && (e.code.startsWith('Arrow') || e.code === 'Space')) e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('keyup', onKey)
    return () => {
      window.clearTimeout(later)
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('keyup', onKey)
    }
  }, [surfaceView, sandboxSrc])

  const statusLeft = surfaceView === 'street'
    ? `Street view · ${streetDrop?.world === 'olympus' ? 'Olympus Mons scarp' : 'Jezero fan'}`
    : cursor || (loaded ? (cameraMode === 'planet' ? 'Mars · move the cursor over the globe' : 'Jezero · move the cursor over the map') : '')

  return (
    <div className="app">
      <TopBar mode={uiMode} ready={!!viewer && !!site} onMode={setMode} query={query} hits={hits} onSearch={search} onPick={goTo}
        layers={layerRows} onToggleLayer={toggle} />
      <main className="stage">
        <div ref={box} className={`cesium${surfaceView === 'street' ? ' cesium--hidden' : ''}`} />
        {surfaceView === 'street' && streetDrop && (
          <iframe ref={streetFrame} className="street-frame" title="Rover street view" src={sandboxSrc} tabIndex={0} onLoad={onFrameLoad} />
        )}
        {uiMode === 'jezero' && (
          <div className="viewtools">
            <button type="button" className="iconbtn" aria-label="Reset view" title="Reset view" onClick={resetView}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5" /></svg>
            </button>
            <button type="button" className="iconbtn" aria-label="Measure distance" aria-pressed={measureOn} title="Measure: click A then B (M)" onClick={() => setMeasureOn(!measureOn)}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 17 17 3l4 4L7 21l-4-4ZM7 13l2 2M10 10l2 2M13 7l2 2" /></svg>
            </button>
            <button type="button" className="iconbtn" aria-label="Explore tools" aria-pressed={exploreOpen} title="Explore: lake, sun, minerals, reach, viewshed, rover history, tour (E)" onClick={() => setExploreOpen(!exploreOpen)}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="m15.5 8.5-2 5-5 2 2-5 5-2Z" /></svg>
            </button>
            <button type="button" className="iconbtn" aria-label="Ground view" aria-pressed={cameraMode === 'ground'} title="Ground-level view (drag to look around)" onClick={toggleGround}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="5" r="2" /><path d="M12 8v7M8 22l4-7 4 7M7 12h10" /></svg>
            </button>
          </div>
        )}
        {showPegman && (
          <div
            ref={pegRef}
            className="pegman"
            role="img"
            aria-label="Drag onto a marked patch for street view"
            title="Drag onto a marked patch for street view"
            onPointerDown={onPegPointerDown}
            onPointerMove={onPegPointerMove}
            onPointerUp={onPegPointerUp}
            onPointerCancel={onPegPointerUp}
          />
        )}
        {pegging && (
          <div className="peg-hint" role="status">{pegOver ? `Drop here to drive: ${pegOver.bubble.title}` : 'Drag onto the Jezero or Olympus pin'}</div>
        )}
        {pegging && pegOver && (
          <div className="peg-ring" style={{ left: pegOver.sx - 28, top: pegOver.sy - 28 }} aria-hidden="true" />
        )}
        {!loaded && !glFail && !loadErr && (
          <div className="overlay-msg" role="status"><div><span className="spinner" aria-hidden="true" />Loading Mars terrain…</div></div>
        )}
        {loadErr && <div className="overlay-msg" role="alert"><div>Could not load the site data: {loadErr}</div></div>}
        {driveInfo && (
          <div className="drivechip" role="status">
            <span className={`drivechip__dot${driveInfo.active ? ' drivechip__dot--on' : ''}`} aria-hidden="true" />
            {driveInfo.active ? (
              <span>
                Driving ({driveInfo.mode})
                {driveInfo.remaining_m != null ? ` · ${Math.round(driveInfo.remaining_m).toLocaleString()} m left` : ''}
                {` · ${driveInfo.speed_mps.toFixed(1)} m/s`}
              </span>
            ) : <span>{driveInfo.last_event || 'Drive finished'}</span>}
            {driveInfo.active && driveInfo.remaining_m != null && driveInfo.total_m ? (
              <span className="drivechip__bar" aria-hidden="true"><span style={{ width: `${Math.min(100, Math.max(0, 100 * (1 - driveInfo.remaining_m / driveInfo.total_m)))}%` }} /></span>
            ) : null}
          </div>
        )}
        {toast && <div className="toast" role="status">{toast}</div>}
        {viewer && site && (
          <Interactions viewer={viewer} site={site} mode={uiMode} cameraMode={cameraMode} namesOn={vec.names} measureOn={measureOn} setMeasureOn={setMeasureOn}
            getPlaces={() => placesCtl?.places ?? []} dockApi={dockApi} note={note} onOwnDrive={() => { ownDriveAt.current = Date.now() }}
            onJezero={() => goJezero()} onDriveBubble={(b) => driveAt(b.lat, b.lon, b.title)} handleRef={interRef} pickRef={pickRef} />
        )}
        <ExplorePanel viewer={viewer} site={site} mode={uiMode} open={exploreOpen} setOpen={setExploreOpen} measureOn={measureOn} setMeasureOn={setMeasureOn}
          pickRef={pickRef} traverseOn={vec.traverse} setTraverseVisible={(on) => placesCtl?.setTraverse(on)} enterSite={() => goJezero(2.5)} />
        <AtlasDock
          apiRef={dockApi}
          onHighlight={(h, kind, sx, sy) => interRef.current?.highlight(h, kind, sx, sy)}
          onPlanetCamera={flyPlanet}
          onOwnDrive={() => { ownDriveAt.current = Date.now() }}
          viewer={viewer}
          site={site}
          getRover={() => roverCtl}
          cameraMode={cameraMode}
          onEnterSite={() => goJezero(2.5)}
          onDrive={onDrive}
          onWatch={startFollow}
          street={surfaceView === 'street' && streetDrop ? { world: streetDrop.world, frame: streetFrame } : null}
        />
      </main>
      <StatusBar left={statusLeft} right={uiMode === 'jezero' ? (driveInfo?.active ? `Rover driving (${driveInfo.mode}) · ${driveInfo.speed_mps.toFixed(1)} m/s` : drive) : ''} creditsRef={creditsEl}
        follow={uiMode === 'jezero' ? { on: cameraMode === 'rover', toggle: () => (cameraMode === 'rover' ? stopFollow() : startFollow()) } : undefined} />
      {glFail && <NoWebGL detail={glFail} />}
    </div>
  )
}

/** Mouse controls per camera mode. Ground = first-person look; site/planet = rotate + tilt. */
function applyControls(v: Cesium.Viewer, mode: Mode, s?: Site) {
  if (mode === 'planet') releaseRover()
  else if (s) ensureRover(v, s)
  roverCtl?.setChase(mode === 'rover')
  const c = v.scene.screenSpaceCameraController
  const T = Cesium.CameraEventType, K = Cesium.KeyboardEventModifier
  const ctl = c as unknown as Record<string, unknown>
  if (mode === 'ground') {
    ctl.lookEventTypes = T.LEFT_DRAG // drag = look around (yaw + pitch)
    ctl.rotateEventTypes = { eventType: T.LEFT_DRAG, modifier: K.SHIFT } // shift+drag = swing around
    ctl.tiltEventTypes = [T.MIDDLE_DRAG, T.PINCH]
    ctl.zoomEventTypes = [T.WHEEL, T.PINCH, T.RIGHT_DRAG] // wheel / right-drag = move forward/back
  } else {
    ctl.rotateEventTypes = T.LEFT_DRAG // drag = rotate planet
    ctl.tiltEventTypes = [T.RIGHT_DRAG, T.MIDDLE_DRAG, T.PINCH] // right-drag up/down = tilt
    ctl.lookEventTypes = { eventType: T.LEFT_DRAG, modifier: K.SHIFT }
    ctl.zoomEventTypes = [T.WHEEL, T.PINCH]
  }
}

function siteClippingRing(s: Site): Cesium.Cartesian3[] {
  const INSET = s.ctx ? 800 : 200
  const dLon = INSET / (R * Math.cos(Cesium.Math.toRadians(s.manifest.lat0))), dLat = INSET / R
  return Cesium.Cartesian3.fromRadiansArray(
    [s.extent.west + dLon, s.extent.south + dLat, s.extent.east - dLon, s.extent.south + dLat,
     s.extent.east - dLon, s.extent.north - dLat, s.extent.west + dLon, s.extent.north - dLat], SPHERE)
}

function applySiteGlobeClip(scene: Cesium.Scene, s: Site) {
  if (new URLSearchParams(location.search).has('noglobeclip')) return
  const zS = (R + s.relief.max) * Math.sin(s.extent.south), zN = (R + s.relief.min) * Math.sin(s.extent.north)
  const w = s.extent.west, e = s.extent.east
  scene.globe.clippingPlanes = new Cesium.ClippingPlaneCollection({
    unionClippingRegions: true,
    planes: [
      new Cesium.ClippingPlane(new Cesium.Cartesian3(-Math.sin(w), Math.cos(w), 0), 0),
      new Cesium.ClippingPlane(new Cesium.Cartesian3(Math.sin(e), -Math.cos(e), 0), 0),
      new Cesium.ClippingPlane(new Cesium.Cartesian3(0, 0, 1), -zS),
      new Cesium.ClippingPlane(new Cesium.Cartesian3(0, 0, -1), zN),
    ],
  })
}

function applySiteTilesetClip(ts: Cesium.Cesium3DTileset, s: Site) {
  ts.clippingPolygons = new Cesium.ClippingPolygonCollection({
    polygons: [new Cesium.ClippingPolygon({ positions: siteClippingRing(s) })],
  })
}

function hideSiteImagery(v: Cesium.Viewer) {
  if (siteImageryShown) return
  siteImageryShown = []
  const layers = v.imageryLayers
  for (let i = 0; i < layers.length; i++) {
    const layer = layers.get(i)
    siteImageryShown.push(layer.show)
    if (!marsBasemapLayers.has(layer)) layer.show = false
  }
}

function restoreSiteImagery(v: Cesium.Viewer) {
  if (!siteImageryShown) return
  const layers = v.imageryLayers
  for (let i = 0; i < layers.length; i++) layers.get(i).show = siteImageryShown[i] ?? false
  siteImageryShown = null
}

function clearPlanetBubbles(v: Cesium.Viewer) {
  for (const prim of bubblePrimitives) {
    v.scene.primitives.remove(prim)
    if (!prim.isDestroyed()) prim.destroy()
  }
  bubblePrimitives = []
  for (const marker of bubbleMarkers) v.entities.remove(marker)
  bubbleMarkers = []
}

function showPlanetBubbles(v: Cesium.Viewer) {
  clearPlanetBubbles(v)
  const vertexFormat = Cesium.MaterialAppearance.MaterialSupport.TEXTURED.vertexFormat
  for (const bubble of BUBBLES) {
    const halfDegLat = (bubble.sizeM / 2) / R * 180 / Math.PI
    const halfDegLon = halfDegLat / Math.cos(bubble.lat * Math.PI / 180)
    const lon = eastLon(bubble.lon)
    const rect = Cesium.Rectangle.fromDegrees(
      lon - halfDegLon, bubble.lat - halfDegLat,
      lon + halfDegLon, bubble.lat + halfDegLat,
    )
    const prim = new Cesium.GroundPrimitive({
      geometryInstances: new Cesium.GeometryInstance({
        geometry: new Cesium.RectangleGeometry({ rectangle: rect, vertexFormat }),
      }),
      appearance: new Cesium.MaterialAppearance({
        material: Cesium.Material.fromType('Image', {
          image: `/worlds/${bubble.id}/tiles/ortho/0/0/0.webp`,
        }),
      }),
      classificationType: Cesium.ClassificationType.BOTH,
    })
    v.scene.primitives.add(prim)
    bubblePrimitives.push(prim)
    // Ortho patches are ~2 km; at planet distance they are sub-pixel. Labels mark the drops.
    bubbleMarkers.push(v.entities.add({
      id: `bubble:${bubble.id}`,
      position: Cesium.Cartesian3.fromDegrees(lon, bubble.lat, 0, SPHERE),
      point: {
        pixelSize: 12,
        color: Cesium.Color.fromCssColorString('#e0794a'),
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 2,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      },
    }))
  }
}

function fly(v: Cesium.Viewer, s: Site, mode: Mode, duration: number) {
  const lat0 = s.manifest.lat0, lon0 = s.manifest.lon0
  applyControls(v, String(mode).startsWith('alt:') ? 'site' : mode, s)
  if (mode === 'rover') return // chase camera is driven by the rover each frame
  if (mode === 'planet') {
    v.scene.globe.clippingPlanes = undefined as unknown as Cesium.ClippingPlaneCollection
    if (marsTileset) marsTileset.clippingPolygons = undefined as unknown as Cesium.ClippingPolygonCollection
    v.scene.globe.show = !marsTileset
    hideSiteImagery(v)
    showPlanetBubbles(v)
    // Midpoint lon between Jezero (~77E) and Olympus (~222E). Height high enough that both
    // sit inside the visible disk (horizon ~75°); at 4e6 m both were over the limb.
    v.camera.flyTo({
      destination: Cesium.Cartesian3.fromDegrees(150, 20, 9_000_000, SPHERE),
      orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 },
      duration,
    })
    return
  }
  clearPlanetBubbles(v)
  v.scene.globe.show = true
  restoreSiteImagery(v)
  applySiteGlobeClip(v.scene, s)
  if (marsTileset) applySiteTilesetClip(marsTileset, s)
  if (String(mode).startsWith('alt:')) {
    // test hook: straight-down view from a given altitude in metres, e.g. ?view=alt:40000
    v.camera.setView({
      destination: Cesium.Cartesian3.fromDegrees(lon0, lat0, Number(String(mode).slice(4)), SPHERE),
      orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 },
    })
  } else if (mode === 'site') {
    // look at the site centre from the south at ~35 degrees, 6 km out
    // framed on the traverse + landing area (centre shifted east/south of the site centre) so the panels do not hide it
    const target = s.position(-250, -450, 0)
    v.camera.flyToBoundingSphere(new Cesium.BoundingSphere(target, 1500), {
      offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-45), 9500),
      duration,
    })
  } else {
    // eye height 1.7 m, 150 m south of the centre looking north
    const [lon, lat] = s.xyToLonLat(0, -150)
    const h = s.elevation(lon, lat)
    v.camera.flyTo({
      destination: Cesium.Cartesian3.fromRadians(lon, lat, (Number.isNaN(h) ? 0 : h) + 1.7, SPHERE),
      orientation: { heading: 0, pitch: Cesium.Math.toRadians(-5), roll: 0 },
      duration,
    })
  }
  void deg
}
