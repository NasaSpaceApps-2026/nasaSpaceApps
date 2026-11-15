// Draws copilot results on the teammate's Cesium viewer and moves its camera. All coordinate maths goes through
// the viewer's own Site helpers (xyToLonLat, lonLatToXY, position), nothing is duplicated here.
import * as Cesium from 'cesium';
import { SPHERE, type Site } from '../../site';
import type { CesiumRover } from '../../cesiumRover';
import type { Feature, FeatureKind, RouteLine } from './features';

const COLORS: Record<FeatureKind, string> = {
  spot: '#5dffa0', region: '#ffcf5a', poi: '#5cd6ff', marker: '#ff7ad9', stop: '#c9a0ff', target: '#ffffff',
};
const ROUTE_COLOR = '#35e0ff';
const LABEL_BG = Cesium.Color.fromCssColorString('#14100e').withAlpha(0.85);
const FONT = '600 12px system-ui, sans-serif';
const MAX_LABELS = 4; // labels shown at once besides the hovered / selected one
const LABEL_H = 24;

interface LabelEntry { label: Cesium.Label; position: Cesium.Cartesian3; w: number; ownsPoint: boolean; order: number }
interface Rect { x0: number; y0: number; x1: number; y1: number }
const hit = (a: Rect, b: Rect) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
const PLACEMENTS = [
  { h: Cesium.HorizontalOrigin.CENTER, v: Cesium.VerticalOrigin.BOTTOM, dx: 0, dy: -14 },
  { h: Cesium.HorizontalOrigin.CENTER, v: Cesium.VerticalOrigin.TOP, dx: 0, dy: 14 },
  { h: Cesium.HorizontalOrigin.LEFT, v: Cesium.VerticalOrigin.CENTER, dx: 14, dy: 0 },
  { h: Cesium.HorizontalOrigin.RIGHT, v: Cesium.VerticalOrigin.CENTER, dx: -14, dy: 0 },
];
let measureCtx: CanvasRenderingContext2D | null | undefined;
function textWidth(text: string, font = FONT): number {
  if (measureCtx === undefined) measureCtx = typeof document !== 'undefined' ? document.createElement('canvas').getContext('2d') : null;
  if (!measureCtx) return text.length * 7;
  measureCtx.font = font;
  return measureCtx.measureText(text).width;
}
function rectAt(x: number, y: number, w: number, h: number, ho: Cesium.HorizontalOrigin, vo: Cesium.VerticalOrigin): Rect {
  const x0 = ho === Cesium.HorizontalOrigin.LEFT ? x : ho === Cesium.HorizontalOrigin.RIGHT ? x - w : x - w / 2;
  const y0 = vo === Cesium.VerticalOrigin.BOTTOM ? y - h : vo === Cesium.VerticalOrigin.TOP ? y : y - h / 2;
  return { x0, y0, x1: x0 + w, y1: y0 + h };
}

export interface FlyTarget { x: number; y: number; mode?: string; distance_m?: number; heading_deg?: number }

export class CopilotOverlay {
  readonly ds = new Cesium.CustomDataSource('atlas-copilot');
  private features = new Map<string, Feature>(); // every feature ever seen, so view highlights by id can be resolved
  private shown = new Set<string>();
  private routes: RouteLine[] = [];
  /** Labels live in our own LabelCollection so a declutter pass (below) can show only the ones that fit. */
  private labels: Cesium.LabelCollection;
  private entries = new Map<string, LabelEntry>();
  private hovered: string | null = null;
  private selected: string | null = null;
  private lastDeclutter = 0;
  private beaconShown = true;
  private offPost: (() => void) | null = null;

  private viewer: Cesium.Viewer;
  private site: Site;
  private getRover: () => CesiumRover | null;

