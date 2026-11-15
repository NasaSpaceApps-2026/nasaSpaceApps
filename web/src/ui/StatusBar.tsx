import type { Ref } from 'react'
import './ui.css'

interface Props {
  left: string
  right: string
  creditsRef: Ref<HTMLDivElement>
  follow?: { on: boolean; toggle: () => void }
}

/** Bottom strip: cursor position, rover state (when there is one), and Cesium's credit container (data attribution). */
export function StatusBar({ left, right, creditsRef, follow }: Props) {
  return (
    <footer className="statusbar">
      <span className="statusbar__text" aria-live="off">{left}</span>
      <span className="statusbar__text statusbar__rover">{right}</span>
      {follow && (
        <button type="button" className="statusbar__follow" aria-pressed={follow.on} onClick={follow.toggle}
          title="Camera follows the rover. Drag the map to take the camera back.">
          Follow rover
        </button>
      )}
      <div className="statusbar__credits" ref={creditsRef} />
    </footer>
  )
}
