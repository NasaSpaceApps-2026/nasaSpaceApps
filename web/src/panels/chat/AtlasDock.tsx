// Right-hand dock: the chat copilot wired to the Cesium viewer.
//  - tool results (find_*, search_pois, routes, ...) are drawn on the viewer and the camera flies to them;
//  - the API WebSocket (view.camera, marker.add, rover.state) is followed so agent camera moves and rover drives show;
//  - getContext reports the camera, the rover pose and the selected feature to the agent.
// Mounted from App.tsx with 3 props, see INTEGRATION.md. Everything here is ours; nothing global is touched except
// the viewer container's right margin (so the globe is not hidden under the dock), restored on unmount.
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import * as Cesium from 'cesium';
import type { Site } from '../../site';
import type { CesiumRover } from '../../cesiumRover';
import { BUBBLES } from '../../bubbles';
import { RoverLink } from '../../rover/link';
import { ChatPanel } from './ChatPanel';
import type { ToolEvent } from './types';
import { listMarkers } from '../../interact/api';
import { extractFromResult, focusFromCamera, thin, xyOf, type Extracted, type Feature, type RouteLine } from './features';
import { CopilotOverlay } from './overlay';
import './dock.css';

export interface AtlasDockProps {
  viewer: Cesium.Viewer | null;
  site: Site | null;
  /** The viewer creates/releases its rover as the camera mode changes, so this is a getter, not a value. */
  getRover: () => CesiumRover | null;
  /** The viewer's camera mode ('planet' | 'site' | 'orbit' | 'ground' | 'rover'). */
  cameraMode: string;
  /** Called before a tool flies the camera while the planet globe is showing: switch the viewer to the site. */
  onEnterSite: () => void;
  /** Street view (the sandbox iframe) is showing: which world, and its iframe so the dock can mirror the API rover into it. */
  street: { world: string; frame: RefObject<HTMLIFrameElement | null> } | null;
  /** Rover drive progress for the viewer's chip and auto-follow (null when no state yet). */
  onDrive?: (d: DriveInfo) => void;
  /** The street-view note's "watch in Jezero" button. */
  onWatch?: () => void;
  agentUrl?: string;
  apiWsUrl?: string;
  /** A highlight (copilot pad/region/route/POI/marker) was hovered or clicked on the map. Canvas px. */
  onHighlight?: (h: { feature?: Feature; route?: RouteLine } | null, kind: 'click' | 'hover', sx: number, sy: number) => void;
  /** Backend `view.camera` with mode "planet" and lat/lon: the viewer flies the globe there. */
  onPlanetCamera?: (c: { lat: number; lon: number; height_m?: number; label?: string }) => void;
  /** The copilot started a drive (drive_to / goto / follow_route...): this tab should follow it. */
  onOwnDrive?: () => void;
  /** Imperative handle for the viewer's info cards. */
  apiRef?: { current: DockApi | null };
}

export interface DockApi {
  /** Open the dock and send a message. False when an answer is still streaming. */
  ask: (text: string) => boolean;
  showFeatures: (fs: Feature[]) => void;
  hideFeature: (id: string) => void;
  /** Is there a copilot highlight (marker or route) under this canvas point? */
  hit: (pos: Cesium.Cartesian2) => boolean;
}
const DRIVE_TOOLS = new Set(['drive_to', 'goto_location', 'drive_rover', 'follow_route']);

/** What the viewer needs to show a drive in progress (from the API's rover.state, 10 Hz). */
export interface DriveInfo {
  active: boolean;
  mode: string;
  status: string;
  speed_mps: number;
  /** Metres left along the active route, when a route is known (null for a plain `drive`). */
  remaining_m: number | null;
  total_m: number | null;
  last_event: string | null;
  /** Route being driven (site metres), for the overview flight after arrival. */
  route_pts?: [number, number][];
}

const DOCK_W = 380;
/** Rover poses closer than this to the street-view world's edge are not mirrored (the world is a square patch). */
const STREET_EDGE_M = 15;
const DEFAULT_AGENT = '/__agent';
const DEFAULT_WS = typeof location !== 'undefined' ? `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/__rover/ws` : 'ws://localhost:8000/ws';
/** Tools whose result is a camera instruction: the WebSocket usually delivers it first, this is the fallback. */
const CAMERA_TOOLS = new Set(['fly_camera', 'set_view']);
const FIT_TOOLS = new Set(['find_locations', 'find_regions', 'find_sites', 'search_pois', 'nearest', 'plan_route', 'plan_science_traverse', 'list_markers']);

