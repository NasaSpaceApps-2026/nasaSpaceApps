const MAX = 160;

function clip(s: string, n = MAX): string {
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}

function num(n: number): string {
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 100) / 100);
}

function value(v: unknown): string {
  if (v === null || v === undefined) return 'none';
  if (typeof v === 'number') return num(v);
  if (typeof v === 'string') return v.length > 28 ? `"${clip(v, 28)}"` : v;
  if (typeof v === 'boolean') return String(v);
  if (Array.isArray(v)) {
    if (v.length <= 3 && v.every((x) => typeof x !== 'object' || x === null)) return `[${v.map(value).join(', ')}]`;
    return `${v.length} items`;
  }
  if (typeof v === 'object') {
    const o = v as Record<string, unknown>;
    if ('x' in o && 'y' in o && typeof o.x === 'number' && typeof o.y === 'number') return `(${num(o.x)}, ${num(o.y)})`;
    return `{${Object.keys(o).length} fields}`;
  }
  return String(v);
}

const OPS: Record<string, string> = { '<=': '≤', '>=': '≥', '!=': '≠', '==': '=', '=': '=', '<': '<', '>': '>' };

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** Location ref: `{x,y}` -> `(812, 455)`, `{lat,lon}`, strings (`marker:m1`, `set:3`, `base`) as-is. */
function loc(v: unknown): string {
  if (Array.isArray(v)) return v.map(loc).join(', ');
  if (isObj(v) && typeof v.lat === 'number' && typeof v.lon === 'number') return `(${num(v.lat)}°, ${num(v.lon)}°)`;
  return value(v);
}

function list(v: unknown): string {
  return Array.isArray(v) ? `[${v.map(value).join(', ')}]` : value(v);
}

/** One condition from the find_locations / find_regions condition language. */
function condition(c: unknown): string {
  if (!isObj(c)) return value(c);
  if (Array.isArray(c.any_of)) return `(${c.any_of.map(condition).join(' or ')})`;
  if (typeof c.evidence === 'string') {
    return typeof c.min_tier === 'string' ? `${c.evidence} ≥ ${c.min_tier}` : String(c.evidence);
  }
  if (typeof c.layer === 'string') {
    const op = typeof c.op === 'string' ? (OPS[c.op] ?? c.op) : '';
    if (!op) return c.layer;
    return `${c.layer} ${op} ${op === 'in' || op === 'not in' ? list(c.value) : value(c.value)}`;
  }
  return Object.entries(c).map(([k, v]) => `${k} ${value(v)}`).join(' ');
}

function conditions(v: unknown): string {
  return (Array.isArray(v) ? v : [v]).map(condition).join(' · ');
}

function within(v: unknown): string {
  if (!isObj(v)) return `within ${value(v)}`;
  const from = v.from === undefined ? '' : ` of ${loc(v.from)}`;
  const max = typeof v.max === 'number' ? num(v.max) : value(v.max);
  switch (v.metric) {
    case 'walk_time_min':
      return `within ${max} min walk${from}`;
    case 'walk_m':
      return `within ${max} m walk${from}`;
    case 'straight_m':
      return `within ${max} m${from}`;
    default:
      return `within ${max}${typeof v.metric === 'string' ? ' ' + v.metric : ''}${from}`;
  }
}

/** Human rendering of known arguments; null means "use the generic `key: value`". */
function special(key: string, v: unknown): string | null {
  switch (key) {
    case 'conditions':
      return v == null ? null : conditions(v);
    case 'within':
      return v == null ? null : within(v);
    case 'objective':
      return typeof v === 'string' ? `objective ${v}` : null;
    case 'profile':
      return typeof v === 'string' ? `profile ${v}` : null;
    case 'from':
    case 'to':
    case 'at':
    case 'near':
      return v == null ? null : `${key} ${loc(v)}`;
    default:
      return null;
  }
}

/** Short human summary of tool arguments, e.g. `target: carbonate, radius_m: 2000`. */
export function summarizeArgs(args: Record<string, unknown> | undefined | null): string {
  if (!args || typeof args !== 'object') return '';
  const keys = Object.keys(args).filter((k) => args[k] !== undefined);
  if (keys.length === 0) return 'no arguments';
  const rendered = keys.map((k) => special(k, args[k]));
  const humanMode = keys.some((k, i) => rendered[i] !== null && ['conditions', 'within', 'objective'].includes(k));
  const sep = humanMode ? ' · ' : ', ';
  const parts: string[] = [];
  let len = 0;
  for (let i = 0; i < keys.length; i++) {
    const sp = humanMode ? rendered[i] : null;
    const p = sp ?? (humanMode && keys[i] === 'limit' ? `limit ${value(args[keys[i]])}` : `${keys[i]}: ${value(args[keys[i]])}`);
    if (len + p.length > MAX && parts.length > 0) {
      parts.push(`+${keys.length - i} more`);
      break;
    }
    parts.push(p);
    len += p.length + 2;
  }
  return clip(parts.join(sep), MAX + 12);
}

/** `get_site_overview` -> `Get site overview`. */
export function humanToolName(name: string): string {
  const s = name.replace(/^mcp__[^_]+__/, '').replace(/_/g, ' ').trim();
  return s ? s[0].toUpperCase() + s.slice(1) : name;
}

export function prettyJson(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}
