// Paste into Rithvik's Site.elevation and Yousef's sampleHeight.
// Inside the bubble, use the vertex grid written by `vision_loop drive`.
// Outside it, use the existing pixel-center dem2m.u16 grid.
// Wheel height, pitch, roll, and later suspension all read this number.
// Texture does not enter this function. guessed.u8 is a separate caution flag.

const STEP = 0.01;

/** Vertex grid from `vision_loop drive` (`height.json` + little-endian uint16). */
export interface VertexGrid {
  x0: number;
  y0: number;
  res_m: number;
  base: number;
  width: number;
  height: number;
  values: ArrayLike<number>;
}

/** Pixel-centered Cesium site grid (`dem2m.json` + `dem2m.u16`). */
export interface PixelCenterGrid {
  size_m: number;
  res_m: number;
  base: number;
  width: number;
  height: number;
  center_x: number;
  center_y: number;
  values: ArrayLike<number>;
}

/**
 * Same shape as `web/src/rover/heightTiles.ts` `HeightSampler`.
 * Copied here so this file has no Cesium or `web/` imports.
 */
export interface HeightSampler {
  sampleHeight(x: number, y: number): number;
  contains?(x: number, y: number): boolean;
}

function bilinear(
  values: ArrayLike<number>,
  width: number,
  height: number,
  cf: number,
  rf: number,
): number {
  if (cf < 0 || rf < 0 || cf > width - 1 || rf > height - 1) return NaN;
  const c0 = Math.floor(cf);
  const r0 = Math.floor(rf);
  const c1 = Math.min(c0 + 1, width - 1);
  const r1 = Math.min(r0 + 1, height - 1);
  const fc = cf - c0;
  const fr = rf - r0;
  const v00 = values[r0 * width + c0];
  const v01 = values[r0 * width + c1];
  const v10 = values[r1 * width + c0];
  const v11 = values[r1 * width + c1];
  return (v00 * (1 - fc) + v01 * fc) * (1 - fr) + (v10 * (1 - fc) + v11 * fc) * fr;
}

export function sampleVertex(x: number, y: number, bubble: VertexGrid): number {
  const cf = (x - bubble.x0) / bubble.res_m;
  const rf = (bubble.y0 - y) / bubble.res_m;
  const value = bilinear(bubble.values, bubble.width, bubble.height, cf, rf);
  if (value !== value) return value;
  return bubble.base + STEP * value;
}

export function samplePixelCenter(x: number, y: number, site: PixelCenterGrid): number {
  const half = site.size_m / 2.0;
  const cf = (x - site.center_x + half) / site.res_m - 0.5;
  const rf = (half - (y - site.center_y)) / site.res_m - 0.5;
  const value = bilinear(site.values, site.width, site.height, cf, rf);
  if (value !== value) return value;
  return site.base + STEP * value;
}

export function sampleDrive(
  x: number,
  y: number,
  bubble: VertexGrid,
  site?: PixelCenterGrid,
): number {
  const got = sampleVertex(x, y, bubble);
  if (got === got) return got;
  if (site == null) return NaN;
  return samplePixelCenter(x, y, site);
}

export function slopeDeg(
  x: number,
  y: number,
  bubble: VertexGrid,
  site?: PixelCenterGrid,
): number {
  const center = sampleDrive(x, y, bubble, site);
  let right = sampleDrive(x + 2, y, bubble, site);
  let left = sampleDrive(x - 2, y, bubble, site);
  let north = sampleDrive(x, y + 2, bubble, site);
  let south = sampleDrive(x, y - 2, bubble, site);
  if (right !== right) right = center;
  if (left !== left) left = center;
  if (north !== north) north = center;
  if (south !== south) south = center;
  const gx = (right - left) / 4;
  const gy = (north - south) / 4;
  return (Math.atan(Math.hypot(gx, gy)) * 180) / Math.PI;
}

/** HeightSampler: bubble vertex height when finite, otherwise `tiles.sampleHeight`. */
export function bubbleThenTiles(bubble: VertexGrid, tiles: HeightSampler): HeightSampler {
  return {
    sampleHeight(x: number, y: number): number {
      const height = sampleVertex(x, y, bubble);
      if (Number.isFinite(height)) return height;
      return tiles.sampleHeight(x, y);
    },
  };
}
