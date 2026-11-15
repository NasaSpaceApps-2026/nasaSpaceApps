// Drives the teammate's rover physics (src/rover) on OUR Mars world and draws it in Cesium.
// The rover module is plain TS in site metres; we feed it our real 2 m DEM through HeightSampler and
// render its pose as Cesium box entities (the module's own three.js model is not used here).
// Imports are direct file paths on purpose: the barrel (rover/index.ts) pulls in three.js files.
import * as Cesium from 'cesium'
import { RoverDriver, type DriverPose } from './rover/driver'
import { KeyboardControls } from './rover/controls'
import { RemotePoseInterpolator, type RemoteRoverState } from './rover/link'
import { DEFAULT_ROVER_PROFILE } from './rover/physics'
import { SPHERE, type Site } from './site'

const D2R = Math.PI / 180

interface Part {
  size: [number, number, number] // forward, left, up (metres)
  at: [number, number, number] // offset: forward, left, up from the ground contact point
  color: Cesium.Color
  entity: Cesium.Entity
  pos: Cesium.Cartesian3 // current world position/orientation, read by the entity callbacks each frame
  q: Cesium.Quaternion
}

const C = Cesium.Color
const PARTS: Omit<Part, 'entity' | 'pos' | 'q'>[] = [
  { size: [3.0, 2.2, 0.7], at: [0, 0, 0.95], color: C.fromCssColorString('#d8d4cc') }, // body
  { size: [0.7, 0.55, 0.55], at: [-1.7, 0, 1.0], color: C.fromCssColorString('#6b6b70') }, // RTG
  { size: [0.12, 0.12, 1.3], at: [1.0, 0.6, 2.0], color: C.fromCssColorString('#9a9aa0') }, // mast
  { size: [0.35, 0.55, 0.28], at: [1.0, 0.6, 2.75], color: C.fromCssColorString('#f0f0f0') }, // head
  { size: [1.1, 0.2, 0.2], at: [1.9, -0.5, 0.8], color: C.fromCssColorString('#9a9aa0') }, // arm
  ...[1.3, 0, -1.3].flatMap((n) =>
    [1.25, -1.25].map((l) => ({ size: [0.6, 0.4, 0.6] as [number, number, number], at: [n, l, 0.3] as [number, number, number],
      color: C.fromCssColorString('#2c2c30') })),
  ), // six wheels
]

export class CesiumRover {
  readonly driver: RoverDriver
  readonly controls: KeyboardControls
  chase = false
  onPose?: (p: DriverPose) => void
  private parts: Part[]
  private last = performance.now()
  private smoothHeading = 0
  private remote = new RemotePoseInterpolator(150)
  private remoteAt = -1e9
  private off: () => void
  private viewer: Cesium.Viewer
  private site: Site

  constructor(viewer: Cesium.Viewer, site: Site, start = { x: 0, y: -150, heading_deg: 0 }) {
    this.viewer = viewer
    this.site = site
    this.controls = new KeyboardControls()
    this.driver = new RoverDriver({
      sampler: {
        sampleHeight: (x, y) => site.elevationAtXY(x, y),
        contains: (x, y) => !Number.isNaN(site.elevationAtXY(x, y)),
      },
      profile: DEFAULT_ROVER_PROFILE,
      controls: this.controls,
      link: null, // API WebSocket link not wired yet
      start,
    })
    this.smoothHeading = start.heading_deg
    // Never start an entity at the ECEF origin (planet centre): Cesium cannot project it and crashes.
    const home = site.position(start.x, start.y, 1)
    // Dynamic (callback) properties: Cesium re-reads them every frame. Reassigning constant
    // properties instead forces a static-batch rebuild each frame, which is slow and unreliable.
    this.parts = PARTS.map((p) => {
      const part = { ...p, pos: home, q: Cesium.Quaternion.clone(Cesium.Quaternion.IDENTITY) } as Part
      part.entity = viewer.entities.add({
        position: new Cesium.CallbackPositionProperty(() => part.pos, false),
        orientation: new Cesium.CallbackProperty(() => part.q, false) as unknown as Cesium.Property,
        box: { dimensions: new Cesium.Cartesian3(...p.size), material: p.color },
      })
      return part
    })
    const cb = () => this.tick()
    viewer.scene.preRender.addEventListener(cb)
    this.off = () => viewer.scene.preRender.removeEventListener(cb)
  }

