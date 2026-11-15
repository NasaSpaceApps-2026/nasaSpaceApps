import { useMemo, useState } from 'react';
import type { ToolCall } from './types';
import { humanToolName, prettyJson, summarizeArgs } from './format';
import { extractFromResult, type Feature } from './features';

const MAX_ROWS = 8;
const RESULT_PREVIEW = 3000;

export function ToolCard({ call, onFocusPlace }: { call: ToolCall; onFocusPlace?: (f: Feature) => void }) {
  const [open, setOpen] = useState(false);
  const [full, setFull] = useState(false);
  const rows = useMemo(
    () => (onFocusPlace && call.status === 'ok' ? extractFromResult({ name: call.name, args: call.args, structured: call.structured, result: call.result }).features.slice(0, MAX_ROWS) : []),
    [onFocusPlace, call.status, call.name, call.args, call.structured, call.result],
  );
  const result = call.result ?? (call.status === 'running' ? 'Waiting for result…' : call.summary ?? 'No result');
  const panelId = `ares-tool-${call.id.replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const statusText = call.status === 'running' ? 'Running' : call.status === 'ok' ? 'Done' : 'Failed';
  return (
    <div className={`ares-tool ares-tool--${call.status}`}>
      <button
        type="button"
        className="ares-tool__head"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="ares-tool__icon" aria-hidden="true">
          {call.status === 'running' ? <span className="ares-spinner" /> : call.status === 'ok' ? '✓' : '!'}
        </span>
        <span className="ares-tool__main">
          <span className="ares-tool__name">
            {humanToolName(call.name)} <span className="ares-sr">{statusText}</span>
          </span>
          <span className="ares-tool__args">{summarizeArgs(call.args)}</span>
          {call.summary && <span className="ares-tool__summary">{call.summary}</span>}
        </span>
        <span className="ares-tool__chev" aria-hidden="true">{open ? '▾' : '▸'}</span>
      </button>
      {rows.length > 0 && onFocusPlace && (
        <ul className="ares-tool__rows" aria-label="Highlighted results (click to fly there)">
          {rows.map((f) => (
            <li key={f.id}>
              <button type="button" className="ares-tool__row" onClick={() => onFocusPlace(f)} title="Fly the camera here">
                <span className="ares-tool__pin" aria-hidden="true">&#9678;</span>
                <span>{f.label}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {open && (
        <div id={panelId} className="ares-tool__body">
          <div className="ares-tool__label">Arguments</div>
          <pre>{prettyJson(call.args)}</pre>
          <div className="ares-tool__label">Result</div>
          <pre>{result.length > RESULT_PREVIEW && !full ? result.slice(0, RESULT_PREVIEW) + '\n…' : result}</pre>
          {result.length > RESULT_PREVIEW && (
            <button type="button" className="ares-tool__more" onClick={() => setFull((f) => !f)}>
              {full ? 'Show less' : `Show all (${result.length.toLocaleString()} characters)`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
