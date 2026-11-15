// "Now showing" cards: legend, source, caveat, opacity and clear for the active overlays (main + science slot).
import type { OverlayState, SlotState } from './OverlayManager'
import type { Slot } from './overlayApi'

interface Props {
  state: OverlayState
  onOpacity: (slot: Slot, a: number) => void
  onClear: (slot: Slot) => void
  compact?: boolean
}

function Card({ slot, s, compact, onOpacity, onClear }: { slot: Slot; s: SlotState; compact?: boolean; onOpacity: Props['onOpacity']; onClear: Props['onClear'] }) {
  if (!s.kind) return null
  const title = s.meta?.title ?? s.kind
  return (
    <section className={`exov${compact ? ' exov--compact' : ''}`} aria-label={`Overlay: ${title}`}>
      <header className="exov__head">
        <h4>{title}</h4>
        {s.loading && <span className="spinner" role="status" aria-label="Loading overlay" />}
        <button type="button" className="exov__x" onClick={() => onClear(slot)} aria-label={`Hide ${title}`} title="Hide this overlay">&times;</button>
      </header>
      {s.error && <p className="exov__err" role="alert">{s.error}</p>}
      {s.meta && s.meta.legend.length > 0 && (
        <ul className="exov__legend">
          {s.meta.legend.map((l) => (
            <li key={l.label}><span className="exov__sw" style={{ background: l.color }} aria-hidden="true" />{l.label}</li>
          ))}
        </ul>
      )}
      {!compact && s.meta?.source && <p className="exov__src" title={s.meta.source}>Source: {s.meta.source}</p>}
      {s.meta?.caveat && <p className="exov__cav">{s.meta.caveat}</p>}
      {!compact && (
        <label className="exov__op">
          <span>Opacity</span>
          <input type="range" min={5} max={100} value={Math.round(s.opacity * 100)} onChange={(e) => onOpacity(slot, Number(e.target.value) / 100)} aria-label="Overlay opacity" />
        </label>
      )}
    </section>
  )
}

export function ActiveOverlays({ state, onOpacity, onClear, compact }: Props) {
  if (!state.main.kind && !state.science.kind) return null
  return (
    <div className="exov__stack">
      <Card slot="science" s={state.science} compact={compact} onOpacity={onOpacity} onClear={onClear} />
      <Card slot="main" s={state.main} compact={compact} onOpacity={onOpacity} onClear={onClear} />
    </div>
  )
}
