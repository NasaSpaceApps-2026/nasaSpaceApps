// Pure parsing: turn agent tool results into things the viewer can draw (no Cesium here, so it is unit-testable).
// Tool results arrive as MCP structuredContent (see mcp/format.py, contracts/mcp-tools.md). Every shape is read
// defensively: any field may be missing, and a result we do not understand simply yields no features.
import type { ToolEvent } from './types';

export type FeatureKind = 'spot' | 'region' | 'poi' | 'marker' | 'stop' | 'target';

/** One drawable place in site-frame metres (x east, y north). */
export interface Feature {
  id: string;
  kind: FeatureKind;
  x: number;
  y: number;
  label: string;
  /** Tool that produced it, shown on the label ("from find_regions"). */
  source: string;
  /** Evidence tier / score text, when the tool gave one. */
  tier?: string;
  /** Footprint or region radius in metres for the ring drawn around the point. */
  radius?: number;
}

export interface RouteLine {
  id: string;
  source: string;
  points: [number, number][];
  label: string;
}

export interface Extracted {
  features: Feature[];
  routes: RouteLine[];
  /** A single place the camera should go to (fly_camera / set_view / teleport / goto targets). */
  focus?: { x: number; y: number; label?: string; mode?: string; distance_m?: number };
  /** plan_route result without geometry: the dock fetches the polyline from the API (route id + the goal to verify it). */
  routeRef?: { id: string; goal?: [number, number] };
}

const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const rec = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const str = (v: unknown): string | undefined => (typeof v === 'string' && v ? v : undefined);

/** Position from {x,y}, [x,y] or {position:{x,y}}. */
export function xyOf(v: unknown): [number, number] | null {
  if (rec(v)) {
    if (num(v.x) && num(v.y)) return [v.x, v.y];
    if (rec(v.position)) return xyOf(v.position);
    if (rec(v.target)) return xyOf(v.target);
    if (rec(v.representative_point)) return xyOf(v.representative_point);
    if (rec(v.centroid)) return xyOf(v.centroid);
  } else if (Array.isArray(v) && v.length >= 2 && num(v[0]) && num(v[1])) return [v[0], v[1]];
  return null;
}

const MAX_FEATURES = 60;
/** Tools whose result is a set of places or a route worth drawing. get_site_overview, describe_* etc. are deliberately absent. */
const DRAW_TOOLS = new Set(['find_locations', 'find_regions', 'find_sites', 'search_pois', 'nearest', 'get_set', 'list_markers',
  'place_marker', 'plan_route', 'assess_route', 'plan_science_traverse', 'set_view']);
const FOCUS_TOOLS = new Set(['fly_camera', 'set_view', 'teleport_rover', 'goto_location', 'drive_to', 'place_marker', ...DRAW_TOOLS]);
const LIST_KEYS = ['members', 'sites', 'pois', 'hits', 'results', 'markers', 'stops', 'items', 'regions', 'spots'];

const round = (v: number, d = 0) => { const k = 10 ** d; return Math.round(v * k) / k; };
const clean = (n: string) => n.replace(/^Sample \d+:\s*/, '');

/** First evidence layer that has a best tier name: [layer, tier name]. */
function evidenceOf(m: Record<string, unknown>): { layer?: string; tier?: string } {
  const tier0 = str(m.best_tier_name);
  if (rec(m.evidence)) {
    for (const [layer, ev] of Object.entries(m.evidence)) {
      if (rec(ev) && str(ev.best_tier_name)) return { layer, tier: str(ev.best_tier_name) };
    }
  }
  return { tier: tier0 };
}

/**
 * Human label for a result row, built only from fields present in the structured result (nothing invented, no raw score):
 * pad "Pad 1.1 · slope 1.1° · 14 min walk", region "Region 1.2 · strong_orbital olivine · 0.43 km²", POI/marker/stop: its name.
 */
