/**
 * Procedural textures for the buggy (CanvasTexture / DataTexture only; no
 * external assets, no network). All generators are deterministic (seeded).
 */

import * as THREE from 'three';

/** Small deterministic PRNG (mulberry32). */
export function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function canvas(w: number, h: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  return [c, c.getContext('2d')!];
}

function tex(c: HTMLCanvasElement, srgb: boolean, repeat = true): THREE.CanvasTexture {
  const t = new THREE.CanvasTexture(c);
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  if (repeat) t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.anisotropy = 4;
  return t;
}

/** Height field (0..1) -> tangent-space normal map. */
function heightToNormal(h: Float32Array, size: number, strength: number): THREE.DataTexture {
  const data = new Uint8Array(size * size * 4);
  const at = (x: number, y: number) => h[((y + size) % size) * size + ((x + size) % size)];
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dx = (at(x + 1, y) - at(x - 1, y)) * strength;
      const dy = (at(x, y + 1) - at(x, y - 1)) * strength;
      const len = Math.hypot(dx, dy, 1);
      const i = (y * size + x) * 4;
      data[i] = ((-dx / len) * 0.5 + 0.5) * 255;
      data[i + 1] = ((dy / len) * 0.5 + 0.5) * 255;
      data[i + 2] = ((1 / len) * 0.5 + 0.5) * 255;
      data[i + 3] = 255;
    }
  }
  const t = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.magFilter = THREE.LinearFilter;
  t.minFilter = THREE.LinearMipmapLinearFilter;
  t.generateMipmaps = true;
  t.needsUpdate = true;
  return t;
}

/**
 * Crinkled foil: Voronoi facets, each a random tilted plane, plus fine noise.
 * Returned as a normal map (tileable).
 */
export function crinkleNormalMap(size = 256, cells = 90, seed = 7): THREE.DataTexture {
  const r = rng(seed);
  const pts: Array<[number, number, number, number, number]> = [];
  for (let i = 0; i < cells; i++) pts.push([r() * size, r() * size, (r() - 0.5) * 1.4, (r() - 0.5) * 1.4, r()]);
  const h = new Float32Array(size * size);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let best = Infinity;
      let bi = 0;
      for (let k = 0; k < pts.length; k++) {
        let dx = Math.abs(x - pts[k][0]);
        let dy = Math.abs(y - pts[k][1]);
        if (dx > size / 2) dx = size - dx;
        if (dy > size / 2) dy = size - dy;
        const d = dx * dx + dy * dy;
        if (d < best) {
          best = d;
          bi = k;
        }
      }
      const p = pts[bi];
      let dx = x - p[0];
      let dy = y - p[1];
      if (dx > size / 2) dx -= size;
      if (dx < -size / 2) dx += size;
      if (dy > size / 2) dy -= size;
      if (dy < -size / 2) dy += size;
      h[y * size + x] = (p[2] * dx + p[3] * dy) / size * 6 + p[4] * 0.3 + r() * 0.02;
    }
  }
  return heightToNormal(h, size, 3.0);
}

/** Carbon fibre 2x2 twill: colour + roughness in one (sRGB colour map). */
export function carbonTexture(): THREE.CanvasTexture {
  const n = 256;
  const [c, g] = canvas(n, n);
  const cell = 16;
  for (let y = 0; y < n / cell; y++) {
    for (let x = 0; x < n / cell; x++) {
      const warp = ((x + y) >> 1) % 2 === 0;
      const grad = warp
        ? g.createLinearGradient(x * cell, 0, x * cell + cell, 0)
        : g.createLinearGradient(0, y * cell, 0, y * cell + cell);
      grad.addColorStop(0, '#0c0d0f');
      grad.addColorStop(0.5, warp ? '#3a3d44' : '#2a2c31');
      grad.addColorStop(1, '#0c0d0f');
      g.fillStyle = grad;
      g.fillRect(x * cell, y * cell, cell, cell);
    }
  }
  const t = tex(c, true);
  t.repeat.set(3, 3);
  return t;
}

