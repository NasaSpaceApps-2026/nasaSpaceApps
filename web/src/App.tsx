import { useEffect, useRef, useState } from 'react'
import * as Cesium from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'
import { R, Site, SPHERE, deg } from './site'
import { siteImagery, siteTerrain } from './providers'
import { CesiumRover } from './cesiumRover'
import { PlacesLayer, type Place } from './places'
import './App.css'

const MARS_ION_ASSET = 3644333
type Mode = 'orbit' | 'site' | 'ground' | 'rover'
let roverCtl: CesiumRover | null = null // set once the viewer is up; used by fly()
let placesCtl: PlacesLayer | null = null

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
      scene.globe.baseColor = Cesium.Color.TRANSPARENT // outside the site the Mars tileset shows
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
      const INSET = 200
      const ring = () => {
        const dLon = INSET / (R * Math.cos(Cesium.Math.toRadians(s.manifest.lat0))), dLat = INSET / R
        return Cesium.Cartesian3.fromRadiansArray(
          [s.west + dLon, s.south + dLat, s.east - dLon, s.south + dLat,
           s.east - dLon, s.north - dLat, s.west + dLon, s.north - dLat], SPHERE)
      }
      // Globe clipping uses planes, not ClippingPolygon: polygon clipping removes whole globe tiles
      // and crashes Cesium's TerrainFillMesh (northIndicesWestToEast undefined) during flights.
      // Meridian planes are exact. Latitude edges are constant-z planes chosen so the globe can
      // never extend PAST the site (north uses the lowest terrain radius, south the highest):
      // beyond the site the globe is flat and writes depth, hiding the Mars tileset (black strip).
      // That trims up to ~(relief * tan(lat)) m inside the north/south edges, which the shrunken
      // tileset hole (INSET) covers.
      if (!new URLSearchParams(location.search).has('noglobeclip')) {
        const m = s.manifest as unknown as { elev_min: number; elev_max: number }
        const zS = (R + m.elev_max) * Math.sin(s.south), zN = (R + m.elev_min) * Math.sin(s.north)
        const w = s.west, e = s.east
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
      for (const L of s.manifest.layers) {
        const layer = new Cesium.ImageryLayer(siteImagery(s, L.id, L.max_level, L.core))
        layer.show = L.kind === 'base'
        if (L.kind === 'overlay') layer.alpha = 0.8
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
          setStatus('site + Cesium Mars globe')
        } catch (e) {
          setStatus('site only (Mars globe failed: ' + (e as Error).message + ')')
        }
      } else setStatus('site only (no Cesium token)')

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
      roverCtl = new CesiumRover(v, s)
      ;(window as unknown as { __rover?: CesiumRover }).__rover = roverCtl
      let lastHud = 0
      roverCtl.onPose = (p) => {
        const now = performance.now()
        if (now - lastHud < 250) return
        lastHud = now
        setDrive(`rover ${p.status}${p.stop_reason ? ' (' + p.stop_reason + ')' : ''} · slope ${p.slope_deg.toFixed(1)}° · ` +
          `ahead ${p.ahead_slope_deg.toFixed(1)}° · ${p.display_speed_mps.toFixed(1)} m/s shown (${p.speed_mps.toFixed(2)} real) · x ${p.x.toFixed(0)} y ${p.y.toFixed(0)}`)
      }
      // Real Perseverance traverse/waypoints and IAU names; start the rover at the first real waypoint.
      placesCtl = new PlacesLayer(v, s)
      placesCtl.load().then(() => {
        const w = placesCtl!.first()
        if (w && roverCtl) roverCtl.driver.teleport(w.x, w.y, w.heading ?? 0)
      }).catch((e) => console.error('places', e))
      setSite(s)
      setViewer(v)
      fly(v, s, (new URLSearchParams(location.search).get('view') as Mode) || 'site', 0)
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
      roverCtl?.driver.teleport(p.x, p.y, p.heading ?? 0)
      fly(viewer, site, 'rover', 0)
    } else {
      roverCtl?.setChase(false)
      viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(p.lon, p.lat, Math.max(15000, (p.diameterKm ?? 5) * 2500), SPHERE),
        orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 }, duration: 2,
      })
    }
  }

  return (
    <div className="app">
      <div ref={box} className="cesium" />
      <div className="panel">
        <b>Ares Atlas — Jezero</b>
        <div className="row">
          {(['orbit', 'site', 'ground', 'rover'] as Mode[]).map((m) => (
            <button key={m} disabled={!viewer} onClick={() => fly(viewer!, site!, m, 2)}>
              {m}
            </button>
          ))}
        </div>
        <input className="search" placeholder="Search: sol 120, Jezero, Belva…" value={query}
          onChange={(e) => search(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && hits[0]) goTo(hits[0]) }} />
        {hits.map((h) => (
          <button key={h.id} className="hit" onClick={() => goTo(h)} title={h.source}>
            {h.name} <small>{h.kind === 'waypoint' ? 'rover waypoint → drive from here' : 'IAU/USGS name'}</small>
          </button>
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
        <label title="USGS Gazetteer of Planetary Nomenclature (IAU-approved names)">
          <input type="checkbox" checked={vec.names} onChange={() => toggleVec('names')} />
          Place names <small>(IAU/USGS)</small>
        </label>
        <small>Ground: drag = look · Orbit/site: drag = rotate, right-drag = tilt · wheel = zoom</small>
        <small>Rover: W/S drive, A/D turn (limits are assumed values)</small>
        <small>{drive}</small>
        <small>{status}</small>
        <small>{hud}</small>
      </div>
    </div>
  )
}

/** Mouse controls per camera mode. Ground = first-person look; orbit/site = rotate planet + tilt. */
function applyControls(v: Cesium.Viewer, mode: Mode) {
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

function fly(v: Cesium.Viewer, s: Site, mode: Mode, duration: number) {
  const lat0 = s.manifest.lat0, lon0 = s.manifest.lon0
  applyControls(v, String(mode).startsWith('alt:') ? 'site' : mode)
  if (mode === 'rover') return // chase camera is driven by the rover each frame
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
    const target = s.position(0, 0, 0)
    v.camera.flyToBoundingSphere(new Cesium.BoundingSphere(target, 1500), {
      offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-35), 9000),
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
