import { BUBBLES, covers, localMeters, type Bubble } from './bubbles.ts'

export interface StreetDrop {
  world: Bubble['id']
  x: number
  y: number
  heading: number
}

export function dropOnBubbles(latDeg: number, lonDeg: number): StreetDrop | null {
  const bubble = BUBBLES.find((item) => covers(item, latDeg, lonDeg))
  if (!bubble) return null
  const { x, y } = localMeters(bubble, latDeg, lonDeg)
  return { world: bubble.id, x, y, heading: 0 }
}