/**
 * Painted panel: panel lines (inset grooves) and rivet dots near the edges.
 * Returns a bump map (linear) and a colour map (sRGB) sharing the layout.
 */
export function panelTextures(seed = 3): { map: THREE.CanvasTexture; bump: THREE.CanvasTexture } {
  const n = 512;
  const [c, g] = canvas(n, n);
  const [cb, b] = canvas(n, n);
  const r = rng(seed);
  // base colour with faint grime / dust toward the bottom
  const grad = g.createLinearGradient(0, 0, 0, n);
  grad.addColorStop(0, '#f1efea');
  grad.addColorStop(0.75, '#e6e1d8');
  grad.addColorStop(1, '#cdb8a2');
  g.fillStyle = grad;
  g.fillRect(0, 0, n, n);
  for (let i = 0; i < 900; i++) {
    g.fillStyle = `rgba(150,95,60,${r() * 0.05})`;
    const s = 2 + r() * 10;
    g.fillRect(r() * n, n * 0.4 + r() * n * 0.6, s, s * 0.5);
  }
  b.fillStyle = '#808080';
  b.fillRect(0, 0, n, n);
  const groove = (x0: number, y0: number, x1: number, y1: number) => {
    for (const [ctx, col, w] of [[b, '#2a2a2a', 5], [g, 'rgba(60,55,50,0.55)', 3]] as const) {
      ctx.strokeStyle = col;
      ctx.lineWidth = w;
      ctx.beginPath();
      ctx.moveTo(x0, y0);
      ctx.lineTo(x1, y1);
      ctx.stroke();
    }
  };
  const m = 22;
  groove(m, m, n - m, m);
  groove(n - m, m, n - m, n - m);
  groove(n - m, n - m, m, n - m);
  groove(m, n - m, m, m);
  groove(n * 0.5, m, n * 0.5, n * 0.42);
  groove(m, n * 0.42, n - m, n * 0.42);
  // access hatch
  b.strokeStyle = '#3a3a3a';
  b.lineWidth = 4;
  b.strokeRect(n * 0.58, n * 0.55, n * 0.3, n * 0.3);
  g.strokeStyle = 'rgba(60,55,50,0.45)';
  g.lineWidth = 2;
  g.strokeRect(n * 0.58, n * 0.55, n * 0.3, n * 0.3);
  // rivets
  const rivet = (x: number, y: number) => {
    const rg = b.createRadialGradient(x, y, 0, x, y, 6);
    rg.addColorStop(0, '#ffffff');
    rg.addColorStop(1, '#808080');
    b.fillStyle = rg;
    b.beginPath();
    b.arc(x, y, 6, 0, Math.PI * 2);
    b.fill();
    g.fillStyle = 'rgba(120,120,120,0.6)';
    g.beginPath();
    g.arc(x, y, 3.5, 0, Math.PI * 2);
    g.fill();
  };
  for (let i = 0; i <= 10; i++) {
    const t = m + 14 + (i / 10) * (n - 2 * m - 28);
    rivet(t, m + 14);
    rivet(t, n - m - 14);
    rivet(m + 14, t);
    rivet(n - m - 14, t);
  }
  // small warning stripe block (abstract, no text)
  for (let i = 0; i < 6; i++) {
    g.fillStyle = i % 2 ? '#1b1b1b' : '#f2a516';
    g.beginPath();
    const x = n * 0.1 + i * 14;
    g.moveTo(x, n * 0.8);
    g.lineTo(x + 14, n * 0.8);
    g.lineTo(x + 28, n * 0.88);
    g.lineTo(x + 14, n * 0.88);
    g.fill();
  }
  return { map: tex(c, true, false), bump: tex(cb, false, false) };
}

