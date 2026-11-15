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

/**
 * Pure-JS PNG decode (8-bit RGB/RGBA, non-interlaced) via DecompressionStream. Returns null when the file is something
 * else or the API is missing, and the caller falls back to the canvas path.
 * Why: canvas readback is not bit-exact in every browser (Brave "farbling" randomises low bits). A one-bit change in the
 * RED byte of a height pixel is 655.36 m, which showed up in street view as random spikes and pits with striped walls.
 */
async function decodePngExact(blob: Blob): Promise<{ data: Float32Array; px: number } | null> {
  if (typeof DecompressionStream === 'undefined') return null;
  const b = new Uint8Array(await blob.arrayBuffer());
  const sig = [137, 80, 78, 71, 13, 10, 26, 10];
  if (b.length < 33 || sig.some((v, i) => b[i] !== v)) return null;
  const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
  let w = 0, h = 0, depth = 0, ctype = 0, interlace = 1;
  const parts: Uint8Array[] = [];
  for (let o = 8; o + 8 <= b.length;) {
    const len = dv.getUint32(o);
    const type = String.fromCharCode(b[o + 4], b[o + 5], b[o + 6], b[o + 7]);
    const body = b.subarray(o + 8, o + 8 + len);
    if (type === 'IHDR') {
      w = dv.getUint32(o + 8); h = dv.getUint32(o + 12); depth = b[o + 16]; ctype = b[o + 17]; interlace = b[o + 20];
    } else if (type === 'IDAT') parts.push(body);
    else if (type === 'IEND') break;
    o += 12 + len;
  }
  const bpp = ctype === 2 ? 3 : ctype === 6 ? 4 : 0;
  if (!bpp || depth !== 8 || interlace !== 0 || !w || !h || !parts.length) return null;
  const zbytes = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let zo = 0;
  for (const p of parts) { zbytes.set(p, zo); zo += p.length; }
  const raw = new Uint8Array(await new Response(new Blob([zbytes]).stream().pipeThrough(new DecompressionStream('deflate'))).arrayBuffer());
  const rowLen = w * bpp;
  if (raw.length < (rowLen + 1) * h) return null;
  const px = new Uint8Array(rowLen * h);
  for (let y = 0; y < h; y++) {
    const f = raw[y * (rowLen + 1)];
    const src = y * (rowLen + 1) + 1, dst = y * rowLen, up = dst - rowLen;
    for (let i = 0; i < rowLen; i++) {
      const x = raw[src + i];
      const a = i >= bpp ? px[dst + i - bpp] : 0;
      const c = y > 0 ? px[up + i] : 0;
      const d = y > 0 && i >= bpp ? px[up + i - bpp] : 0;
      let v: number;
      if (f === 0) v = x;
      else if (f === 1) v = x + a;
      else if (f === 2) v = x + c;
      else if (f === 3) v = x + ((a + c) >> 1);
      else if (f === 4) {
        const pp = a + c - d, pa = Math.abs(pp - a), pb = Math.abs(pp - c), pc = Math.abs(pp - d);
        v = x + (pa <= pb && pa <= pc ? a : pb <= pc ? c : d);
      } else return null;
      px[dst + i] = v & 255;
    }
  }
  return { data: decodeHeightRGBA(px, w, h, bpp), px: w };
}

/** Decode a PNG Blob into elevations in the browser (bit-exact when possible, else canvas without colour management). */
export async function decodeHeightPng(blob: Blob): Promise<{ data: Float32Array; px: number }> {
  try {
    const exact = await decodePngExact(blob);
    if (exact) return exact;
  } catch { /* fall through to the canvas path */ }
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
