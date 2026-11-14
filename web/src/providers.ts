// Custom Cesium providers that feed our site tiles/DEM into the engine.
import * as Cesium from 'cesium'
import type { Site } from './site'
import { R, SPHERE } from './site'

const GRID = 65

/** Terrain: heights from our 2 m DEM. Flat (site mean) outside the site, where the globe is hidden. */
export function siteTerrain(site: Site): Cesium.CustomHeightmapTerrainProvider {
  const scheme = new Cesium.GeographicTilingScheme({ ellipsoid: SPHERE })
  const flat = (site.manifest as unknown as { elev_min?: number }).elev_min ?? 0
  return new Cesium.CustomHeightmapTerrainProvider({
    width: GRID,
    height: GRID,
    tilingScheme: scheme,
    callback: (x: number, y: number, level: number) => {
      const r = scheme.tileXYToRectangle(x, y, level)
      const out = new Float32Array(GRID * GRID)
      for (let i = 0; i < GRID; i++) {
        const lat = r.north - ((r.north - r.south) * i) / (GRID - 1)
        for (let j = 0; j < GRID; j++) {
          const lon = r.west + ((r.east - r.west) * j) / (GRID - 1)
          const h = site.elevation(lon, lat)
          out[i * GRID + j] = Number.isNaN(h) ? flat : h
        }
      }
      return out
    },
  })
}

const TILE = 256
const blank = (() => {
  const c = document.createElement('canvas')
  c.width = c.height = TILE
  return c
})()

/**
 * Imagery: composes Cesium geographic tiles from our site quadtree tiles (/tiles/{layer}/{z}/{x}/{y}.webp).
 * The site frame is equirectangular, so each Cesium tile maps to a plain rectangle in site metres.
 */
export function siteImagery(site: Site, layerId: string, maxLevel: number): Cesium.ImageryProvider {
  const scheme = new Cesium.GeographicTilingScheme({ ellipsoid: SPHERE })
  const size = site.manifest.size_m
  const half = size / 2
  const cache = new Map<string, Promise<ImageBitmap | null>>()
  const getTile = (z: number, tx: number, ty: number) => {
    const key = `${z}/${tx}/${ty}`
    let p = cache.get(key)
    if (!p) {
      p = fetch(`/tiles/${layerId}/${key}.webp`)
        .then((r) => (r.ok ? r.blob() : null))
        .then((b) => (b ? createImageBitmap(b) : null))
        .catch(() => null)
      cache.set(key, p)
    }
    return p
  }

  const provider = {
    rectangle: site.rectangle,
    tileWidth: TILE,
    tileHeight: TILE,
    minimumLevel: 0,
    maximumLevel: 24,
    tilingScheme: scheme,
    tileDiscardPolicy: undefined,
    errorEvent: new Cesium.Event(),
    credit: new Cesium.Credit('NASA/JPL/UArizona/USGS'),
    proxy: undefined,
    hasAlphaChannel: true,
    readyEvent: new Cesium.Event(),
    ready: true,
    async requestImage(x: number, y: number, level: number) {
      const r = scheme.tileXYToRectangle(x, y, level)
      const [xa, yn] = site.lonLatToXY(r.west, r.north) // tile west edge, north edge (site m)
      const [xb, ys] = site.lonLatToXY(r.east, r.south)
      const wm = xb - xa // tile width in site metres
      if (xb < -half || xa > half || yn < -half || ys > half) return blank
      // our level l has 512 px per (size/2^l) m; need source px size <= target px size
      const l = Math.max(0, Math.min(maxLevel, Math.ceil(Math.log2((size / 512) / (wm / TILE)))))
      const ts = size / 2 ** l
      const tx0 = Math.max(0, Math.floor((xa + half) / ts)), tx1 = Math.min(2 ** l - 1, Math.floor((xb + half) / ts))
      const ty0 = Math.max(0, Math.floor((half - yn) / ts)), ty1 = Math.min(2 ** l - 1, Math.floor((half - ys) / ts))
      const canvas = document.createElement('canvas')
      canvas.width = canvas.height = TILE
      const ctx = canvas.getContext('2d')!
      ctx.imageSmoothingQuality = 'high'
      const jobs: Promise<void>[] = []
      for (let ty = ty0; ty <= ty1; ty++) {
        for (let tx = tx0; tx <= tx1; tx++) {
          jobs.push(
            getTile(l, tx, ty).then((img) => {
              if (!img) return
              const sx0 = -half + tx * ts, sy0 = half - ty * ts // source tile west/north edge (site m)
              const dx = ((sx0 - xa) / wm) * TILE
              const dy = ((yn - sy0) / (yn - ys)) * TILE
              ctx.drawImage(img, dx, dy, (ts / wm) * TILE, (ts / (yn - ys)) * TILE)
            }),
          )
        }
      }
      await Promise.all(jobs)
      return canvas
    },
    pickFeatures: () => undefined,
  }
  void R
  return provider as unknown as Cesium.ImageryProvider
}
