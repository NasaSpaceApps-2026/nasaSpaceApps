import { useEffect, useRef, useState } from 'react'
import type { Place } from '../places'
import './ui.css'

export type Mode = 'mars' | 'jezero' | 'drive'

export interface LayerRow {
  id: string
  title: string
  sub: string
  on: boolean
  falseColour?: boolean
  title_attr?: string
}

interface Props {
  mode: Mode
  ready: boolean
  onMode: (m: Mode) => void
  query: string
  hits: Place[]
  onSearch: (q: string) => void
  onPick: (p: Place) => void
  layers: LayerRow[]
  onToggleLayer: (id: string) => void
}

const MODES: { id: Mode; label: string; hint: string; key: string }[] = [
  { id: 'mars', label: 'Mars', hint: 'Planet globe', key: '1' },
  { id: 'jezero', label: 'Jezero', hint: 'Site analysis: terrain, imagery, copilot highlights', key: '2' },
  { id: 'drive', label: 'Drive', hint: 'Street view with the buggy', key: '3' },
]

const Icon = ({ d }: { d: string }) => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={d} />
  </svg>
)

export function TopBar({ mode, ready, onMode, query, hits, onSearch, onPick, layers, onToggleLayer }: Props) {
  const [open, setOpen] = useState<'layers' | 'help' | null>(null)
  const [showHits, setShowHits] = useState(false)
  const bar = useRef<HTMLElement>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setOpen(null); setShowHits(false) }
    }
    const onDown = (e: PointerEvent) => {
      if (bar.current && !bar.current.contains(e.target as Node)) { setOpen(null); setShowHits(false) }
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('pointerdown', onDown)
    return () => { window.removeEventListener('keydown', onKey); window.removeEventListener('pointerdown', onDown) }
  }, [])

  const layersOn = mode === 'jezero'
  const toggle = (k: 'layers' | 'help') => setOpen(open === k ? null : k)

  return (
    <header className="topbar" ref={bar}>
      <span className="topbar__brand">Ares Atlas</span>

      <div className="seg" role="group" aria-label="View">
        {MODES.map((m) => (
          <button key={m.id} type="button" className="seg__btn" aria-pressed={mode === m.id} disabled={!ready}
            title={`${m.hint} (${m.key})`} onClick={() => onMode(m.id)}>
            {m.label}
          </button>
        ))}
      </div>

      <div className="search">
        <input type="search" placeholder="Search a place or rover sol" aria-label="Search places" value={query} disabled={!ready}
          onChange={(e) => { onSearch(e.target.value); setShowHits(true) }}
          onFocus={() => setShowHits(true)}
          onKeyDown={(e) => { if (e.key === 'Enter' && hits[0]) { onPick(hits[0]); setShowHits(false) } }} />
        {showHits && hits.length > 0 && (
          <ul className="search__hits" role="listbox">
            {hits.map((h) => (
              <li key={h.id}>
                <button type="button" role="option" aria-selected="false" onClick={() => { onPick(h); setShowHits(false) }}>
                  <span>{h.name}</span>
                  <small>{h.kind === 'waypoint' ? 'Rover waypoint' : 'IAU/USGS name'}</small>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="topbar__tools">
        <div className="pop">
          <button type="button" className="iconbtn" aria-label="Layers" aria-expanded={open === 'layers'} disabled={!layersOn}
            title={layersOn ? 'Layers' : 'Layers apply in Jezero'} onClick={() => toggle('layers')}>
            <Icon d="M12 3 3 8l9 5 9-5-9-5ZM3 12l9 5 9-5M3 16l9 5 9-5" />
          </button>
          {open === 'layers' && layersOn && (
            <div className="popover" role="dialog" aria-label="Layers">
              <h3>Layers</h3>
              {layers.map((l) => (
                <label key={l.id} className="check" title={l.title_attr}>
                  <input type="checkbox" checked={l.on} onChange={() => onToggleLayer(l.id)} />
                  <span>
                    {l.title}
                    {l.falseColour && <em className="badge">false colour</em>}
                    <small>{l.sub}</small>
                  </span>
                </label>
              ))}
            </div>
          )}
        </div>
        <div className="pop">
          <button type="button" className="iconbtn" aria-label="Help" aria-expanded={open === 'help'} title="Controls" onClick={() => toggle('help')}>
            <span aria-hidden="true" className="iconbtn__q">?</span>
          </button>
          {open === 'help' && (
            <div className="popover" role="dialog" aria-label="Controls">
              <h3>Controls</h3>
              <dl className="keys">
                <dt>1 / 2 / 3</dt><dd>Mars / Jezero / Drive</dd>
                <dt>Drag</dt><dd>Rotate the globe or site</dd>
                <dt>Right-drag</dt><dd>Tilt</dd>
                <dt>Wheel</dt><dd>Zoom</dd>
                <dt>W A S D</dt><dd>Drive the buggy (Drive); arrow keys also work</dd>
                <dt>R</dt><dd>Reset the buggy (Drive)</dd>
                <dt>Esc</dt><dd>Close this popover</dd>
              </dl>
              <p className="note">Imagery is false colour. Slope limits and walking speeds are assumed values, not mission data.</p>
            </div>
          )}
        </div>
      </div>
    </header>
  )
}