/** Rubber: colour (dusty) and bump for the tyre sidewall and tread surface. */
export function tireTextures(seed = 11): { map: THREE.CanvasTexture; bump: THREE.CanvasTexture; rough: THREE.CanvasTexture } {
  // u wraps around the tyre, v runs across the profile (bead -> tread -> bead).
  const w = 1024;
  const h = 128;
  const [c, g] = canvas(w, h);
  const [cb, b] = canvas(w, h);
  const [cr, rr] = canvas(w, h);
  const r = rng(seed);
  g.fillStyle = '#26221f';
  g.fillRect(0, 0, w, h);
  b.fillStyle = '#808080';
  b.fillRect(0, 0, w, h);
  rr.fillStyle = '#e0e0e0';
  rr.fillRect(0, 0, w, h);
  // sidewall bands near both beads: concentric ribs and radial serrations
  for (const side of [0, 1]) {
    const v0 = side ? h * 0.72 : 0;
    const v1 = side ? h : h * 0.28;
    for (let k = 0; k < 4; k++) {
      const y = v0 + (v1 - v0) * (0.2 + k * 0.18);
      b.fillStyle = k % 2 ? '#a0a0a0' : '#606060';
      b.fillRect(0, y, w, 2);
    }
    // radial serrations at the shoulder
    const ys = side ? h * 0.72 : h * 0.22;
    for (let i = 0; i < 160; i++) {
      const x = (i / 160) * w;
      b.fillStyle = '#b4b4b4';
      b.fillRect(x, ys, 2.5, h * 0.06);
    }
    // a raised "rim protector" bar pattern (lettering-free blocks)
    for (let i = 0; i < 24; i++) {
      const x = (i / 24) * w + 8;
      b.fillStyle = '#9a9a9a';
      b.fillRect(x, v0 + (v1 - v0) * 0.45, w / 24 - 24, (v1 - v0) * 0.12);
      g.fillStyle = 'rgba(80,72,66,0.5)';
      g.fillRect(x, v0 + (v1 - v0) * 0.45, w / 24 - 24, (v1 - v0) * 0.12);
    }
  }
  // Mars dust caked in the tread centre and speckles everywhere
  const dust = g.createLinearGradient(0, 0, 0, h);
  dust.addColorStop(0, 'rgba(150,90,55,0.05)');
  dust.addColorStop(0.5, 'rgba(160,95,58,0.38)');
  dust.addColorStop(1, 'rgba(150,90,55,0.05)');
  g.fillStyle = dust;
  g.fillRect(0, 0, w, h);
  for (let i = 0; i < 5000; i++) {
    const x = r() * w;
    const y = r() * h;
    const a = r() * 0.25;
    g.fillStyle = `rgba(${150 + r() * 40},${85 + r() * 30},${50 + r() * 20},${a})`;
    g.fillRect(x, y, 1 + r() * 2, 1 + r() * 2);
    rr.fillStyle = `rgba(255,255,255,${a})`;
    rr.fillRect(x, y, 2, 2);
  }
  const map = tex(c, true);
  const bump = tex(cb, false);
  const rough = tex(cr, false);
  return { map, bump, rough };
}

/** Suit fabric: fine rip-stop weave with faint dust, as colour + bump. */
export function fabricTextures(seed = 5): { map: THREE.CanvasTexture; bump: THREE.CanvasTexture } {
  const n = 256;
  const [c, g] = canvas(n, n);
  const [cb, b] = canvas(n, n);
  const r = rng(seed);
  g.fillStyle = '#f3f1ec';
  g.fillRect(0, 0, n, n);
  b.fillStyle = '#808080';
  b.fillRect(0, 0, n, n);
  for (let i = 0; i < n; i += 4) {
    b.fillStyle = i % 32 === 0 ? '#5a5a5a' : '#747474';
    b.fillRect(0, i, n, 1);
    b.fillRect(i, 0, 1, n);
  }
  for (let i = 0; i < n; i += 32) {
    g.fillStyle = 'rgba(0,0,0,0.04)';
    g.fillRect(0, i, n, 1);
    g.fillRect(i, 0, 1, n);
  }
  for (let i = 0; i < 1400; i++) {
    g.fillStyle = `rgba(170,110,70,${r() * 0.06})`;
    const s = 1 + r() * 4;
    g.fillRect(r() * n, r() * n, s, s);
  }
  const map = tex(c, true);
  const bump = tex(cb, false);
  map.repeat.set(4, 4);
  bump.repeat.set(4, 4);
  return { map, bump };
}