export function formatFeatureLabel(kind: FeatureKind, m: Record<string, unknown>, id: string, args?: Record<string, unknown>): string {
  const attrs = rec(m.attributes) ? m.attributes : {};
  const pick = (k: string) => (num(attrs[k]) ? attrs[k] : num(m[k]) ? m[k] : undefined) as number | undefined;
  const name = str(m.name) ?? str(m.label);
  if (kind === 'poi' || kind === 'marker' || kind === 'stop') return name ? clean(name) : id;
  const parts: string[] = [];
  if (kind === 'spot') {
    const prof = str(args?.profile) ?? '';
    parts.push(`${/pad/i.test(prof) ? 'Pad' : 'Spot'} ${id}`);
    const slope = pick('slope_2m_mean');
    if (slope !== undefined) parts.push(`slope ${round(slope, 1)}°`);
    const walk = pick('walk_time_min');
    if (walk !== undefined) parts.push(`${round(walk)} min walk`);
    return parts.join(' · ');
  }
  // region / target / anything else set-like
  parts.push(`Region ${name ? clean(name) : id}`);
  const ev = evidenceOf(m);
  const tier = [ev.tier, ev.layer].filter(Boolean).join(' ');
  if (tier) parts.push(tier);
  if (num(m.area_m2) && m.area_m2 > 0) parts.push(m.area_m2 < 50_000 ? `${round(m.area_m2)} m²` : `${round(m.area_m2 / 1e6, 2)} km²`);
  return parts.join(' · ');
}

/** "Route · 2.1 km · 48 min", omitting what the result does not carry. */
export function formatRouteLabel(d: Record<string, unknown>): string {
  const r = rec(d.route) ? d.route : {};
  const dist = num(d.distance_m) ? d.distance_m : num(r.distance_m) ? r.distance_m : undefined;
  const dur = num(d.duration_min) ? d.duration_min : num(r.duration_min) ? r.duration_min : undefined;
  return ['Route', dist !== undefined ? `${round(dist / 1000, 1)} km` : '', dur !== undefined ? `${round(dur)} min` : ''].filter(Boolean).join(' · ');
}

function tierText(m: Record<string, unknown>): string | undefined {
  const name = str(m.best_tier_name);
  if (name) return name.replace(/_/g, ' ');
  if (num(m.best_tier)) return `tier ${m.best_tier}`;
  if (num(m.score)) return `score ${Math.round(m.score * 100) / 100}`;
  return undefined;
}

function radiusOf(m: Record<string, unknown>): number | undefined {
  if (num(m.clear_radius_m) && m.clear_radius_m > 0) return m.clear_radius_m;
  if (num(m.area_m2) && m.area_m2 > 0) return Math.min(400, Math.max(12, Math.sqrt(m.area_m2 / Math.PI)));
  return undefined;
}

/** Points of a route: structuredContent.points, or route.points, as [x, y][]. */
function routePoints(d: Record<string, unknown>): [number, number][] {
  const raw = Array.isArray(d.points) ? d.points : rec(d.route) && Array.isArray(d.route.points) ? d.route.points : [];
  const out: [number, number][] = [];
  for (const p of raw) {
    const xy = xyOf(p);
    if (xy) out.push(xy);
  }
  return out;
}

/** Thin a long polyline to at most `max` vertices (keeps the ends). */
export function thin(pts: [number, number][], max = 400): [number, number][] {
  if (pts.length <= max) return pts;
  const step = (pts.length - 1) / (max - 1);
  return Array.from({ length: max }, (_, i) => pts[Math.round(i * step)]);
}

function kindFor(tool: string, listKey: string): FeatureKind {
  if (listKey === 'markers' || tool === 'place_marker' || tool === 'list_markers') return 'marker';
  if (listKey === 'stops') return 'stop';
  if (listKey === 'pois' || tool === 'search_pois') return 'poi';
  if (tool === 'find_locations') return 'spot';
  return 'region'; // find_regions, find_sites, nearest, unknown sets
}

function fromList(tool: string, key: string, list: unknown[], out: Feature[], args?: Record<string, unknown>) {
  const kind = kindFor(tool, key);
  list.slice(0, MAX_FEATURES).forEach((raw, i) => {
    if (!rec(raw)) return;
    const xy = xyOf(raw);
    if (!xy) return;
    const id = str(raw.id) ?? str(raw.ref) ?? `${tool}.${i + 1}`;
    out.push({ id, kind, x: xy[0], y: xy[1], source: tool, tier: tierText(raw), radius: radiusOf(raw), label: formatFeatureLabel(kind, raw, id, args) });
  });
}

