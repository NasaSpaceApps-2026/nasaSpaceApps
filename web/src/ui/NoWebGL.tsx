import './ui.css'

export function NoWebGL({ detail }: { detail?: string }) {
  return (
    <div className="nogl" role="alert">
      <div className="nogl__card">
        <h1>Ares Atlas</h1>
        <p>
          Your browser could not start 3D graphics (WebGL). Try Chrome or Brave with hardware acceleration on, or open the
          copilot without 3D at <a href="http://localhost:5199">http://localhost:5199</a>.
        </p>
        {detail && <p className="nogl__detail">{detail}</p>}
      </div>
    </div>
  )
}
