/**
 * Contract 2 (PRD): RGB-encoded height tiles and the manifest.
 *
 * - Region: square of side manifest.size_m centred on the site origin (0, 0).
 * - Quadtree: level z has 2^z x 2^z tiles; tile x counts columns from the
 *   west, tile y counts rows from the north.
 * - Height tiles are 257 x 257 posts with shared edges: post (i, j) of tile
 *   (z, tx, ty) sits at x = west + i * ts/256, y = north - j * ts/256 where
 *   ts = size_m / 2^z.
 * - Decode: elev_m = -10000 + 0.01 * (R * 65536 + G * 256 + B).
 *
 * The rover only needs `HeightSampler`; the viewer's terrain module can supply
 * its own implementation instead of this one.
 */

export interface HeightSampler {
  /** Ground elevation (site-frame metres) at site-frame (x, y). */
  sampleHeight(x: number, y: number): number;
  /** Optional: false when (x, y) has no terrain data (outside the region). */
  contains?(x: number, y: number): boolean;
}

export interface Manifest {
  site: string;
  fixture?: boolean;
  lat0: number;
  lon0: number;
  radius_m: number;
  size_m: number;
  elev_min: number;
  elev_max: number;
  height: { max_level: number; tile_px: number };
  layers: Array<{
    id: string;
    title?: string;
    mission?: string;
    instrument?: string;
    max_level: number;
    kind?: string;
    credit?: string;
    url?: string;
    legend?: Array<[number | null, string]>;
  }>;
  [key: string]: unknown;
}

export interface TileBounds {
  west: number;
  north: number;
  size: number;
}

/** Site-frame extent of tile (z, tx, ty). */
export function tileBounds(sizeM: number, z: number, tx: number, ty: number): TileBounds {
  const ts = sizeM / 2 ** z;
  return { west: -sizeM / 2 + tx * ts, north: sizeM / 2 - ty * ts, size: ts };
}

/** Decode an RGBA (or RGB with stride 4) pixel buffer into elevations (row 0 = north). */
export function decodeHeightRGBA(rgba: ArrayLike<number>, w: number, h: number, stride = 4): Float32Array {
  const out = new Float32Array(w * h);
  for (let i = 0, p = 0; i < w * h; i++, p += stride) {
    out[i] = -10000 + 0.01 * (rgba[p] * 65536 + rgba[p + 1] * 256 + rgba[p + 2]);
  }
  return out;
}

interface Tile {
  z: number;
  tx: number;
  ty: number;
  px: number;
  data: Float32Array;
}

/**
 * Bilinear sampler over a sparse quadtree of decoded height tiles. Uses the
 * highest loaded level that covers the point; outside the region it clamps to
 * the nearest edge.
 */
export class TileHeightSampler implements HeightSampler {
  readonly sizeM: number;
  private tiles = new Map<string, Tile>();
  private levels: number[] = []; // loaded levels, descending

  constructor(sizeM: number) {
    this.sizeM = sizeM;
  }

  addTile(z: number, tx: number, ty: number, data: Float32Array, px = Math.round(Math.sqrt(data.length))): void {
    this.tiles.set(`${z}/${tx}/${ty}`, { z, tx, ty, px, data });
    if (!this.levels.includes(z)) this.levels = [...this.levels, z].sort((a, b) => b - a);
  }

  hasTile(z: number, tx: number, ty: number): boolean {
    return this.tiles.has(`${z}/${tx}/${ty}`);
  }

  getTile(z: number, tx: number, ty: number): Float32Array | undefined {
    return this.tiles.get(`${z}/${tx}/${ty}`)?.data;
  }

  contains(x: number, y: number): boolean {
    const h = this.sizeM / 2;
    return x >= -h && x <= h && y >= -h && y <= h;
  }

