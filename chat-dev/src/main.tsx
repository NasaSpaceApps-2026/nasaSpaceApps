import { StrictMode, useCallback, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ChatPanel, type ToolEvent } from '../../web/src/panels/chat';

const AGENT_URL = new URLSearchParams(location.search).get('agent') ?? 'http://localhost:8766';

function App() {
  const [x, setX] = useState(120);
  const [y, setY] = useState(-340);
  const [log, setLog] = useState<string[]>([]);
  const ctx = useRef({ x, y });
  ctx.current = { x, y };

  const getContext = useCallback(
    () => ({ rover: { x: ctx.current.x, y: ctx.current.y, heading_deg: 0 }, camera: { x: ctx.current.x, y: ctx.current.y - 50, z: 80 } }),
    [],
  );
  const onToolEvent = useCallback((e: ToolEvent) => {
    const line = `${new Date().toLocaleTimeString()} ${e.phase} ${e.name}${e.phase === 'result' ? ` ${e.isError ? '[error]' : '[ok]'} ${e.summary ?? ''}` : ' ' + JSON.stringify(e.args)}`;
    console.log('onToolEvent', e);
    setLog((l) => [line, ...l].slice(0, 100));
  }, []);

  return (
    <div style={{ display: 'flex', height: '100vh', background: '#0c0908', color: '#ddd', fontFamily: 'system-ui, sans-serif' }}>
      <main style={{ flex: 1, padding: 16, overflow: 'auto' }}>
        <h1 style={{ fontSize: 18, margin: '0 0 12px' }}>Viewer placeholder</h1>
        <fieldset style={{ border: '1px solid #3d2e25', marginBottom: 16 }}>
          <legend>Fake context sent with each message (site-frame metres)</legend>
          <label>
            Rover x{' '}
            <input type="number" value={x} onChange={(e) => setX(Number(e.target.value))} style={{ width: 90 }} />
          </label>{' '}
          <label>
            Rover y{' '}
            <input type="number" value={y} onChange={(e) => setY(Number(e.target.value))} style={{ width: 90 }} />
          </label>
        </fieldset>
        <h2 style={{ fontSize: 15 }}>onToolEvent log</h2>
        <pre style={{ fontSize: 12, whiteSpace: 'pre-wrap' }} aria-live="polite">{log.length ? log.join('\n') : 'No tool events yet.'}</pre>
      </main>
      <div style={{ width: 380, flex: 'none', height: '100%' }}>
        <ChatPanel agentUrl={AGENT_URL} getContext={getContext} onToolEvent={onToolEvent} />
      </div>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