/** Solar cell array: dark blue cells, silver busbars. */
export function solarTexture(): THREE.CanvasTexture {
  const w = 512;
  const h = 256;
  const [c, g] = canvas(w, h);
  g.fillStyle = '#c9ccd2';
  g.fillRect(0, 0, w, h);
  const cols = 12;
  const rows = 6;
  const cw = w / cols;
  const ch = h / rows;
  for (let y = 0; y < rows; y++) {
    for (let x = 0; x < cols; x++) {
      const gr = g.createLinearGradient(x * cw, y * ch, x * cw + cw, y * ch + ch);
      gr.addColorStop(0, '#16224a');
      gr.addColorStop(1, '#0c1430');
      g.fillStyle = gr;
      g.fillRect(x * cw + 2, y * ch + 2, cw - 4, ch - 4);
      g.fillStyle = 'rgba(200,205,215,0.55)';
      for (let k = 1; k < 4; k++) g.fillRect(x * cw + 2, y * ch + (k * ch) / 4, cw - 4, 1);
    }
  }
  return tex(c, true, false);
}

/** Abstract mission patch ("ARES"): Mars disc, orbit swoosh, chevron. No real logos. */
export function patchTexture(): THREE.CanvasTexture {
  const n = 256;
  const [c, g] = canvas(n, n);
  const cx = n / 2;
  g.fillStyle = '#10151f';
  g.beginPath();
  g.arc(cx, cx, cx - 2, 0, Math.PI * 2);
  g.fill();
  g.strokeStyle = '#e8b34a';
  g.lineWidth = 10;
  g.beginPath();
  g.arc(cx, cx, cx - 8, 0, Math.PI * 2);
  g.stroke();
  const mg = g.createRadialGradient(cx - 20, cx - 20, 5, cx, cx, 70);
  mg.addColorStop(0, '#f08a4b');
  mg.addColorStop(1, '#8e2f16');
  g.fillStyle = mg;
  g.beginPath();
  g.arc(cx, cx + 6, 62, 0, Math.PI * 2);
  g.fill();
  g.strokeStyle = '#ffffff';
  g.lineWidth = 6;
  g.beginPath();
  g.ellipse(cx, cx + 6, 100, 30, -0.35, 0, Math.PI * 2);
  g.stroke();
  g.fillStyle = '#ffffff';
  g.beginPath();
  g.moveTo(cx, 40);
  g.lineTo(cx + 24, 92);
  g.lineTo(cx, 80);
  g.lineTo(cx - 24, 92);
  g.closePath();
  g.fill();
  g.fillStyle = '#ffffff';
  g.font = 'bold 34px Helvetica, Arial, sans-serif';
  g.textAlign = 'center';
  g.fillText('ARES', cx, n - 30);
  return tex(c, true, false);
}

/** Side livery decal: stripe + "ARES ATLAS" + number. Transparent background. */
export function liveryTexture(): THREE.CanvasTexture {
  const w = 1024;
  const h = 256;
  const [c, g] = canvas(w, h);
  g.clearRect(0, 0, w, h);
  g.fillStyle = '#ff5a1f';
  g.beginPath();
  g.moveTo(0, h * 0.62);
  g.lineTo(w, h * 0.4);
  g.lineTo(w, h * 0.55);
  g.lineTo(0, h * 0.78);
  g.fill();
  g.fillStyle = '#1b1d22';
  g.beginPath();
  g.moveTo(0, h * 0.82);
  g.lineTo(w, h * 0.6);
  g.lineTo(w, h * 0.64);
  g.lineTo(0, h * 0.86);
  g.fill();
  g.fillStyle = '#1b1d22';
  g.font = 'italic bold 92px Helvetica, Arial, sans-serif';
  g.fillText('ARES ATLAS', 40, 120);
  g.fillStyle = '#1b1d22';
  g.font = 'italic bold 110px Helvetica, Arial, sans-serif';
  g.fillText('01', w - 170, 95);
  const t = tex(c, true, false);
  return t;
}

