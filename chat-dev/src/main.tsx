import { StrictMode, useCallback, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ChatPanel, type ToolEvent } from '../../web/src/panels/chat';
import { highlightsFor, type Highlight } from './highlights';
import { AGENT_BASE, API_URL, useAgentHealth, useMarkers, useMinimap, useRover } from './live';
import { Minimap } from './Minimap';
import './app.css';

interface LogLine { id: string; name: string; status: 'running' | 'ok' | 'error'; text: string }

function App() {
  const { rover, trail, link } = useRover();
  const markers = useMarkers();
  const { map, error } = useMinimap();
  const agent = useAgentHealth();
  const [highlights, setHighlights] = useState<Highlight[]>([]);
  const [log, setLog] = useState<LogLine[]>([]);
  const [showPois, setShowPois] = useState(true);
  const roverRef = useRef(rover);
  roverRef.current = rover;

  const getContext = useCallback(() => {
    const r = roverRef.current;
    return r ? { rover: { x: r.x, y: r.y, heading_deg: r.heading_deg ?? 0 } } : undefined;
  }, []);

  const onToolEvent = useCallback((e: ToolEvent) => {
    if (e.phase === 'start') {
      setLog((l) => [{ id: e.id, name: e.name, status: 'running' as const, text: '' }, ...l].slice(0, 40));
      return;
    }
    setLog((l) => l.map((x) => (x.id === e.id ? { ...x, status: e.isError ? 'error' : 'ok', text: e.summary ?? '' } : x)));
    const hs = highlightsFor(e.name, e.args, e.result, !!e.isError, e.id);
    if (hs.length) {
      // a new search replaces the previous result dots; point-like targets accumulate (last 6)
      setHighlights((prev) => {
        const results = hs[0].kind === 'result' ? hs : prev.filter((p) => p.kind === 'result');
        const targets = (hs[0].kind === 'target' ? [...prev.filter((p) => p.kind === 'target'), ...hs] : prev.filter((p) => p.kind === 'target')).slice(-6);
        return [...results, ...targets];
      });
    }
  }, []);

  return (
    <div className="app">
      <main className="side">
        <header className="bar">
          <h1>Ares copilot</h1>
          <span className={`pill ${link === 'down' ? 'bad' : 'good'}`}>rover link: {link}</span>
          <span className={`pill ${agent.ok ? 'good' : 'bad'}`}>{agent.text}</span>
        </header>
        <section className="stats" aria-live="off">
          {rover ? (
            <>
              <Stat k="x, y" v={`${rover.x.toFixed(0)}, ${rover.y.toFixed(0)} m`} />
              <Stat k="heading" v={`${Math.round(rover.heading_deg ?? 0)}°`} />
              <Stat k="mode" v={String(rover.mode ?? '-')} />
              <Stat k="status" v={String(rover.status ?? '-')} />
              <Stat k="odometer" v={`${(rover.odometer_m ?? 0).toFixed(0)} m`} />
              <Stat k="slope" v={rover.slope_deg != null ? `${rover.slope_deg.toFixed(1)}°` : '-'} />
            </>
          ) : (
            <span className="muted">No rover state from {API_URL} yet.</span>
          )}
        </section>
        <section className="mapwrap">
          {map ? (
            <Minimap map={map} rover={rover} trail={trail} markers={markers} highlights={highlights} showPois={showPois} />
          ) : (
            <p className="muted">{error || 'Loading minimap…'}</p>
          )}
        </section>
        <section className="legend">
          <span><i className="sw rover" />rover</span>
          <span><i className="sw hl" />agent results / targets</span>
          <span><i className="sw mk" />markers ({markers.length})</span>
          <span><i className="sw poi" />places</span>
          <label><input type="checkbox" checked={showPois} onChange={(e) => setShowPois(e.target.checked)} /> labels</label>
          <button className="clear" onClick={() => setHighlights([])} disabled={!highlights.length}>clear highlights</button>
        </section>
        <section className="log">
          <h2>Tool calls</h2>
          {log.length === 0 && <p className="muted">Nothing yet. Ask the copilot something.</p>}
          <ul>
            {log.map((l) => (
              <li key={l.id} className={l.status}>
                <b>{l.name}</b> <span>{l.status === 'running' ? 'running…' : l.text}</span>
              </li>
            ))}
          </ul>
        </section>
        <footer className="muted foot">Agent {AGENT_BASE} · API {API_URL}. Positions are site-frame metres (x east, y north).</footer>
      </main>
      <div className="chat">
        <ChatPanel agentUrl={AGENT_BASE} getContext={getContext} onToolEvent={onToolEvent} />
      </div>
    </div>
  );
}

function Stat({ k, v }: { k: string; v: string }) {
  return <div className="stat"><span>{k}</span><b>{v}</b></div>;
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