  sampleHeight(x: number, y: number): number {
    const half = this.sizeM / 2;
    const cx = Math.min(half, Math.max(-half, x));
    const cy = Math.min(half, Math.max(-half, y));
    for (const z of this.levels) {
      const n = 2 ** z;
      const ts = this.sizeM / n;
      const fx = (cx + half) / ts;
      const fy = (half - cy) / ts;
      const tx = Math.min(n - 1, Math.floor(fx));
      const ty = Math.min(n - 1, Math.floor(fy));
      const t = this.tiles.get(`${z}/${tx}/${ty}`);
      if (!t) continue;
      const m = t.px - 1;
      const u = (fx - tx) * m;
      const v = (fy - ty) * m;
      const i0 = Math.min(m - 1, Math.floor(u));
      const j0 = Math.min(m - 1, Math.floor(v));
      const du = u - i0;
      const dv = v - j0;
      const d = t.data;
      const r0 = j0 * t.px;
      const r1 = r0 + t.px;
      const a = d[r0 + i0] * (1 - du) + d[r0 + i0 + 1] * du;
      const b = d[r1 + i0] * (1 - du) + d[r1 + i0 + 1] * du;
      return a * (1 - dv) + b * dv;
    }
    return NaN;
  }
}

/** Decode a PNG Blob into elevations in the browser (no colour management). */
export async function decodeHeightPng(blob: Blob): Promise<{ data: Float32Array; px: number }> {
  const bmp = await createImageBitmap(blob, { premultiplyAlpha: 'none', colorSpaceConversion: 'none' });
  const w = bmp.width;
  const h = bmp.height;
  let ctx: OffscreenCanvasRenderingContext2D | CanvasRenderingContext2D | null;
  if (typeof OffscreenCanvas !== 'undefined') {
    ctx = new OffscreenCanvas(w, h).getContext('2d', { willReadFrequently: true });
  } else {
    const c = document.createElement('canvas');
    c.width = w;
    c.height = h;
    ctx = c.getContext('2d', { willReadFrequently: true });
  }
  if (!ctx) throw new Error('2D canvas unavailable for height decoding');
  ctx.drawImage(bmp, 0, 0);
  bmp.close();
  const img = ctx.getImageData(0, 0, w, h);
  return { data: decodeHeightRGBA(img.data, w, h), px: w };
}

export interface LoadOptions {
  /** Base URL that serves manifest.json and tiles/ (e.g. "" or "http://localhost:8000"). */
  baseUrl?: string;
  /** Level to load completely. Default: min(manifest.height.max_level, 3). */
  level?: number;
  /** Max parallel fetches. */
  concurrency?: number;
}

export async function loadManifest(baseUrl = ''): Promise<Manifest> {
  const r = await fetch(`${baseUrl}/manifest.json`);
  if (!r.ok) throw new Error(`manifest.json: HTTP ${r.status}`);
  return (await r.json()) as Manifest;
}

/** Fetch and decode one height tile into the sampler. Missing tiles (404) are skipped. */
export async function loadHeightTile(sampler: TileHeightSampler, baseUrl: string, z: number, tx: number, ty: number): Promise<boolean> {
  const r = await fetch(`${baseUrl}/tiles/height/${z}/${tx}/${ty}.png`);
  if (!r.ok) return false;
  const { data, px } = await decodeHeightPng(await r.blob());
  sampler.addTile(z, tx, ty, data, px);
  return true;
}

/**
 * Load the manifest and every height tile of one level (plus level 0 as a
 * fallback). Fine for the 1 km fixture or a coarse level of the real site;
 * for level 5 of the real 8 km site load tiles lazily with loadHeightTile.
 */
export async function loadHeightTiles(opts: LoadOptions = {}): Promise<{ manifest: Manifest; sampler: TileHeightSampler; level: number }> {
  const baseUrl = opts.baseUrl ?? '';
  const manifest = await loadManifest(baseUrl);
  const level = Math.min(opts.level ?? Math.min(manifest.height.max_level, 3), manifest.height.max_level);
  const sampler = new TileHeightSampler(manifest.size_m);
  const jobs: Array<[number, number, number]> = [[0, 0, 0]];
  const n = 2 ** level;
  if (level > 0) for (let ty = 0; ty < n; ty++) for (let tx = 0; tx < n; tx++) jobs.push([level, tx, ty]);
  const conc = opts.concurrency ?? 8;
  let next = 0;
  await Promise.all(
    Array.from({ length: Math.min(conc, jobs.length) }, async () => {
      while (next < jobs.length) {
        const [z, tx, ty] = jobs[next++];
        await loadHeightTile(sampler, baseUrl, z, tx, ty);
      }
    }),
  );
  return { manifest, sampler, level };
}
