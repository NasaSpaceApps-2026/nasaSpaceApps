import { useEffect, useRef, useState } from 'react';

export interface RoverState {
  x: number; y: number; heading_deg?: number; mode?: string; status?: string;
  odometer_m?: number; speed_mps?: number; slope_deg?: number; z?: number; [k: string]: unknown;
}
export interface Marker { id: string; x: number; y: number; label?: string; created_by?: string }
export interface Minimap {
  name: string; lat0: number; lon0: number; size_m: number; px: number; source: string;
  pois: Array<{ id?: string; name?: string; type?: string; x: number; y: number }>;
  traverse: number[][][];
}

export const API_URL = (new URLSearchParams(location.search).get('api') ?? 'http://localhost:8000').replace(/\/$/, '');
export const AGENT_BASE = (new URLSearchParams(location.search).get('agent') ?? 'http://localhost:8766').replace(/\/$/, '');

const asState = (m: any): RoverState | null => {
  const s = m?.type === 'rover.state' ? (m.state ?? m.data ?? m) : null;
  return s && typeof s.x === 'number' && typeof s.y === 'number' ? s : null;
};

/** Rover pose from the API: WebSocket (read only, never sends a pose) with 1 s polling as fallback. Trail is kept. */
export function useRover(): { rover: RoverState | null; trail: Array<[number, number]>; link: 'live' | 'polling' | 'down' } {
  const [rover, setRover] = useState<RoverState | null>(null);
  const [link, setLink] = useState<'live' | 'polling' | 'down'>('down');
  const trail = useRef<Array<[number, number]>>([]);
  useEffect(() => {
    let stop = false;
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let lastWs = 0;
    const push = (s: RoverState) => {
      const t = trail.current;
      const last = t[t.length - 1];
      if (last && Math.hypot(last[0] - s.x, last[1] - s.y) > 100) t.length = 0; // teleport: do not draw a line across the jump
      if (!last || !t.length || Math.hypot(last[0] - s.x, last[1] - s.y) > 1.5) {
        t.push([s.x, s.y]);
        if (t.length > 600) t.shift();
      }
      setRover(s);
    };
    const open = () => {
      if (stop) return;
      try {
        ws = new WebSocket(API_URL.replace(/^http/, 'ws') + '/ws');
      } catch { retry = setTimeout(open, 3000); return; }
      ws.onmessage = (e) => {
        try {
          const s = asState(JSON.parse(e.data));
          if (s) { lastWs = Date.now(); setLink('live'); push(s); }
        } catch { /* ignore */ }
      };
      ws.onclose = () => { ws = null; if (!stop) retry = setTimeout(open, 3000); };
    };
    open();
    const poll = setInterval(async () => {
      if (Date.now() - lastWs < 2500) return;
      try {
        const r = await fetch(API_URL + '/rover/state');
        if (!r.ok) throw new Error(String(r.status));
        push(await r.json());
        setLink('polling');
      } catch { setLink('down'); }
    }, 1000);
    return () => { stop = true; clearTimeout(retry); clearInterval(poll); ws?.close(); };
  }, []);
  return { rover, trail: trail.current, link };
}

export function useMarkers(): Marker[] {
  const [markers, setMarkers] = useState<Marker[]>([]);
  useEffect(() => {
    let stop = false;
    const load = async () => {
      try {
        const r = await fetch(API_URL + '/markers');
        const j = await r.json();
        if (!stop) setMarkers(Array.isArray(j) ? j : j.markers ?? []);
      } catch { /* API down */ }
    };
    load();
    const t = setInterval(load, 2000);
    return () => { stop = true; clearInterval(t); };
  }, []);
  return markers;
}

export function useMinimap(): { map: Minimap | null; error: string } {
  const [map, setMap] = useState<Minimap | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    fetch('/minimap/current.json').then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(setMap).catch(() => setError('No minimap yet: run devapi.render_minimap (stack/up.sh does it).'));
  }, []);
  return { map, error };
}

export function useAgentHealth(): { ok: boolean; text: string } {
  const [h, setH] = useState({ ok: false, text: 'agent: checking' });
  useEffect(() => {
    let stop = false;
    const go = async () => {
      try {
        const j = await (await fetch(AGENT_BASE + '/health')).json();
        const what = j.mock ? 'MOCK agent' : `${j.model ?? 'agent'}${j.replay ? ' (replay)' : ''}`;
        if (!stop) setH({ ok: true, text: `${what}${j.mcp_connected === false ? ', MCP connects on first message' : ''}` });
      } catch { if (!stop) setH({ ok: false, text: 'agent not reachable' }); }
    };
    go();
    const t = setInterval(go, 5000);
    return () => { stop = true; clearInterval(t); };
  }, []);
  return h;
}
