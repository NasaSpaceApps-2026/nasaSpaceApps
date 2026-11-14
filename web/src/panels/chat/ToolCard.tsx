import { useState } from 'react';
import type { ToolCall } from './types';
import { humanToolName, prettyJson, summarizeArgs } from './format';

export function ToolCard({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(false);
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
      {open && (
        <div id={panelId} className="ares-tool__body">
          <div className="ares-tool__label">Arguments</div>
          <pre>{prettyJson(call.args)}</pre>
          <div className="ares-tool__label">Result</div>
          <pre>{call.result ?? (call.status === 'running' ? 'Waiting for result…' : call.summary ?? 'No result')}</pre>
        </div>
      )}
    </div>
  );
}
