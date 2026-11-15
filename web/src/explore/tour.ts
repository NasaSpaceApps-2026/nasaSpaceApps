// Guided tour: stops, captions, camera and overlay. Every caption states only facts from our data and docs
// (cited in `cite`): docs/lake-levels.md, docs/science-validity.md and the sample POI descriptions (NASA sample list).
import type { OverlayKind, OverlayParams } from './overlayApi'

export interface TourStop {
  id: string
  title: string
  caption: string
  cite: string
  /** Site metres (x east, y north). */
  at: [number, number]
  range_m: number
  pitch_deg: number
  /** Overlay to show at this stop (null clears the tour's overlays). */
  overlay: { kind: OverlayKind; params?: OverlayParams } | null
}

export const TOUR_STOPS: TourStop[] = [
  {
    id: 'landing', title: 'Landing site', at: [1844, -433], range_m: 1500, pitch_deg: -40,
    caption: 'Perseverance touched down here, at 18.4447 N, 77.4508 E. Our first rover waypoint sits 6.5 m from that published point, so the map and the rover agree.',
    cite: 'POI octavia_e_butler_landing; docs/science-validity.md check 3', overlay: null,
  },
  {
    id: 'seitah', title: 'South Séítah', at: [1360, -1108], range_m: 1700, pitch_deg: -42,
    caption: 'Samples 4 to 7 (sols 262 to 337) are igneous cores, and the rover confirmed olivine and carbonate in them. Orbit sees the olivine; the orbital carbonate map misses it here.',
    cite: 'Sample POIs 4-7 (NASA sample list); docs/science-validity.md stress tests', overlay: { kind: 'evidence', params: { layer: 'olivine' } },
  },
  {
    id: 'delta', title: 'Delta front', at: [-833, 170], range_m: 1800, pitch_deg: -40,
    caption: 'Samples 10 to 16 (sols 490 to 631) are sedimentary cores. The delta layers here record lake levels falling from −2410 m to −2500 m.',
    cite: 'Sample POIs 10-16; docs/lake-levels.md (Mangold et al. 2024)', overlay: { kind: 'geology' },
  },
  {
    id: 'belva', title: 'Belva crater', at: [-2137, 1660], range_m: 1500, pitch_deg: -40,
    caption: 'Images inside Belva crater located the highest delta layers seen so far, at −2410 m. That is a minimum lake level: beds above may be eroded.',
    cite: 'docs/lake-levels.md (delta_top_2410, Mangold et al. 2024)', overlay: { kind: 'geology' },
  },
  {
    id: 'margin', title: 'Margin unit', at: [-3887, 2020], range_m: 1800, pitch_deg: -40,
    caption: 'Samples 22 and 23 (sols 923 and 949) were taken here: sedimentary cores, near the western edge of our 8 km map.',
    cite: 'Sample POIs 22-23; region_margin_unit POI', overlay: { kind: 'geology' },
  },
  {
    id: 'lake', title: 'The lake at −2410 m', at: [-250, -450], range_m: 9500, pitch_deg: -45,
    caption: 'Flood today’s terrain to −2410 m, the highest delta-recorded level: 22 of 23 sample sites and 91% of the delta fan lie below it. This is the present-day surface, not the ancient lake floor.',
    cite: 'docs/science-validity.md (ancient lake at −2410 m); docs/lake-levels.md method limits', overlay: { kind: 'lake', params: { level: -2410 } },
  },
]