/** Glossy display / instrument screen. */
export function screenTexture(): THREE.CanvasTexture {
  const w = 256;
  const h = 128;
  const [c, g] = canvas(w, h);
  g.fillStyle = '#04121a';
  g.fillRect(0, 0, w, h);
  g.strokeStyle = '#1de0c0';
  g.lineWidth = 2;
  g.beginPath();
  for (let x = 0; x <= 120; x += 4) g.lineTo(10 + x, 90 - Math.sin(x * 0.08) * 20 - x * 0.2);
  g.stroke();
  g.strokeStyle = '#ff8a3a';
  g.beginPath();
  g.arc(190, 62, 36, Math.PI * 0.8, Math.PI * 2.2);
  g.stroke();
  g.fillStyle = '#1de0c0';
  for (let i = 0; i < 6; i++) g.fillRect(12 + i * 18, 108, 12, 8 - (i % 3) * 2);
  g.fillStyle = '#ff8a3a';
  g.fillRect(186, 30, 8, 28);
  return tex(c, true, false);
}

/** Soft round sprite (alpha falloff), for dust and glow. */
export function softSprite(inner = 'rgba(255,255,255,1)', outer = 'rgba(255,255,255,0)', size = 64, noisy = false): THREE.CanvasTexture {
  const [c, g] = canvas(size, size);
  const rg = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  rg.addColorStop(0, inner);
  rg.addColorStop(noisy ? 0.35 : 0.2, inner.replace(/[\d.]+\)$/, '0.6)'));
  rg.addColorStop(1, outer);
  g.fillStyle = rg;
  g.fillRect(0, 0, size, size);
  if (noisy) {
    const r = rng(99);
    g.globalCompositeOperation = 'destination-out';
    for (let i = 0; i < 60; i++) {
      g.fillStyle = `rgba(0,0,0,${r() * 0.35})`;
      g.beginPath();
      g.arc(r() * size, r() * size, 2 + r() * 6, 0, Math.PI * 2);
      g.fill();
    }
  }
  return tex(c, true, false);
}

/**
 * Equirectangular Mars-sky environment for reflections (butterscotch sky,
 * bright sun, rust ground). Used as material envMap so metals and the visor
 * read correctly without the host scene having an environment.
 */
export function marsEnvTexture(): THREE.CanvasTexture {
  const w = 512;
  const h = 256;
  const [c, g] = canvas(w, h);
  const sky = g.createLinearGradient(0, 0, 0, h);
  sky.addColorStop(0, '#6f5a4a');
  sky.addColorStop(0.35, '#c4a07c');
  sky.addColorStop(0.49, '#e7c9a5');
  sky.addColorStop(0.5, '#8a5a3c');
  sky.addColorStop(0.62, '#5e3a26');
  sky.addColorStop(1, '#2c1c14');
  g.fillStyle = sky;
  g.fillRect(0, 0, w, h);
  // distant ridges on the horizon
  const r = rng(21);
  g.fillStyle = '#7d5038';
  g.beginPath();
  g.moveTo(0, h * 0.5);
  for (let x = 0; x <= w; x += 8) g.lineTo(x, h * 0.5 - 4 - Math.abs(Math.sin(x * 0.02)) * 10 - r() * 3);
  g.lineTo(w, h * 0.5);
  g.fill();
  // sun (bluish-white halo, as at Martian sunset-ish, but kept warm-neutral)
  const sx = w * 0.62;
  const sy = h * 0.2;
  const sg = g.createRadialGradient(sx, sy, 0, sx, sy, 60);
  sg.addColorStop(0, 'rgba(255,255,250,1)');
  sg.addColorStop(0.08, 'rgba(255,250,235,0.95)');
  sg.addColorStop(0.4, 'rgba(240,220,190,0.35)');
  sg.addColorStop(1, 'rgba(240,220,190,0)');
  g.fillStyle = sg;
  g.fillRect(0, 0, w, h);
  const t = new THREE.CanvasTexture(c);
  t.mapping = THREE.EquirectangularReflectionMapping;
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