/** Extract drawable features from one finished tool call. Never throws. */
export function extractFromResult(e: Pick<ToolEvent, 'name' | 'args' | 'structured' | 'isError'> & { result?: string }): Extracted {
  const res: Extracted = { features: [], routes: [] };
  const d = e.structured;
  if (e.isError) return res;
  // The agent does not always forward MCP structuredContent: fall back to the text the model saw.
  if (!rec(d)) return typeof e.result === 'string' ? extractFromText(e.name, e.result, e.args) : res;
  try {
    const tool = e.name;
    if (!FOCUS_TOOLS.has(tool)) return res;

    // 1. Lists of places in any of the known keys.
    for (const k of LIST_KEYS) if (DRAW_TOOLS.has(tool) && Array.isArray(d[k])) fromList(tool, k, d[k] as unknown[], res.features, e.args);

    // 2. Routes (plan_route, plan_science_traverse -> route, assess_route).
    const pts = routePoints(d);
    if (pts.length >= 2 && DRAW_TOOLS.has(tool)) {
      const rid = str(d.route_id) ?? (rec(d.route) ? str(d.route.route_id) : undefined) ?? tool;
      res.routes.push({ id: rid, source: tool, points: thin(pts), label: formatRouteLabel(d) });
    }

    // 3. A single target: view / fly_camera, teleport, goto, place_marker, describe.
    const t = xyOf(d.target) ?? (tool === 'place_marker' ? xyOf(d) : null) ??
      (['teleport_rover', 'goto_location', 'drive_rover', 'follow_route'].includes(tool) ? xyOf(d.state) ?? xyOf(d) : null);
    if (t) {
      const tg = rec(d.target) ? d.target : undefined;
      const label = (tg && (str(tg.id) ?? str(tg.name))) ?? str(d.label) ?? str(d.id);
      res.focus = {
        x: t[0], y: t[1], label, mode: str(d.mode),
        distance_m: num(d.distance_m) ? d.distance_m : num(e.args?.distance_m) ? (e.args.distance_m as number) : undefined,
      };
      if (tool === 'place_marker' && !res.features.length) {
        res.features.push({ id: str(d.id) ?? 'marker', kind: 'marker', x: t[0], y: t[1], label: label ?? 'marker', source: tool });
      }
    }

    // 3b. set_view highlights as {id,x?,y?}: only those with coordinates can be drawn here (ids go through the registry).
    if (Array.isArray(d.highlights)) fromList(tool, 'highlights', d.highlights, res.features, e.args);

    // 4. Fall back to the first set member when nothing else gave a camera target.
    if (!res.focus && res.features.length && ['find_locations', 'find_regions', 'find_sites', 'search_pois', 'nearest'].includes(tool)) {
      res.focus = { x: res.features[0].x, y: res.features[0].y, label: res.features[0].label };
    }
  } catch {
    /* defensive: a malformed result draws nothing */
  }
  return res;
}

/** Parse a `view.camera`-style message from the API WebSocket into a focus. */
export function focusFromCamera(m: unknown): Extracted['focus'] | null {
  if (!rec(m)) return null;
  const xy = xyOf(m);
  if (!xy) return null;
  return {
    x: xy[0], y: xy[1], label: str(m.target), mode: str(m.mode),
    distance_m: num(m.distance) ? m.distance : num(m.distance_m) ? m.distance_m : undefined,
  };
}

const NUM = '-?\\d+(?:\\.\\d+)?';
const XY = `\\((${NUM}),\\s*(${NUM})\\)`;

/**
 * Text fallback: every tool's text starts with numbered or dashed rows carrying "(x, y) m" (mcp/format.py). Pure, never throws.
 * Same output shape as the structured path, so labels, rings and fly-to behave the same.
 */
