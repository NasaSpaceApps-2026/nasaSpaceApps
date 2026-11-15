import { useState } from 'react';
import type { Highlight } from './highlights';
import type { Marker, Minimap as MinimapData, RoverState } from './live';

const V = 1000;

export function Minimap(props: {
  map: MinimapData;
  rover: RoverState | null;
  trail: Array<[number, number]>;
  markers: Marker[];
  highlights: Highlight[];
  showPois: boolean;
}) {
  const { map, rover, trail, markers, highlights, showPois } = props;
  const half = map.size_m / 2;
  const sx = (x: number) => ((x + half) / map.size_m) * V;
  const sy = (y: number) => ((half - y) / map.size_m) * V;
  const [hover, setHover] = useState<{ x: number; y: number } | null>(null);
  const metresPerUnit = map.size_m / V;
  const bar = niceBar(map.size_m / 5);

  return (
    <div className="mm">
      <svg
        viewBox={`0 0 ${V} ${V}`}
        role="img"
        aria-label="Site minimap with rover, markers and the agent's latest results"
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          setHover({ x: ((e.clientX - r.left) / r.width) * map.size_m - half, y: half - ((e.clientY - r.top) / r.height) * map.size_m });
        }}
        onMouseLeave={() => setHover(null)}
      >
        <image href="/minimap/current.png" x="0" y="0" width={V} height={V} />
        {map.traverse.map((seg, i) => (
          <polyline key={i} points={seg.map(([x, y]) => `${sx(x)},${sy(y)}`).join(' ')} className="trav" />
        ))}
        {showPois && map.pois.map((p, i) => (
          <g key={p.id ?? i}>
            <rect x={sx(p.x) - 4} y={sy(p.y) - 4} width="8" height="8" className="poi" />
            {p.type !== 'sample_site' && <text x={sx(p.x) + 9} y={sy(p.y) + 4} className="lbl">{shortName(p.name ?? p.id ?? '')}</text>}
          </g>
        ))}
        {trail.length > 1 && <polyline points={trail.map(([x, y]) => `${sx(x)},${sy(y)}`).join(' ')} className="trail" />}
        {markers.map((m) => (
          <g key={m.id}>
            <path d={`M${sx(m.x)},${sy(m.y)} l-7,-16 h14 z`} className="marker" />
            <text x={sx(m.x) + 10} y={sy(m.y) - 8} className="lbl">{m.id}</text>
          </g>
        ))}
        {highlights.map((h) => (
          <g key={h.key} className={`hl hl-${h.kind}`}>
            <circle cx={sx(h.x)} cy={sy(h.y)} r="18" className="ring" />
            <circle cx={sx(h.x)} cy={sy(h.y)} r="9" className="dot" />
            <text x={sx(h.x) + 12} y={sy(h.y) - 10} className="lbl hl-lbl">{h.label}</text>
          </g>
        ))}
        {rover && (
          <g transform={`translate(${sx(rover.x)} ${sy(rover.y)}) rotate(${rover.heading_deg ?? 0})`} className="rover">
            <circle r="16" className="rover-halo" />
            <path d="M0,-15 L10,11 L0,6 L-10,11 Z" />
          </g>
        )}
        <g transform={`translate(24 ${V - 28})`} className="scale">
          <rect width={bar / metresPerUnit} height="6" />
          <text y="-6">{bar >= 1000 ? `${bar / 1000} km` : `${bar} m`}</text>
        </g>
        <text x={V - 14} y="26" textAnchor="end" className="north">N ↑</text>
      </svg>
      <div className="mm-cap">
        {hover ? `x ${hover.x.toFixed(0)}, y ${hover.y.toFixed(0)} m` : `${map.name}, ${map.size_m.toFixed(0)} m square, centre ${map.lat0.toFixed(4)} N ${map.lon0.toFixed(4)} E`}
      </div>
    </div>
  );
}

function niceBar(target: number): number {
  const p = 10 ** Math.floor(Math.log10(target));
  for (const m of [5, 2, 1]) if (m * p <= target) return m * p;
  return p;
}

function shortName(s: string): string {
  return s.length > 22 ? s.slice(0, 21) + '…' : s;
}