  /** `getRover` is a getter because the viewer creates and releases its rover as the camera mode changes. */
  constructor(viewer: Cesium.Viewer, site: Site, getRover: () => CesiumRover | null) {
    this.viewer = viewer;
    this.site = site;
    this.getRover = getRover;
    void viewer.dataSources.add(this.ds);
    this.labels = viewer.scene.primitives.add(new Cesium.LabelCollection({ scene: viewer.scene })) as Cesium.LabelCollection;
    const rm = viewer.scene.postRender.addEventListener(() => this.declutter());
    this.offPost = () => rm();
    {
      // Always-visible beacon on the (agent- or keyboard-driven) rover: its 3 m model is invisible from orbit distances.
      this.ds.entities.add({
        id: 'rover-beacon',
        position: new Cesium.CallbackPositionProperty(() => {
          const p = this.getRover()?.driver.pose;
          return p ? this.site.position(p.x, p.y, 6) : undefined;
        }, false),
        point: { pixelSize: 11, color: Cesium.Color.WHITE, outlineColor: Cesium.Color.fromCssColorString('#e0794a'), outlineWidth: 3, disableDepthTestDistance: Number.POSITIVE_INFINITY },
        label: { text: 'rover', font: '600 12px system-ui, sans-serif', fillColor: Cesium.Color.WHITE, showBackground: true, backgroundColor: LABEL_BG, backgroundPadding: new Cesium.Cartesian2(5, 3),
          style: Cesium.LabelStyle.FILL, horizontalOrigin: Cesium.HorizontalOrigin.CENTER, verticalOrigin: Cesium.VerticalOrigin.BOTTOM, pixelOffset: new Cesium.Cartesian2(0, -14),
          disableDepthTestDistance: Number.POSITIVE_INFINITY },
      });
    }
  }

  dispose() {
    this.offPost?.();
    if (!this.viewer.isDestroyed()) {
      this.viewer.dataSources.remove(this.ds, true);
      this.viewer.scene.primitives.remove(this.labels);
    }
  }

  get count() {
    return this.shown.size + this.routes.length;
  }
  /** Distinct tools whose results are currently drawn. */
  sources(): string[] {
    const s = new Set<string>();
    for (const id of this.shown) s.add(this.features.get(id)!.source);
    for (const r of this.routes) s.add(r.source);
    return [...s];
  }
  known(id: string): Feature | undefined {
    return this.features.get(id);
  }
  remember(fs: Feature[]) {
    for (const f of fs) this.features.set(f.id, f);
  }

  /** Terrain-draped point for site metres. Falls back to height 0 outside the data (site.position does that). */
  private at(x: number, y: number, up: number) {
    return this.site.position(x, y, up);
  }

  private ring(f: Feature, color: Cesium.Color) {
    const r = f.radius ?? 0;
    if (r < 3) return;
    const n = 48, pos: Cesium.Cartesian3[] = [];
    for (let i = 0; i <= n; i++) {
      const a = (2 * Math.PI * i) / n;
      pos.push(this.at(f.x + r * Math.cos(a), f.y + r * Math.sin(a), 1.5));
    }
    this.ds.entities.add({ id: `ring:${f.id}`, polyline: { positions: pos, width: 5, arcType: Cesium.ArcType.NONE,
      material: new Cesium.PolylineOutlineMaterialProperty({ color, outlineWidth: 2, outlineColor: Cesium.Color.BLACK.withAlpha(0.85) }) } });
  }

  show(fs: Feature[]) {
    this.remember(fs);
    fs.forEach((f, i) => {
      if (this.shown.has(f.id)) this.hide(f.id);
      const color = Cesium.Color.fromCssColorString(COLORS[f.kind]);
      const position = this.at(f.x, f.y, 3);
      // soft halo under the marker so it pops against bright imagery
      this.ds.entities.add({
        id: `halo:${f.id}`,
        position,
        point: { pixelSize: 34, color: color.withAlpha(0.28), outlineColor: color.withAlpha(0.7), outlineWidth: 2, disableDepthTestDistance: Number.POSITIVE_INFINITY },
      });
      this.ds.entities.add({
        id: `feat:${f.id}`,
        name: f.label,
        position,
        point: { pixelSize: 14, color, outlineColor: Cesium.Color.WHITE, outlineWidth: 3, disableDepthTestDistance: Number.POSITIVE_INFINITY },
      });
      this.addLabel(`feat:${f.id}`, f.label, position, true, 10 + i);
      this.ring(f, color);
      this.shown.add(f.id);
    });
  }

