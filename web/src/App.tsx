import { useEffect, useRef, useState } from 'react'
import * as Cesium from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'
import { R, Site, SPHERE, deg } from './site'
import { siteImagery, siteTerrain } from './providers'
import { CesiumRover } from './cesiumRover'
import type { DriverPose } from './rover/driver'
import { PlacesLayer, type Place } from './places'
import { BUBBLES } from './bubbles'
import { dropOnBubbles, type StreetDrop } from './pegman'
import { AtlasDock } from './panels/chat/AtlasDock'
import './App.css'

type SurfaceView = 'planet' | 'street'

const MARS_ION_ASSET = 3644333
type Mode = 'orbit' | 'site' | 'ground' | 'rover' | 'planet'
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

function pickMarsLatLon(viewer: Cesium.Viewer, clientX: number, clientY: number): { latDeg: number; lonDeg: number } | null {
  const rect = viewer.canvas.getBoundingClientRect()
  const win = new Cesium.Cartesian2(clientX - rect.left, clientY - rect.top)
  let cartesian: Cesium.Cartesian3 | undefined = viewer.scene.pickPosition(win)
  if (!Cesium.defined(cartesian)) cartesian = viewer.camera.pickEllipsoid(win, SPHERE) ?? undefined
  if (!Cesium.defined(cartesian)) return null
  const carto = Cesium.Cartographic.fromCartesian(cartesian, SPHERE)
  return { latDeg: Cesium.Math.toDegrees(carto.latitude), lonDeg: Cesium.Math.toDegrees(carto.longitude) }
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
  const [status, setStatus] = useState('loading site…')
  const [hud, setHud] = useState('')
  const [drive, setDrive] = useState('')
  const [vec, setVec] = useState({ traverse: true, names: true })
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<Place[]>([])
  const initialMode = ((new URLSearchParams(location.search).get('view') as Mode) || 'site')
  const [cameraMode, setCameraMode] = useState<Mode>(initialMode)
  const [surfaceView, setSurfaceView] = useState<SurfaceView>('planet')
  const [streetDrop, setStreetDrop] = useState<StreetDrop | null>(null)
  const pegRef = useRef<HTMLDivElement>(null)
  const pegDrag = useRef(false)
  const streetFrame = useRef<HTMLIFrameElement>(null)

  useEffect(() => {
    let dead = false
    let v: Cesium.Viewer | undefined
    ;(async () => {
      const s = await Site.load()
      if (dead || !box.current) return
      // Everything uses the sphere of contract 1 so site metres map exactly to lon/lat.
      Cesium.Ellipsoid.default = SPHERE
      const token = import.meta.env.VITE_CESIUM_ION_TOKEN as string | undefined
      if (token) Cesium.Ion.defaultAccessToken = token
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
      })
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
          setStatus('site + Cesium Mars globe')
        } catch (e) {
          setStatus('site only (Mars globe failed: ' + (e as Error).message + ')')
        }
      } else setStatus('Drag the pegman onto a patch, or press Drive.')

      v.camera.moveEnd.addEventListener(() => {
        const c = v!.camera.positionCartographic
        const [x, y] = s.lonLatToXY(c.longitude, c.latitude)
        setHud(`x ${x.toFixed(0)} m  y ${y.toFixed(0)} m  alt ${c.height.toFixed(0)} m`)
      })
      scene.renderError.addEventListener((_sc: unknown, err: unknown) => {
        ;(window as unknown as { __renderError?: string }).__renderError = String(err)
        console.error('cesium renderError', err)
      })
      // Deterministic "fully streamed" signal for screenshot checks (tab title + window flag).
      let stable = 0
      scene.postRender.addEventListener(() => {
        const done = scene.globe.tilesLoaded && (!tilesetRef || tilesetRef.tilesLoaded)
        stable = done ? stable + 1 : 0
        if (stable === 45 && !(window as unknown as { __ready?: boolean }).__ready) {
          ;(window as unknown as { __ready?: boolean }).__ready = true
          document.title = 'READY'
        }
      })
      ;(window as unknown as { __viewer?: Cesium.Viewer }).__viewer = v
      const bootMode = (new URLSearchParams(location.search).get('view') as Mode) || 'site'
      let lastHud = 0
      roverPoseHud = (p) => {
        const now = performance.now()
        if (now - lastHud < 250) return
        lastHud = now
        setDrive(`rover ${p.status}${p.stop_reason ? ' (' + p.stop_reason + ')' : ''} · slope ${p.slope_deg.toFixed(1)}° · ` +
          `ahead ${p.ahead_slope_deg.toFixed(1)}° · ${p.display_speed_mps.toFixed(1)} m/s shown (${p.speed_mps.toFixed(2)} real) · x ${p.x.toFixed(0)} y ${p.y.toFixed(0)}`)
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
    })().catch((e) => setStatus('error: ' + e))
    return () => {
      dead = true
      v?.destroy()
    }
  }, [])

  const toggle = (id: string) => {
    const cur = layers[id]
    cur.layer.show = !cur.on
    setLayers({ ...layers, [id]: { ...cur, on: !cur.on } })
  }

  const toggleVec = (k: 'traverse' | 'names') => {
    const on = !vec[k]
    if (k === 'traverse') placesCtl?.setTraverse(on); else placesCtl?.setNames(on)
    setVec({ ...vec, [k]: on })
  }
  const search = (q: string) => { setQuery(q); setHits(placesCtl ? placesCtl.search(q) : []) }
  const goTo = (p: Place) => {
    if (!viewer || !site) return
    setHits([])
    setQuery(p.name)
    if (p.kind === 'waypoint') {
      if (surfaceView === 'street') leaveStreet()
      ensureRover(viewer, site).driver.teleport(p.x, p.y, p.heading ?? 0)
      fly(viewer, site, 'rover', 0)
      setCameraMode('rover')
    } else {
      roverCtl?.setChase(false)
      viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(p.lon, p.lat, Math.max(15000, (p.diameterKm ?? 5) * 2500), SPHERE),
        orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 }, duration: 2,
      })
    }
  }

  const showPegman = cameraMode === 'planet' && surfaceView === 'planet' && viewer

  const onPegPointerDown = (e: React.PointerEvent) => {
    if (!showPegman) return
    e.preventDefault()
    pegDrag.current = true
    e.currentTarget.setPointerCapture(e.pointerId)
    const el = pegRef.current
    if (el) {
      el.style.right = 'auto'
      el.style.bottom = 'auto'
      el.style.left = `${e.clientX - 14}px`
      el.style.top = `${e.clientY - 14}px`
    }
  }

  const onPegPointerMove = (e: React.PointerEvent) => {
    if (!pegDrag.current || !pegRef.current) return
    pegRef.current.style.left = `${e.clientX - 14}px`
    pegRef.current.style.top = `${e.clientY - 14}px`
  }

  const onPegPointerUp = (e: React.PointerEvent) => {
    if (!pegDrag.current) return
    pegDrag.current = false
    e.currentTarget.releasePointerCapture(e.pointerId)
    if (!viewer) {
      resetPegman(pegRef.current)
      return
    }
    const ll = pickMarsLatLon(viewer, e.clientX, e.clientY)
    if (!ll) {
      resetPegman(pegRef.current)
      setStatus('No street view here')
      return
    }
    const drop = dropOnBubbles(ll.latDeg, ll.lonDeg)
    if (!drop) {
      resetPegman(pegRef.current)
      setStatus('No street view here')
      return
    }
    releaseRover()
    setStreetDrop(drop)
    setSurfaceView('street')
    setStatus(`Street view · ${drop.world}`)
    resetPegman(pegRef.current)
  }

  const leaveStreet = () => {
    setStreetDrop(null)
    setSurfaceView('planet')
    resetPegman(pegRef.current)
  }

  const driveAt = (lat: number, lon: number, title: string) => {
    if (!viewer) return
    const drop = dropOnBubbles(lat, lon)
    if (!drop) {
      setStatus('No street view here')
      return
    }
    lookAtBubble(viewer, lat, lon, 15000)
    releaseRover()
    setStreetDrop(drop)
    setSurfaceView('street')
    setCameraMode('planet')
    setStatus(`Street view · ${title}`)
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

  return (
    <div className="app">
      <div ref={box} className={`cesium${surfaceView === 'street' ? ' cesium--hidden' : ''}`} />
      {surfaceView === 'street' && streetDrop && (
        <>
          <iframe ref={streetFrame} className="street-frame" title="Rover street view" src={sandboxSrc} tabIndex={0} />
          <button type="button" className="map-back" onClick={leaveStreet}>Map</button>
        </>
      )}
      {showPegman && (
        <div
          ref={pegRef}
          className="pegman"
          role="img"
          aria-label="Drag to street view"
          onPointerDown={onPegPointerDown}
          onPointerMove={onPegPointerMove}
          onPointerUp={onPegPointerUp}
          onPointerCancel={onPegPointerUp}
        />
      )}
      {surfaceView !== 'street' && <div className="panel">
        <b>{cameraMode === 'planet' ? 'Ares Atlas — Mars' : 'Ares Atlas — Jezero'}</b>
        {cameraMode === 'planet' && (
          <>
            <small>One window. Drive enters the rover on that patch. Drag the yellow pegman onto a patch to do the same.</small>
            {BUBBLES.map((bubble) => (
              <div className="row" key={bubble.id}>
                <button type="button" disabled={!viewer} onClick={() => viewer && lookAtBubble(viewer, bubble.lat, bubble.lon, 20000)}>
                  {bubble.title}
                </button>
                <button type="button" disabled={!viewer} onClick={() => driveAt(bubble.lat, bubble.lon, bubble.title)}>
                  Drive
                </button>
              </div>
            ))}
          </>
        )}
        {cameraMode !== 'planet' && <div className="row">
          {(['orbit', 'site', 'ground', 'rover', 'planet'] as Mode[]).map((m) => (
            <button key={m} disabled={!viewer} onClick={() => {
              fly(viewer!, site!, m, 2)
              setCameraMode(m)
            }}>
              {m}
            </button>
          ))}
        </div>}
        {cameraMode === 'planet' && (
          <button type="button" disabled={!viewer || !site} onClick={() => {
            fly(viewer!, site!, 'site', 2)
            setCameraMode('site')
          }}>Site tools</button>
        )}
        {cameraMode !== 'planet' && <>
        <input className="search" placeholder="Search: sol 120, Jezero, Belva…" value={query}
          onChange={(e) => search(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && hits[0]) goTo(hits[0]) }} />
        {hits.map((h) => (
          <button key={h.id} className="hit" onClick={() => goTo(h)} title={h.source}>
            {h.name} <small>{h.kind === 'waypoint' ? 'rover waypoint → drive from here' : 'IAU/USGS name'}</small>
          </button>
        ))}
        {site?.ctx?.manifest.layers.map((L) => (
          <label key={'ctx_' + L.id} title={L.credit}>
            <input type="checkbox" checked={layers['ctx_' + L.id]?.on ?? false} onChange={() => toggle('ctx_' + L.id)} />
            {L.title} <small>({L.mission} {L.instrument})</small>
          </label>
        ))}
        {site?.manifest.layers.map((L) => (
          <label key={L.id} title={L.credit}>
            <input type="checkbox" checked={layers[L.id]?.on ?? false} onChange={() => toggle(L.id)} />
            {L.title} <small>({L.mission} {L.instrument})</small>
          </label>
        ))}
        <label title="NASA/JPL Mars 2020 location map (M20_traverse.json, M20_waypoints.json)">
          <input type="checkbox" checked={vec.traverse} onChange={() => toggleVec('traverse')} />
          Perseverance traverse + waypoints <small>(Mars 2020, NASA/JPL)</small>
        </label>
        </>}
        {cameraMode !== 'planet' && <label title="USGS Gazetteer of Planetary Nomenclature (IAU-approved names)">
          <input type="checkbox" checked={vec.names} onChange={() => toggleVec('names')} />
          Place names <small>(IAU/USGS)</small>
        </label>}
        {cameraMode !== 'planet' && <details className="help"><summary>Controls</summary>
          <small>Ground: drag = look · Orbit/site: drag = rotate, right-drag = tilt · wheel = zoom<br />Rover: W/S drive, A/D turn (limits are assumed values)</small>
        </details>}
        <small>{drive}</small>
        <small>{status}</small>
        <small>{hud}</small>
      </div>}
      <AtlasDock
        viewer={viewer}
        site={site}
        getRover={() => roverCtl}
        cameraMode={cameraMode}
        onEnterSite={() => {
          if (!viewer || !site) return
          fly(viewer, site, 'site', 2.5)
          setCameraMode('site')
        }}
        street={surfaceView === 'street' && streetDrop ? { world: streetDrop.world, frame: streetFrame } : null}
      />
    </div>
  )
}

/** Mouse controls per camera mode. Ground = first-person look; orbit/site = rotate planet + tilt. */
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
      position: Cesium.Cartesian3.fromDegrees(lon, bubble.lat, 0, SPHERE),
      point: {
        pixelSize: 10,
        color: Cesium.Color.fromCssColorString('#ffcc00'),
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 2,
      },
      label: {
        text: bubble.title,
        font: 'bold 16px sans-serif',
        fillColor: Cesium.Color.WHITE,
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 4,
        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
        pixelOffset: new Cesium.Cartesian2(0, -12),
        scaleByDistance: new Cesium.NearFarScalar(5e4, 1.4, 1.2e7, 0.55),
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
  } else if (mode === 'orbit') {
    v.camera.flyTo({
      destination: Cesium.Cartesian3.fromDegrees(lon0, lat0, 4_000_000, SPHERE),
      orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 },
      duration,
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