export function extractFromText(tool: string, text: string, args: Record<string, unknown> = {}): Extracted {
  const res: Extracted = { features: [], routes: [] };
  try {
    const lines = text.split('\n');
    const push = (f: Feature) => { if (res.features.length < MAX_FEATURES) res.features.push(f); };
    const pt = (m: RegExpExecArray, i: number): [number, number] => [Number(m[i]), Number(m[i + 1])];
    for (const line of lines) {
      let m: RegExpExecArray | null;
      if (tool === 'find_locations') {
        m = new RegExp(`^\\s*\\d+\\.\\s+(\\S+)\\s+${XY} m(.*)$`).exec(line);
        if (!m) continue;
        const attrs: Record<string, number> = {};
        for (const a of m[4].matchAll(/(\w+)=(-?\d+(?:\.\d+)?)/g)) attrs[a[1]] = Number(a[2]);
        const rad = /clear radius (\d+(?:\.\d+)?) m/.exec(m[4]);
        const sc = /score (-?\d+(?:\.\d+)?)/.exec(m[4]);
        const [x, y] = pt(m, 2);
        push({ id: m[1], kind: 'spot', x, y, source: tool, radius: rad ? Number(rad[1]) : undefined, tier: sc ? `score ${sc[1]}` : undefined,
          label: formatFeatureLabel('spot', { attributes: attrs }, m[1], args) });
      } else if (tool === 'find_sites' || tool === 'find_regions') {
        m = new RegExp(`^\\s*\\d+\\.\\s+(\\S+)\\s+${XY} m(.*)$`).exec(line);
        if (!m) continue;
        const tierM = /best tier \d+ (\w+)/.exec(m[4]);
        const layer = /layer (\w+)\s*$/.exec(m[4]) ?? /layer (\w+)/.exec(m[4]);
        const area = /region area (\d+(?:\.\d+)?) m2/.exec(m[4]) ?? /area (\d+(?:\.\d+)?) m2/.exec(m[4]);
        const mm: Record<string, unknown> = { best_tier_name: tierM?.[1], area_m2: area ? Number(area[1]) : undefined };
        if (tierM && layer) mm.evidence = { [layer[1]]: { best_tier_name: tierM[1] } };
        const [x, y] = pt(m, 2);
        push({ id: m[1], kind: 'region', x, y, source: tool, tier: tierM ? tierM[1].replace(/_/g, ' ') : undefined,
          radius: area ? Math.min(400, Math.max(12, Math.sqrt(Number(area[1]) / Math.PI))) : undefined,
          label: formatFeatureLabel('region', mm, m[1]) });
      } else if (tool === 'nearest') {
        m = new RegExp(`^\\s*\\d+\\.\\s+\\S+\\s+->\\s+(\\S+)\\s+at\\s+${XY}`).exec(line);
        if (!m) continue;
        const [x, y] = pt(m, 2);
        push({ id: m[1], kind: 'region', x, y, source: tool, label: `Region ${m[1]}` });
      } else if (tool === 'search_pois') {
        m = new RegExp(`^\\s*-\\s+(poi:\\S+)\\s+(.+?)\\s+\\[(\\w+)\\]\\s+${XY} m`).exec(line);
        if (!m) continue;
        const [x, y] = pt(m, 4);
        push({ id: m[1], kind: 'poi', x, y, source: tool, label: clean(m[2]) });
      } else if (tool === 'plan_science_traverse') {
        m = new RegExp(`^\\s*\\d+\\.\\s+(poi:\\S+)\\s+(.+?)\\s+${XY} m`).exec(line);
        if (!m) continue;
        const [x, y] = pt(m, 3);
        push({ id: m[1], kind: 'stop', x, y, source: tool, label: `Stop · ${clean(m[2])}` });
      } else if (tool === 'place_marker') {
        m = new RegExp(`Placed marker:(\\S+)\\s+"(.*)"\\s+at\\s+${XY}`).exec(line);
        if (!m) continue;
        const [x, y] = pt(m, 3);
        push({ id: m[1], kind: 'marker', x, y, source: tool, label: m[2] });
      }
    }
    if (tool === 'plan_route' || tool === 'drive_to') {
      const r = /^Route ([\w-]+)[ :(]/m.exec(text);
      const g = new RegExp(`goal\\s+${XY}`).exec(text);
      if (r) res.routeRef = { id: r[1], goal: g ? [Number(g[1]), Number(g[2])] : undefined };
      if (g) push({ id: 'goal', kind: 'stop', x: Number(g[1]), y: Number(g[2]), source: tool, label: 'Route goal' });
      const gl = new RegExp(`^Goal (.*?)\\s*${XY} m`, 'm').exec(text);
      if (tool === 'drive_to' && gl) push({ id: 'goal', kind: 'stop', x: Number(gl[2]), y: Number(gl[3]), source: tool, label: gl[1].trim() && gl[1].trim() !== 'top' ? `Goal · ${clean(gl[1].trim())}` : 'Highest point' });
    }
    if (['teleport_rover', 'goto_location', 'drive_rover', 'follow_route', 'drive_to'].includes(tool)) {
      const p = new RegExp(`rover now (?:at )?${XY}`, 'i').exec(text);
      if (p) res.focus = { x: Number(p[1]), y: Number(p[2]), label: 'rover' };
    }
    if (tool === 'fly_camera' || tool === 'set_view') {
      const p = new RegExp(`target (.+?) ${XY} m`).exec(text);
      if (p) res.focus = { x: Number(p[2]), y: Number(p[3]), label: p[1], mode: /mode (\w+)/.exec(text)?.[1],
        distance_m: typeof args.distance_m === 'number' ? args.distance_m : undefined };
    }
    if (!res.focus && res.features.length && ['find_locations', 'find_regions', 'find_sites', 'search_pois', 'nearest', 'place_marker'].includes(tool)) {
      res.focus = { x: res.features[0].x, y: res.features[0].y, label: res.features[0].label };
    }
  } catch {
    /* defensive */
  }
  return res;
}
