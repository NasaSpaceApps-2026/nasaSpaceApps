import { useEffect, useRef, useState } from 'react'
import * as Cesium from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'
import { Site, SPHERE, deg } from './site'
import { siteImagery, siteTerrain } from './providers'
import './App.css'

const MARS_ION_ASSET = 3644333
type Mode = 'orbit' | 'site' | 'ground'

export default function App() {
  const box = useRef<HTMLDivElement>(null)
  const [site, setSite] = useState<Site | null>(null)
  const [viewer, setViewer] = useState<Cesium.Viewer | null>(null)
  const [layers, setLayers] = useState<Record<string, { on: boolean; layer: Cesium.ImageryLayer }>>({})
  const [status, setStatus] = useState('loading site…')
  const [hud, setHud] = useState('')

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
      if (scene.skyAtmosphere) scene.skyAtmosphere.show = false
      scene.backgroundColor = Cesium.Color.fromCssColorString('#05060a')
      scene.fog.enabled = false
      scene.globe.depthTestAgainstTerrain = true

      // The site terrain provider is flat outside the site, and a transparent globe still writes
      // depth, which hid every part of the Mars tileset below that flat surface (the dark spots).
      // So draw the globe only inside the site rectangle, and cut the tileset out under it.
      const ring = () =>
        Cesium.Cartesian3.fromRadiansArray(
          [s.west, s.south, s.east, s.south, s.east, s.north, s.west, s.north], SPHERE)
      scene.globe.clippingPolygons = new Cesium.ClippingPolygonCollection({
        polygons: [new Cesium.ClippingPolygon({ positions: ring() })],
        inverse: true,
      })
      let tilesetRef: Cesium.Cesium3DTileset | undefined

      const next: Record<string, { on: boolean; layer: Cesium.ImageryLayer }> = {}
      for (const L of s.manifest.layers) {
        const layer = new Cesium.ImageryLayer(siteImagery(s, L.id, L.max_level))
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

  return (
    <div className="app">
      <div ref={box} className="cesium" />
      <div className="panel">
        <b>Ares Atlas — Jezero</b>
        <div className="row">
          {(['orbit', 'site', 'ground'] as Mode[]).map((m) => (
            <button key={m} disabled={!viewer} onClick={() => fly(viewer!, site!, m, 2)}>
              {m}
            </button>
          ))}
        </div>
        {site?.manifest.layers.map((L) => (
          <label key={L.id} title={L.credit}>
            <input type="checkbox" checked={layers[L.id]?.on ?? false} onChange={() => toggle(L.id)} />
            {L.title} <small>({L.mission} {L.instrument})</small>
          </label>
        ))}
        <small>{status}</small>
        <small>{hud}</small>
      </div>
    </div>
  )
}

function fly(v: Cesium.Viewer, s: Site, mode: Mode, duration: number) {
  const lat0 = s.manifest.lat0, lon0 = s.manifest.lon0
  if (mode === 'orbit') {
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
