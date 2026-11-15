/** Turn agent tool events into map highlights (site-frame metres). Pure functions, unit tested. */

export interface Highlight {
  key: string;
  x: number;
  y: number;
  label: string;
  tool: string;
  kind: 'result' | 'target' | 'rover' | 'marker';
}

type Args = Record<string, unknown>;
const NUM = '-?\\d+(?:\\.\\d+)?';
const PAIR = new RegExp(`\\((${NUM}),\\s*(${NUM})\\)`);
const LISTED = ['find_locations', 'find_regions', 'find_sites', 'nearest', 'search_pois', 'get_set'];
const POINT = ['teleport_rover', 'goto_location', 'drive_rover', 'place_marker', 'plan_route', 'plan_science_traverse',
  'follow_route', 'fly_camera', 'set_view', 'describe_location', 'sample_terrain'];

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** Coordinates the model passed in the arguments: {x, y}, [x, y] or nested one level. */
export function argPoints(args: Args): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  const visit = (v: unknown, depth: number) => {
    if (!v || typeof v !== 'object' || depth > 2) return;
    if (Array.isArray(v)) {
      if (v.length === 2 && finite(v[0]) && finite(v[1])) out.push([v[0], v[1]]);
      else v.forEach((e) => visit(e, depth + 1));
      return;
    }
    const o = v as Args;
    if (finite(o.x) && finite(o.y)) out.push([o.x, o.y]);
    else Object.values(o).forEach((e) => visit(e, depth + 1));
  };
  Object.values(args).forEach((v) => visit(v, 0));
  return out;
}

export function highlightsFor(name: string, args: Args, result: string | undefined, isError: boolean, callId: string): Highlight[] {
  if (isError) return [];
  const out: Highlight[] = [];
  const text = result ?? '';
  if (LISTED.includes(name)) {
    let i = 0;
    for (const line of text.split('\n')) {
      const m = /^\s*(\d+)\.\s+(\S+)/.exec(line);
      const p = m && PAIR.exec(line);
      if (!m || !p) continue;
      i += 1;
      out.push({ key: `${callId}:${i}`, x: Number(p[1]), y: Number(p[2]), label: m[2].includes('.') ? m[2] : String(i), tool: name, kind: 'result' });
      if (i >= 12) break;
    }
  } else if (POINT.includes(name)) {
    // Destination: the first "(x, y)" the tool reported ("teleported to (x, y)", "Stops: goal (x, y)", "Placed marker ... at (x, y)").
    const lines = text.split('\n');
    const goal = lines.find((l) => /goal|arrived|teleported|placed marker|rover now/i.test(l) && PAIR.test(l)) ?? lines.find((l) => PAIR.test(l));
    const p = goal ? PAIR.exec(goal) : null;
    if (p) out.push({ key: `${callId}:t`, x: Number(p[1]), y: Number(p[2]), label: name.replace(/_/g, ' '), tool: name, kind: 'target' });
    else for (const [x, y] of argPoints(args).slice(0, 3)) out.push({ key: `${callId}:a${out.length}`, x, y, label: name.replace(/_/g, ' '), tool: name, kind: 'target' });
  }
  return out;
}
