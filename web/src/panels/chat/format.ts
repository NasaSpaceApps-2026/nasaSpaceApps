const MAX = 90;

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

/** Short human summary of tool arguments, e.g. `target: carbonate, radius_m: 2000`. */
export function summarizeArgs(args: Record<string, unknown> | undefined | null): string {
  if (!args || typeof args !== 'object') return '';
  const keys = Object.keys(args).filter((k) => args[k] !== undefined);
  if (keys.length === 0) return 'no arguments';
  const parts: string[] = [];
  let len = 0;
  for (let i = 0; i < keys.length; i++) {
    const p = `${keys[i]}: ${value(args[keys[i]])}`;
    if (len + p.length > MAX && parts.length > 0) {
      parts.push(`+${keys.length - i} more`);
      break;
    }
    parts.push(p);
    len += p.length + 2;
  }
  return clip(parts.join(', '), MAX + 12);
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