  /** An API-driven (auto/fsd) server state: rendered interpolated (10 Hz updates become smooth motion). */
  pushRemote(s: RemoteRoverState) {
    this.remote.push(s, performance.now())
    this.remoteAt = performance.now()
  }

  setChase(on: boolean) {
    if (this.chase && !on) this.viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY)
    this.chase = on
  }

  dispose() {
    this.off()
    this.controls.dispose()
    this.parts.forEach((p) => this.viewer.entities.remove(p.entity))
  }

  /** World axes (ECEF) of the rover body from its site-frame pose. */
  private frame(p: DriverPose) {
    const h = p.heading_deg * D2R, pi = p.pitch_deg * D2R, ro = p.roll_deg * D2R
    // in local east/north/up: forward, right, up with heading cw from north, pitch nose-up, roll right-down
    let f = [Math.sin(h), Math.cos(h), 0]
    let r = [Math.cos(h), -Math.sin(h), 0]
    let u = [0, 0, 1]
    const comb = (a: number[], x: number, b: number[], y: number) => a.map((_, i) => a[i] * x + b[i] * y)
    const f1 = comb(f, Math.cos(pi), u, Math.sin(pi))
    const u1 = comb(u, Math.cos(pi), f, -Math.sin(pi))
    r = comb(r, Math.cos(ro), u1, -Math.sin(ro))
    u = comb(u1, Math.cos(ro), r, Math.sin(ro))
    f = f1
    const origin = this.site.position(p.x, p.y, 0)
    const enu = Cesium.Transforms.eastNorthUpToFixedFrame(origin, SPHERE)
    const m3 = Cesium.Matrix4.getMatrix3(enu, new Cesium.Matrix3())
    const w = (v: number[]) => Cesium.Matrix3.multiplyByVector(m3, new Cesium.Cartesian3(v[0], v[1], v[2]), new Cesium.Cartesian3())
    const fw = w(f), lw = Cesium.Cartesian3.negate(w(r), new Cesium.Cartesian3()), uw = w(u)
    return { origin, fw, lw, uw }
  }

  private tick() {
    const now = performance.now()
    const dt = Math.min(0.1, (now - this.last) / 1000)
    this.last = now
    if (now - this.remoteAt < 700) {
      const r = this.remote.sample(now)
      if (r) this.driver.teleport(r.x, r.y, r.heading_deg)
    }
    const p = this.driver.step(dt)
    // Use the sampled ground height so the rover and the terrain provider agree exactly.
    const { origin, fw, lw, uw } = this.frame(p)
    const rot = Cesium.Matrix3.fromColumnMajorArray([fw.x, fw.y, fw.z, lw.x, lw.y, lw.z, uw.x, uw.y, uw.z])
    const q = Cesium.Quaternion.fromRotationMatrix(rot)
    for (const part of this.parts) {
      const pos = Cesium.Cartesian3.clone(origin)
      Cesium.Cartesian3.add(pos, Cesium.Cartesian3.multiplyByScalar(fw, part.at[0], new Cesium.Cartesian3()), pos)
      Cesium.Cartesian3.add(pos, Cesium.Cartesian3.multiplyByScalar(lw, part.at[1], new Cesium.Cartesian3()), pos)
      Cesium.Cartesian3.add(pos, Cesium.Cartesian3.multiplyByScalar(uw, part.at[2], new Cesium.Cartesian3()), pos)
      part.pos = pos
      part.q = q
    }
    if (this.chase) {
      // ease the camera heading toward the rover's so turns feel smooth
      const d = ((p.heading_deg - this.smoothHeading + 540) % 360) - 180
      this.smoothHeading += d * Math.min(1, dt * 4)
      const target = Cesium.Cartesian3.add(origin, Cesium.Cartesian3.multiplyByScalar(uw, 1.4, new Cesium.Cartesian3()), new Cesium.Cartesian3())
      this.viewer.camera.lookAt(target, new Cesium.HeadingPitchRange(this.smoothHeading * D2R, -18 * D2R, 14))
    }
    this.onPose?.(p)
  }
}