/** Frame check: the viewer's site origin and size vs the API's /site. Differences are shown, never converted silently. */
async function checkFrame(site: Site, apiUrl: string): Promise<string | null> {
  try {
    const r = await fetch(`${apiUrl}/site`);
    if (!r.ok) return null;
    const body = (await r.json()) as { manifest?: Record<string, unknown> } & Record<string, unknown>;
    const s = (body.manifest ?? body) as { lat0?: number; lon0?: number; size_m?: number };
    if (typeof s.lat0 !== 'number' || typeof s.lon0 !== 'number') return null;
    const m = site.manifest;
    const dLat = Math.abs(s.lat0 - m.lat0), dLon = Math.abs(s.lon0 - m.lon0);
    if (dLat < 1e-6 && dLon < 1e-6) return null;
    return `Frame mismatch: this viewer shows lat0/lon0 ${m.lat0}/${m.lon0} (${m.size_m} m), the API uses ${s.lat0}/${s.lon0}${s.size_m ? ` (${s.size_m} m)` : ''}. Highlights and the rover will land in the wrong place. Fix: run stack/up.sh --with-viewer (it serves the viewer the same Jezero frame as the backend).`;
  } catch {
    return null;
  }
}

const rad = (d: number) => (d * Math.PI) / 180;

export function AtlasDock({ viewer, site, getRover, cameraMode, onEnterSite, street, onDrive, onWatch, agentUrl = DEFAULT_AGENT, apiWsUrl = DEFAULT_WS, onHighlight, onPlanetCamera, onOwnDrive, apiRef }: AtlasDockProps) {
  const [open, setOpen] = useState(() => typeof window === 'undefined' || window.innerWidth >= 900);
  const asideRef = useRef<HTMLElement>(null);
  const getRoverRef = useRef(getRover);
  const roverGetter = useCallback(() => getRoverRef.current(), []);
  const modeRef = useRef({ cameraMode, street, onEnterSite });
  useEffect(() => {
    getRoverRef.current = getRover;
    modeRef.current = { cameraMode, street, onEnterSite };
  });
  const [streetNote, setStreetNote] = useState<string | null>(null);
  const [outside, setOutside] = useState(false);
  const [count, setCount] = useState(0);
  const [sources, setSources] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [link, setLink] = useState<'open' | 'connecting' | 'closed'>('connecting');
  const [warn, setWarn] = useState<string | null>(null);
  const overlay = useRef<CopilotOverlay | null>(null);
  const selectedRef = useRef<string | null>(null);
  const lastWsCamera = useRef(0);
  const lastServer = useRef<{ x: number; y: number; h: number } | null>(null);
  const linkRef = useRef<RoverLink | null>(null);
  const lastPose = useRef<{ x: number; y: number; h: number } | null>(null);
  const routePts = useRef<{ id: string; pts: [number, number][]; cum: number[] } | null>(null);
  const prevAuto = useRef(false);
  const lastEmit = useRef(0);
  const onDriveRef = useRef(onDrive);
  const cbRef = useRef({ onHighlight, onPlanetCamera, onOwnDrive });
  const sendRef = useRef<((t: string) => boolean) | null>(null);
  const registerSend = useCallback((f: ((t: string) => boolean) | null) => { sendRef.current = f; }, []);
  useEffect(() => { onDriveRef.current = onDrive; cbRef.current = { onHighlight, onPlanetCamera, onOwnDrive }; });

  const sync = useCallback(() => {
    const o = overlay.current;
    setCount(o?.count ?? 0);
    setSources(o?.sources() ?? []);
  }, []);

  // A rover that the viewer has just created (entering site/rover mode) starts where the API rover is.
  const seenRover = useRef<CesiumRover | null>(null);
  useEffect(() => {
    const r = getRoverRef.current();
    if (r && r !== seenRover.current && lastPose.current) r.driver.teleport(lastPose.current.x, lastPose.current.y, lastPose.current.h);
    seenRover.current = r;
  }, [cameraMode, street]);

  const mirrorToStreet = useCallback(() => {
    const st = modeRef.current.street;
    const p = lastPose.current;
    if (!st || !site) return;
    const bubble = BUBBLES.find((b) => b.id === st.world);
    if (!bubble) return;
    if (!p) {
      setStreetNote('Waiting for the API rover (is stack/up.sh running?).');
      setOutside(false);
      return;
    }
    const [ox, oy] = site.lonLatToXY(rad(bubble.lon), rad(bubble.lat));
    const lx = p.x - ox, ly = p.y - oy;
    const half = bubble.sizeM / 2 - STREET_EDGE_M;
    if (Math.abs(lx) > half || Math.abs(ly) > half) {
      setStreetNote(`The rover is outside this street-view patch (${Math.round(Math.hypot(lx, ly))} m away).`);
      setOutside(true);
      return;
    }
    setStreetNote(null);
    setOutside(false);
    st.frame.current?.contentWindow?.postMessage({ type: 'ares-atlas-pose', x: lx, y: ly, heading_deg: p.h }, window.location.origin);
  }, [site]);

  // Overlay + click-to-select.
  useEffect(() => {
    if (!viewer || !site) return;
    const o = new CopilotOverlay(viewer, site, roverGetter);
    overlay.current = o;
    const h = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
    h.setInputAction((ev: { position: Cesium.Cartesian2 }) => {
      const picked = viewer.scene.pick(ev.position);
      let id = o.featureIdFromPick(picked);
      let route = id ? undefined : o.routeFromPick(picked);
      if (!id && !route) {
        const hit = o.hitAt(ev.position);
        if (hit?.feature) id = hit.feature.id; else route = hit?.route;
      }
      selectedRef.current = id ?? null;
      o.setSelected(id ?? null);
      setSelected(id ?? null);
      const feature = id ? o.known(id) : undefined;
      if (feature || route) cbRef.current.onHighlight?.({ feature, route }, 'click', ev.position.x, ev.position.y);
    }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
    h.setInputAction((ev: { endPosition: Cesium.Cartesian2 }) => {
      const picked = viewer.scene.pick(ev.endPosition);
      let id = o.featureIdFromPick(picked);
      let route = id ? undefined : o.routeFromPick(picked);
      if (!id && !route) {
        const hit = o.hitAt(ev.endPosition);
        if (hit?.feature) id = hit.feature.id; else route = hit?.route;
      }
      o.setHover(id ?? null);
      const feature = id ? o.known(id) : undefined;
      cbRef.current.onHighlight?.(feature || route ? { feature, route } : null, 'hover', ev.endPosition.x, ev.endPosition.y);
    }, Cesium.ScreenSpaceEventType.MOUSE_MOVE);
    return () => {
      h.destroy();
      o.dispose();
      overlay.current = null;
    };
  }, [viewer, site, roverGetter]);

  // Make room: the globe (and the street-view iframe, pegman, Map button) shrink instead of hiding behind the dock.
  // The dock sets --dock-w on .app; dock.css applies it to those elements. Restored on unmount.
  useEffect(() => {
    const app = asideRef.current?.parentElement;
    if (!app) return;
    const apply = () => {
      app.style.setProperty('--dock-w', open ? `${Math.min(DOCK_W, Math.round(window.innerWidth * 0.92))}px` : '0px');
      if (viewer && !viewer.isDestroyed()) requestAnimationFrame(() => !viewer.isDestroyed() && viewer.resize());
    };
    apply();
    window.addEventListener('resize', apply);
    return () => {
      window.removeEventListener('resize', apply);
      app.style.removeProperty('--dock-w');
      if (viewer && !viewer.isDestroyed()) viewer.resize();
    };
  }, [viewer, open]);

  // Frame check (API vs viewer).
  useEffect(() => {
    if (!site) return;
    void checkFrame(site, apiWsUrl.replace(/^ws/, 'http').replace(/\/ws$/, '')).then(setWarn);
  }, [site, apiWsUrl]);

  // Live link to the API: agent camera moves, markers, and the server-side rover.
  useEffect(() => {
    if (!viewer || !site) return;
    const l = new RoverLink({ url: apiWsUrl });
    linkRef.current = l;
    const offStatus = l.onStatus((s) => setLink(s));
    const offMsg = l.onMessage((m: Record<string, unknown>) => {
      const o = overlay.current;
      if (!o || !m || typeof m.type !== 'string') return;
      if (m.type === 'view.camera') {
        const pl = (m.payload && typeof m.payload === 'object' ? m.payload : m) as Record<string, unknown>;
        if (pl.mode === 'planet' && typeof pl.lat === 'number' && typeof pl.lon === 'number') {
          cbRef.current.onPlanetCamera?.({ lat: pl.lat, lon: pl.lon, height_m: typeof pl.height_m === 'number' ? pl.height_m : undefined, label: typeof pl.label === 'string' ? pl.label : undefined });
          return;
        }
        const f = focusFromCamera(m);
        if (f) {
          lastWsCamera.current = Date.now();
          o.flyTo({ ...f, heading_deg: typeof m.heading === 'number' ? m.heading : undefined });
        }
      } else if (m.type === 'marker.add') {
        const xy = xyOf(m);
        if (xy) {
          o.show([{ id: String(m.id ?? 'marker'), kind: 'marker', x: xy[0], y: xy[1], label: String(m.label ?? m.id ?? 'marker'), source: 'place_marker' }]);
          sync();
        }
      } else if (m.type === 'marker.remove') {
        o.hide(String(m.id ?? ''));
        sync();
      } else if (m.type === 'route.set' && Array.isArray(m.points)) {
        const pts = (m.points as { x?: number; y?: number }[])
          .filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y)).map((p) => [p.x as number, p.y as number] as [number, number]);
        if (pts.length >= 2) {
          const cum = [0];
          for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
          const id = String(m.route_id ?? 'active');
          routePts.current = { id, pts, cum };
          o.showRoutes([{ id, source: 'route.set', points: thin(pts), label: 'Route' }]);
          sync();
        }
      } else if (m.type === 'view.highlight' && Array.isArray(m.ids)) {
        const fs = (m.ids as unknown[]).map((id) => o.known(String(id))).filter((f): f is NonNullable<typeof f> => !!f);
        if (fs.length) {
          o.show(fs);
          o.fit(fs);
          sync();
        }
      }
    });
    // The server owns the rover the agent drives: follow its pose (only when it changes, so manual driving is not overridden).
    // Markers dropped earlier (this or another session) are loaded once when the link first opens.
    let loadedMarkers = false;
    const offStatus2 = l.onStatus((st) => {
      if (st !== 'open' || loadedMarkers) return;
      loadedMarkers = true;
      void listMarkers().then((ms) => {
        const o = overlay.current;
        if (!o || !ms.length) return;
        o.show(ms.filter((mk) => Number.isFinite(mk.x) && Number.isFinite(mk.y)).map((mk) => ({ id: String(mk.id), kind: 'marker' as const, x: mk.x, y: mk.y, label: mk.label || String(mk.id), source: 'place_marker' })));
        sync();
      }).catch(() => { /* markers are optional */ });
    });
    const offState = l.onState((s) => {
      const prev = lastServer.current;
      lastServer.current = { x: s.x, y: s.y, h: s.heading_deg };
      lastPose.current = lastServer.current;
      const auto = s.mode === 'auto' || s.mode === 'fsd';
      // Drive progress for the viewer: remaining distance along the known route (nearest-point projection).
      if (auto !== prevAuto.current || Date.now() - lastEmit.current > 250) {
        lastEmit.current = Date.now();
        prevAuto.current = auto;
        const rp = routePts.current;
        let remaining: number | null = null, total: number | null = null;
        if (rp && auto && (!s.route_id || s.route_id === rp.id)) {
          let best = Infinity, along = 0;
          for (let i = 1; i < rp.pts.length; i++) {
            const [ax, ay] = rp.pts[i - 1], [bx, by] = rp.pts[i];
            const dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy;
            const u = L2 ? Math.max(0, Math.min(1, ((s.x - ax) * dx + (s.y - ay) * dy) / L2)) : 0;
            const d = Math.hypot(s.x - (ax + u * dx), s.y - (ay + u * dy));
            if (d < best) { best = d; along = rp.cum[i - 1] + u * Math.sqrt(L2); }
          }
          total = rp.cum[rp.cum.length - 1];
          remaining = Math.max(0, total - along);
        }
        onDriveRef.current?.({
          active: auto, mode: s.mode, status: String(s.status ?? ''), speed_mps: typeof s.speed_mps === 'number' ? s.speed_mps : 0,
          remaining_m: remaining, total_m: total, last_event: (s.last_event as string | null | undefined) ?? null,
          route_pts: rp ? thin(rp.pts) : undefined,
        });
      }
      if (auto) {
        // Server-driven: render smoothly from the 10 Hz stream instead of teleporting each update.
        getRoverRef.current()?.pushRemote(s);
        if (modeRef.current.street) mirrorToStreet();
        return;
      }
      if (prev && Math.hypot(prev.x - s.x, prev.y - s.y) < 0.05 && Math.abs(prev.h - s.heading_deg) < 0.1) return;
      if (modeRef.current.street) mirrorToStreet();
      else getRoverRef.current()?.driver.teleport(s.x, s.y, s.heading_deg);
    });
    return () => {
      offStatus();
      offStatus2();
      offMsg();
      offState();
      l.close();
      linkRef.current = null;
    };
  }, [viewer, site, apiWsUrl, sync, mirrorToStreet]);

  // Street view: the sandbox rover is a local sim with no API link (App passes link=0). The API rover (the one the agent
  // drives) is mirrored into it: site-frame pose -> world-local metres, sent as a postMessage the sandbox listens for
  // (web/src/rover/sandbox/main.ts, see web/INTEGRATION.md). Poses outside the street-view patch are not mirrored.
  useEffect(() => {
    if (!street) return;
    mirrorToStreet();
    const t = window.setTimeout(mirrorToStreet, 1500); // the iframe may still be loading on the first call
    return () => window.clearTimeout(t);
  }, [street, mirrorToStreet]);

  const apply = useCallback((x: Extracted, fly: boolean, tool: string) => {
    const o = overlay.current;
    if (!o) return;
    if (x.features.length) o.show(x.features);
    if (x.routes.length) o.showRoutes(x.routes);
    if (x.features.length || x.routes.length) sync();
    if (!fly || modeRef.current.street) return; // street view: the globe is hidden, nothing to fly
    const go = () => {
      if (FIT_TOOLS.has(tool) && (x.features.length > 1 || x.routes.length)) o.fit(x.features, x.routes);
      else if (x.focus) o.flyTo(x.focus);
    };
    if (modeRef.current.cameraMode === 'planet' && (x.focus || x.features.length)) {
      modeRef.current.onEnterSite(); // leave the globe first (creates the rover, restores site imagery), then fly
      window.setTimeout(go, 60);
    } else go();
  }, [sync]);

  /** plan_route / drive_to text carries no polyline: fetch it from the API, accepting it only if its end is the goal. */
  const fetchRoute = useCallback(async (ref: NonNullable<Extracted['routeRef']>, tool: string, fit: boolean) => {
    const base = apiWsUrl.replace(/^ws/, 'http').replace(/\/ws$/, '');
    const n = /(\d+)$/.exec(ref.id)?.[1];
    for (const id of [ref.id, n ? `r${n}` : '']) {
      if (!id) continue;
      try {
        const r = await fetch(`${base}/route/${encodeURIComponent(id)}`);
        if (!r.ok) continue;
        const d = (await r.json()) as { points?: { x: number; y: number }[] };
        const pts = (d.points ?? []).filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y)).map((p) => [p.x, p.y] as [number, number]);
        if (pts.length < 2) continue;
        const last = pts[pts.length - 1];
        if (ref.goal && Math.hypot(last[0] - ref.goal[0], last[1] - ref.goal[1]) > 60) continue; // some other route
        const o = overlay.current;
        if (!o) return;
        const route = { id: ref.id, source: tool, points: thin(pts), label: 'Route' };
        o.showRoutes([route]);
        sync();
        if (fit && !modeRef.current.street) o.fit([], [route]);
        return;
      } catch {
        /* try the next id */
      }
    }
  }, [apiWsUrl, sync]);

  const onToolEvent = useCallback((e: ToolEvent) => {
    if (e.phase === 'start' && DRIVE_TOOLS.has(e.name)) cbRef.current.onOwnDrive?.();
    if (e.phase !== 'result' || e.isError) return;
    const x = extractFromResult(e);
    // fly_camera / set_view: the API WebSocket already moved the camera if it is up; otherwise do it from the result.
    const wsMoved = Date.now() - lastWsCamera.current < 5000;
    apply(x, !(CAMERA_TOOLS.has(e.name) && wsMoved), e.name);
    if (x.routeRef && !x.routes.length) void fetchRoute(x.routeRef, e.name, e.name === 'plan_route');
  }, [apply, fetchRoute]);

  const getContext = useCallback(() => {
    const o = overlay.current;
    const p = getRoverRef.current()?.driver.pose;
    const sp = lastPose.current;
    const sel = selectedRef.current ? overlay.current?.known(selectedRef.current) : undefined;
    let camera: unknown;
    try {
      camera = modeRef.current.street ? undefined : o?.cameraState();
    } catch {
      camera = undefined;
    }
    const rover = p
      ? { x: Math.round(p.x * 10) / 10, y: Math.round(p.y * 10) / 10, z: Math.round(p.z * 10) / 10, heading_deg: Math.round(p.heading_deg), mode: p.mode, status: p.status }
      : sp ? { x: Math.round(sp.x * 10) / 10, y: Math.round(sp.y * 10) / 10, heading_deg: Math.round(sp.h) } : undefined;
    return {
      camera,
      rover,
      view: modeRef.current.street ? `street view (${modeRef.current.street.world})` : modeRef.current.cameraMode,
      selected_marker: sel ? { id: sel.id, x: sel.x, y: sel.y, label: sel.label, from: sel.source } : undefined,
      frame: 'site metres, x east, y north, origin at lat0/lon0 of the viewer manifest',
    };
  }, []);

  /** A result row in a tool card was clicked: select that place and fly there. */
  const focusPlace = useCallback((f: Feature) => {
    const o = overlay.current;
    if (!o) return;
    if (!o.known(f.id)) o.remember([f]);
    selectedRef.current = f.id;
    o.setSelected(f.id);
    setSelected(f.id);
    const go = () => o.flyTo({ x: f.x, y: f.y, mode: 'orbit', distance_m: 450 }, 1.8);
    if (modeRef.current.street) return;
    if (modeRef.current.cameraMode === 'planet') {
      modeRef.current.onEnterSite();
      window.setTimeout(go, 60);
    } else go();
  }, []);

  const clear = () => {
    overlay.current?.clear();
    selectedRef.current = null;
    setSelected(null);
    sync();
  };

  useEffect(() => {
    if (!apiRef) return;
    apiRef.current = {
      ask: (text) => { setOpen(true); return sendRef.current ? sendRef.current(text) : false; },
      showFeatures: (fs) => { overlay.current?.show(fs); sync(); },
      hideFeature: (id) => { overlay.current?.hide(id); sync(); },
      hit: (pos) => !!overlay.current?.hitAt(pos),
    };
    return () => { apiRef.current = null; };
  }, [apiRef, sync]);

  const lines = useMemo(() => (sources.length ? `from ${sources.join(', ')}` : ''), [sources]);

  return (
    <aside ref={asideRef} className={`atlas-dock${open ? '' : ' atlas-dock--closed'}`} aria-label="Ares copilot dock">
      <button type="button" className="atlas-dock__tab" onClick={() => setOpen(!open)} aria-expanded={open}
        title={open ? 'Collapse the copilot' : 'Open the copilot'}>
        {open ? <><span>Hide</span><span aria-hidden="true">&rsaquo;</span></> : <><span aria-hidden="true">&lsaquo;</span><span>Copilot</span></>}
      </button>
      <div className="atlas-dock__body" hidden={!open}>
        <div className="atlas-dock__bar" role="status">
          <span className={`atlas-dock__dot atlas-dock__dot--${link}`} title={`API link: ${link}`} />
          <span className="atlas-dock__info">
            {count ? `${count} highlighted ${lines}` : 'No highlights yet'}
            {selected ? ` · selected ${selected}` : ''}
          </span>
          <button type="button" className="atlas-dock__clear" onClick={clear} disabled={!count && !selected}>Clear highlights</button>
        </div>
        {warn && <div className="atlas-dock__warn" role="alert">{warn}</div>}
        {street && streetNote && (
          <div className="atlas-dock__note" role="status">
            <span>{streetNote}</span>
            {onWatch && outside && <button type="button" className="atlas-dock__clear" onClick={onWatch}>Watch in Jezero view</button>}
          </div>
        )}
        <div className="atlas-dock__chat">
          <ChatPanel agentUrl={agentUrl} getContext={getContext} onToolEvent={onToolEvent} onFocusPlace={focusPlace} registerSend={registerSend} />
        </div>
      </div>
    </aside>
  );
}