  private addLabel(key: string, text: string, position: Cesium.Cartesian3, ownsPoint: boolean, order: number) {
    this.removeLabel(key);
    const label = this.labels.add({
      position, text, font: FONT, fillColor: Cesium.Color.WHITE, showBackground: true, backgroundColor: LABEL_BG, backgroundPadding: new Cesium.Cartesian2(6, 4),
      style: Cesium.LabelStyle.FILL, verticalOrigin: Cesium.VerticalOrigin.BOTTOM, horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
      pixelOffset: new Cesium.Cartesian2(0, -14), disableDepthTestDistance: Number.POSITIVE_INFINITY,
      distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 40000), show: false,
    });
    this.entries.set(key, { label, position, w: textWidth(text) + 16, ownsPoint, order });
  }

  private removeLabel(key: string) {
    const e = this.entries.get(key);
    if (e) {
      this.labels.remove(e.label);
      this.entries.delete(key);
    }
  }

  /** Hover / selection pin a label even beyond the top-N cap. */
  setHover(id: string | null) {
    this.hovered = id;
  }
  setSelected(id: string | null) {
    this.selected = id;
  }

  /**
   * Screen-space declutter, run after render (throttled): shows labels in priority order (selected, hovered, routes, then
   * result rank) while a label (a) is on screen, (b) fits in one of four placements without touching an already shown
   * label, any other marker, the rover badge, or a label of the viewer's own (places / waypoints), and (c) stays within
   * the top MAX_LABELS. Everything else stays hidden until hovered or selected.
   */
  private declutter() {
    const now = performance.now();
    if (now - this.lastDeclutter < 120 || this.viewer.isDestroyed()) return;
    this.lastDeclutter = now;
    const scene = this.viewer.scene;
    const W = scene.canvas.clientWidth, H = scene.canvas.clientHeight;
    const toWin = (p: Cesium.Cartesian3) => Cesium.SceneTransforms.worldToWindowCoordinates(scene, p);
    const inView = (c: Cesium.Cartesian2 | undefined): c is Cesium.Cartesian2 => !!c && c.x > 8 && c.y > 8 && c.x < W - 8 && c.y < H - 8;

    // Obstacles: the viewer's own labels, every marker of ours, the rover badge.
    const viewerLabels: Rect[] = [];
    const prims = scene.primitives;
    for (let i = 0; i < prims.length; i++) {
      const c = prims.get(i);
      if (!(c instanceof Cesium.LabelCollection) || c === this.labels || !c.show) continue;
      for (let j = 0; j < c.length; j++) {
        const l = c.get(j);
        if (!l.show || (l.id instanceof Cesium.Entity && l.id.entityCollection === this.ds.entities)) continue;
        let sp: Cesium.Cartesian2 | undefined;
        try { sp = l.computeScreenSpacePosition(scene); } catch { sp = undefined; }
        if (!sp) continue;
        const px = /(\d+(?:\.\d+)?)px/.exec(l.font);
        const fs = (px ? Number(px[1]) : 16) * l.scale;
        viewerLabels.push(rectAt(sp.x, sp.y, textWidth(l.text, l.font) * l.scale + 8, fs * 1.4, l.horizontalOrigin, l.verticalOrigin));
      }
    }
    const markers: { id: string; r: Rect }[] = [];
    for (const [key, e] of this.entries) {
      if (!e.ownsPoint) continue;
      const c = toWin(e.position);
      if (c) markers.push({ id: key, r: { x0: c.x - 9, y0: c.y - 9, x1: c.x + 9, y1: c.y + 9 } });
    }
    const badge: Rect[] = [];
    const beacon = this.ds.entities.getById('rover-beacon');
    if (beacon) {
      const bp = beacon.position?.getValue(scene.lastRenderTime ?? Cesium.JulianDate.now());
      const c = bp && toWin(bp);
      if (c) {
        badge.push({ x0: c.x - 10, y0: c.y - 10, x1: c.x + 10, y1: c.y + 10 });
        // the "rover" tag itself is hidden whenever it would sit on one of the viewer's labels
        const tag = rectAt(c.x, c.y - 14, textWidth('rover') + 14, 20, Cesium.HorizontalOrigin.CENTER, Cesium.VerticalOrigin.BOTTOM);
        const ok = !viewerLabels.some((r) => hit(r, tag));
        if (ok) badge.push(tag);
        if (ok !== this.beaconShown && beacon.label) {
          this.beaconShown = ok;
          beacon.label.show = new Cesium.ConstantProperty(ok);
        }
      }
    }

    const placed: Rect[] = [];
    const order = [...this.entries.entries()].sort((a, b) => {
      const pa = a[0] === `feat:${this.selected}` ? -2 : a[0] === `feat:${this.hovered}` ? -1 : a[1].order;
      const pb = b[0] === `feat:${this.selected}` ? -2 : b[0] === `feat:${this.hovered}` ? -1 : b[1].order;
      return pa - pb;
    });
    let n = 0;
    for (const [key, e] of order) {
      const pinned = key === `feat:${this.selected}` || key === `feat:${this.hovered}`;
      let showIt = false;
      const c = toWin(e.position);
      if (inView(c) && (pinned || n < MAX_LABELS)) {
        for (const pl of PLACEMENTS) {
          const r = rectAt(c.x + pl.dx, c.y + pl.dy, e.w, LABEL_H, pl.h, pl.v);
          if (r.x0 < 4 || r.y0 < 4 || r.x1 > W - 4 || r.y1 > H - 4) continue;
          if (placed.some((p) => hit(p, r)) || viewerLabels.some((p) => hit(p, r)) || badge.some((p) => hit(p, r))) continue;
          if (!pinned && markers.some((m) => m.id !== key && hit(m.r, r))) continue;
          e.label.horizontalOrigin = pl.h;
          e.label.verticalOrigin = pl.v;
          e.label.pixelOffset = new Cesium.Cartesian2(pl.dx, pl.dy);
          placed.push(r);
          showIt = true;
          break;
        }
      }
      if (showIt && !pinned) n++;
      if (e.label.show !== showIt) e.label.show = showIt;
    }
  }

  showRoutes(rs: RouteLine[]) {
    for (const r of rs) {
      this.routes = this.routes.filter((o) => o.id !== r.id);
      this.ds.entities.removeById(`route:${r.id}`);
      this.removeLabel(`routelabel:${r.id}`);
      this.routes.push(r);
      this.ds.entities.add({
        id: `route:${r.id}`,
        polyline: {
          positions: r.points.map(([x, y]) => this.at(x, y, 1.2)), width: 6, arcType: Cesium.ArcType.NONE,
          material: new Cesium.PolylineOutlineMaterialProperty({ color: Cesium.Color.fromCssColorString(ROUTE_COLOR), outlineWidth: 2, outlineColor: Cesium.Color.BLACK }),
        },
      });
      const mid = r.points[Math.floor(r.points.length / 2)];
      this.addLabel(`routelabel:${r.id}`, r.label, this.at(mid[0], mid[1], 4), false, 0);
    }
  }

  hide(id: string) {
    this.ds.entities.removeById(`feat:${id}`);
    this.ds.entities.removeById(`ring:${id}`);
    this.ds.entities.removeById(`halo:${id}`);
    this.removeLabel(`feat:${id}`);
    this.shown.delete(id);
  }

  clear() {
    for (const e of [...this.ds.entities.values]) if (e.id !== 'rover-beacon') this.ds.entities.remove(e);
    this.labels.removeAll();
    this.entries.clear();
    this.hovered = this.selected = null;
    this.shown.clear();
    this.routes = [];
  }

  /**
   * Screen-space hit test for our highlights: a marker within `px` pixels, else a route within 8 px. Used next to scene.pick,
   * which misses thin or depth-test-free primitives on some GPUs/software renderers.
   */
  hitAt(pos: Cesium.Cartesian2, px = 16): { feature?: Feature; route?: RouteLine } | undefined {
    const scene = this.viewer.scene;
    let best: { f: Feature; d: number } | undefined;
    for (const id of this.shown) {
      const f = this.features.get(id);
      const w = f && Cesium.SceneTransforms.worldToWindowCoordinates(scene, this.at(f.x, f.y, 3));
      if (!f || !w) continue;
      const d = Math.hypot(w.x - pos.x, w.y - pos.y);
      if (d <= px && (!best || d < best.d)) best = { f, d };
    }
    if (best) return { feature: best.f };
    for (const r of this.routes) {
      let prev: Cesium.Cartesian2 | undefined;
      const step = Math.max(1, Math.floor(r.points.length / 200));
      for (let i = 0; i < r.points.length; i += step) {
        const w = Cesium.SceneTransforms.worldToWindowCoordinates(scene, this.at(r.points[i][0], r.points[i][1], 1.2));
        if (w && prev) {
          const dx = w.x - prev.x, dy = w.y - prev.y, L2 = dx * dx + dy * dy;
          const u = L2 ? Math.max(0, Math.min(1, ((pos.x - prev.x) * dx + (pos.y - prev.y) * dy) / L2)) : 0;
          if (Math.hypot(pos.x - (prev.x + u * dx), pos.y - (prev.y + u * dy)) <= 8) return { route: r };
        }
        prev = w;
      }
    }
    return undefined;
  }

  /** Route drawn under this entity id (`route:<id>`), when the picked object is one of ours. */
  routeFromPick(picked: unknown): RouteLine | undefined {
    const id = (picked as { id?: Cesium.Entity } | undefined)?.id?.id;
    return typeof id === 'string' && id.startsWith('route:') ? this.routes.find((r) => `route:${r.id}` === id) : undefined;
  }

  /** Entity id -> feature id when the picked object is one of ours. */
  featureIdFromPick(picked: unknown): string | undefined {
    const id = (picked as { id?: Cesium.Entity } | undefined)?.id?.id;
    return typeof id === 'string' && id.startsWith('feat:') ? id.slice(5) : undefined;
  }

  /** Camera: look at a site-frame point. Same style as the viewer's own presets (flyToBoundingSphere, heading 0). */
  flyTo(t: FlyTarget, duration = 2.5) {
    const cam = this.viewer.camera;
    const mode = t.mode ?? 'orbit';
    const rv = this.getRover();
    if (mode === 'chase' && rv) {
      rv.setChase(true);
      return;
    }
    this.getRover()?.setChase(false);
    if (mode === 'globe') {
      const [lon, lat] = this.site.xyToLonLat(t.x, t.y);
      cam.flyTo({ destination: Cesium.Cartesian3.fromRadians(lon, lat, t.distance_m ?? 400_000, SPHERE), orientation: { heading: 0, pitch: -Math.PI / 2, roll: 0 }, duration });
      return;
    }
    if (mode === 'ground') {
      const [lon, lat] = this.site.xyToLonLat(t.x, t.y - 20);
      const h = this.site.elevation(lon, lat);
      cam.flyTo({ destination: Cesium.Cartesian3.fromRadians(lon, lat, (Number.isNaN(h) ? 0 : h) + 1.7, SPHERE), orientation: { heading: Cesium.Math.toRadians(t.heading_deg ?? 0), pitch: Cesium.Math.toRadians(-4), roll: 0 }, duration });
      return;
    }
    const range = Math.min(20000, Math.max(80, t.distance_m ?? 900));
    cam.flyToBoundingSphere(new Cesium.BoundingSphere(this.at(t.x, t.y, 0), Math.min(range / 4, 300)), {
      offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-40), range), duration,
    });
  }

  /** Fit a set of features (and routes) in view. */
  fit(fs: Feature[], rs: RouteLine[] = [], duration = 2.5) {
    const pts = [...fs.map((f) => this.at(f.x, f.y, 0)), ...rs.flatMap((r) => r.points.filter((_, i) => i % 5 === 0).map(([x, y]) => this.at(x, y, 0)))];
    if (!pts.length) return;
    this.getRover()?.setChase(false);
    const bs = Cesium.BoundingSphere.fromPoints(pts);
    this.viewer.camera.flyToBoundingSphere(bs, {
      offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-45), Math.min(25000, Math.max(500, bs.radius * 2.6 + 150))), duration,
    });
  }

  /** The site-frame point the camera is looking at (screen centre on the terrain), plus its altitude. */
  cameraState() {
    const cam = this.viewer.camera, scene = this.viewer.scene;
    const c = cam.positionCartographic;
    const [cx, cy] = this.site.lonLatToXY(c.longitude, c.latitude);
    let looking: { x: number; y: number } | null = null;
    try {
      const canvas = scene.canvas;
      const hit = scene.pickPosition(new Cesium.Cartesian2(canvas.clientWidth / 2, canvas.clientHeight / 2)) ?? scene.globe.pick(cam.getPickRay(new Cesium.Cartesian2(canvas.clientWidth / 2, canvas.clientHeight / 2))!, scene);
      if (hit) {
        const g = Cesium.Cartographic.fromCartesian(hit, SPHERE);
        const [lx, ly] = this.site.lonLatToXY(g.longitude, g.latitude);
        looking = { x: Math.round(lx), y: Math.round(ly) };
      }
    } catch {
      /* pick can fail before the first frame */
    }
    return { x: Math.round(cx), y: Math.round(cy), altitude_m: Math.round(c.height), heading_deg: Math.round(Cesium.Math.toDegrees(cam.heading)), looking_at: looking };
  }
}
