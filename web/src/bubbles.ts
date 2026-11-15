export interface Bubble {
  id: 'crater' | 'olympus'
  title: string
  lat: number
  lon: number
  sizeM: number
}

const R = 3396190

export const BUBBLES: Bubble[] = [
  { id: 'crater', title: 'Jezero fan', lat: 18.4818, lon: 77.4358, sizeM: 1800 },
  { id: 'olympus', title: 'Olympus Mons scarp', lat: 21.5548278127083, lon: 222.31978845442714, sizeM: 2236 },
]

function lonDeltaDeg(lonDeg: number, lon0Deg: number): number {
  const d = lonDeg - lon0Deg
  return ((d + 180) % 360 + 360) % 360 - 180
}

export function localMeters(bubble: Bubble, latDeg: number, lonDeg: number): { x: number; y: number } {
  const lat0 = bubble.lat * Math.PI / 180
  const x = R * Math.cos(lat0) * lonDeltaDeg(lonDeg, bubble.lon) * Math.PI / 180
  const y = R * (latDeg - bubble.lat) * Math.PI / 180
  return { x, y }
}

export function covers(bubble: Bubble, latDeg: number, lonDeg: number): boolean {
  const { x, y } = localMeters(bubble, latDeg, lonDeg)
  const half = bubble.sizeM / 2
  return Math.abs(x) <= half && Math.abs(y) <= half
}