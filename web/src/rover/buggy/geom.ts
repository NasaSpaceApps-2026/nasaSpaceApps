/**
 * Geometry helpers: a per-material batch that merges static parts into one
 * mesh per material, tubes between points, bent tubes with filleted corners,
 * helix springs.
 */

import * as THREE from 'three';

export type V3 = [number, number, number];
export const v = (p: V3 | THREE.Vector3) => (p instanceof THREE.Vector3 ? p.clone() : new THREE.Vector3(p[0], p[1], p[2]));

/** Matrix from position, Euler (XYZ, radians) and scale. */
export function M(x = 0, y = 0, z = 0, rx = 0, ry = 0, rz = 0, sx = 1, sy = sx, sz = sx): THREE.Matrix4 {
  return new THREE.Matrix4().compose(
    new THREE.Vector3(x, y, z),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(rx, ry, rz)),
    new THREE.Vector3(sx, sy, sz),
  );
}

/** Strip to position/normal/uv, non-indexed, so everything can be merged. */
export function normalize(geo: THREE.BufferGeometry): THREE.BufferGeometry {
  let g = geo.index ? geo.toNonIndexed() : geo.clone();
  for (const k of Object.keys(g.attributes)) if (k !== 'position' && k !== 'normal' && k !== 'uv') g.deleteAttribute(k);
  if (!g.attributes.normal) g.computeVertexNormals();
  if (!g.attributes.uv) g.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(g.attributes.position.count * 2), 2));
  g.clearGroups();
  return g;
}

/** Merge normalized (non-indexed position/normal/uv) geometries. */
function mergeGeometries(list: THREE.BufferGeometry[]): THREE.BufferGeometry | null {
  if (!list.length) return null;
  const out = new THREE.BufferGeometry();
  for (const [name, size] of [['position', 3], ['normal', 3], ['uv', 2]] as const) {
    let n = 0;
    for (const g of list) n += g.attributes[name].count;
    const arr = new Float32Array(n * size);
    let o = 0;
    for (const g of list) {
      arr.set(g.attributes[name].array as Float32Array, o);
      o += g.attributes[name].count * size;
    }
    out.setAttribute(name, new THREE.BufferAttribute(arr, size));
  }
  return out;
}

export class Batch {
  private parts = new Map<THREE.Material, THREE.BufferGeometry[]>();

  add(geo: THREE.BufferGeometry, mat: THREE.Material, m?: THREE.Matrix4): this {
    const g = normalize(geo);
    if (m) g.applyMatrix4(m);
    let list = this.parts.get(mat);
    if (!list) this.parts.set(mat, (list = []));
    list.push(g);
    geo.dispose();
    return this;
  }

  /** Mirror-copy everything added through `fn` across X (left/right symmetric parts). */
  mirrorX(fn: (b: Batch) => void): this {
    const tmp = new Batch();
    fn(tmp);
    const flip = new THREE.Matrix4().makeScale(-1, 1, 1);
    for (const [mat, list] of tmp.parts) {
      for (const g of list) {
        this.push(mat, g.clone());
        const f = g.clone().applyMatrix4(flip);
        // mirroring flips winding: swap two vertices of every triangle
        const pos = f.attributes.position as THREE.BufferAttribute;
        const nor = f.attributes.normal as THREE.BufferAttribute;
        const uv = f.attributes.uv as THREE.BufferAttribute;
        for (let i = 0; i < pos.count; i += 3) {
          for (const a of [pos, nor, uv]) {
            const n = a.itemSize;
            for (let c = 0; c < n; c++) {
              const t = a.array[(i + 1) * n + c];
              (a.array as Float32Array)[(i + 1) * n + c] = a.array[(i + 2) * n + c];
              (a.array as Float32Array)[(i + 2) * n + c] = t;
            }
          }
        }
        this.push(mat, f);
      }
    }
    return this;
  }

  private push(mat: THREE.Material, g: THREE.BufferGeometry) {
    let list = this.parts.get(mat);
    if (!list) this.parts.set(mat, (list = []));
    list.push(g);
  }

  /** One mesh per material. */
  build(name: string, shadows = true): THREE.Group {
    const grp = new THREE.Group();
    grp.name = name;
    for (const [mat, list] of this.parts) {
      const merged = mergeGeometries(list);
      for (const g of list) g.dispose();
      if (!merged) continue;
      merged.computeBoundingSphere();
      const mesh = new THREE.Mesh(merged, mat);
      mesh.name = `${name}:${mat.name}`;
      mesh.castShadow = shadows && !(mat as THREE.MeshStandardMaterial).transparent;
      mesh.receiveShadow = shadows;
      grp.add(mesh);
    }
    this.parts.clear();
    return grp;
  }
}

const UP = new THREE.Vector3(0, 1, 0);

/** Matrix placing a Y-axis-aligned unit-height primitive between a and b. */
export function between(a: V3 | THREE.Vector3, b: V3 | THREE.Vector3): THREE.Matrix4 {
  const A = v(a);
  const B = v(b);
  const d = B.clone().sub(A);
  const len = d.length();
  const q = new THREE.Quaternion().setFromUnitVectors(UP, d.normalize());
  return new THREE.Matrix4().compose(A.add(B).multiplyScalar(0.5), q, new THREE.Vector3(1, len, 1));
}

/** Cylinder (tube) between two points. */
export function rod(a: V3 | THREE.Vector3, b: V3 | THREE.Vector3, r: number, seg = 10, r2 = r): THREE.BufferGeometry {
  const g = new THREE.CylinderGeometry(r2, r, 1, seg, 1, false);
  return g.applyMatrix4(between(a, b));
}

/**
 * Polyline with filleted corners (quadratic bends) as a CurvePath, for
 * roll-cage style tubing.
 */
export function bentPath(pts: Array<V3 | THREE.Vector3>, bend = 0.12): THREE.CurvePath<THREE.Vector3> {
  const P = pts.map(v);
  const path = new THREE.CurvePath<THREE.Vector3>();
  let cur = P[0].clone();
  for (let i = 1; i < P.length; i++) {
    const p = P[i];
    if (i === P.length - 1) {
      path.add(new THREE.LineCurve3(cur, p.clone()));
      break;
    }
    const n = P[i + 1];
    const dIn = p.clone().sub(P[i - 1]);
    const dOut = n.clone().sub(p);
    const rIn = Math.min(bend, dIn.length() * 0.45);
    const rOut = Math.min(bend, dOut.length() * 0.45);
    const a = p.clone().sub(dIn.normalize().multiplyScalar(rIn));
    const b = p.clone().add(dOut.normalize().multiplyScalar(rOut));
    if (a.distanceTo(cur) > 1e-4) path.add(new THREE.LineCurve3(cur, a));
    path.add(new THREE.QuadraticBezierCurve3(a, p.clone(), b));
    cur = b;
  }
  return path;
}

export function tube(path: THREE.Curve<THREE.Vector3>, r: number, radial = 10, segPerM = 30): THREE.BufferGeometry {
  const len = path.getLength();
  return new THREE.TubeGeometry(path, Math.max(4, Math.ceil(len * segPerM)), r, radial, false);
}

/** Bent tube with end caps: straight runs are single cylinders, bends are short tubes. */
export function cageTube(b: Batch, mat: THREE.Material, pts: Array<V3 | THREE.Vector3>, r: number, bend = 0.12, caps = true): void {
  const path = bentPath(pts, bend);
  for (const c of path.curves) {
    if (c instanceof THREE.LineCurve3) {
      const g = new THREE.CylinderGeometry(r, r, 1, 10, 1, true);
      b.add(g, mat, between(c.v1, c.v2));
    } else {
      b.add(new THREE.TubeGeometry(c, 6, r, 10, false), mat);
    }
  }
  if (caps) {
    for (const p of [pts[0], pts[pts.length - 1]]) {
      const P = v(p);
      b.add(new THREE.SphereGeometry(r * 1.05, 10, 6), mat, M(P.x, P.y, P.z));
    }
  }
}

/** Smooth tube through points (cables). */
export function cable(pts: Array<V3 | THREE.Vector3>, r: number): THREE.BufferGeometry {
  const c = new THREE.CatmullRomCurve3(pts.map(v), false, 'centripetal');
  return tube(c, r, 6, 24);
}

/** Helix along -Y from y=0 to y=-len (coil spring), radius R, wire r. */
export class HelixCurve extends THREE.Curve<THREE.Vector3> {
  constructor(public R: number, public len: number, public turns: number) {
    super();
  }
  getPoint(t: number, target = new THREE.Vector3()): THREE.Vector3 {
    const a = t * this.turns * Math.PI * 2;
    return target.set(Math.cos(a) * this.R, -t * this.len, Math.sin(a) * this.R);
  }
}

/** Rounded box via ExtrudeGeometry of a rounded rectangle (w x h, depth d along Z), centred. */
export function roundedBox(w: number, h: number, d: number, r: number, bevel = 0.006): THREE.BufferGeometry {
  const s = new THREE.Shape();
  const x = -w / 2 + bevel;
  const y = -h / 2 + bevel;
  const W = w - 2 * bevel;
  const H = h - 2 * bevel;
  const rr = Math.min(r, W / 2, H / 2);
  s.moveTo(x + rr, y);
  s.lineTo(x + W - rr, y);
  s.quadraticCurveTo(x + W, y, x + W, y + rr);
  s.lineTo(x + W, y + H - rr);
  s.quadraticCurveTo(x + W, y + H, x + W - rr, y + H);
  s.lineTo(x + rr, y + H);
  s.quadraticCurveTo(x, y + H, x, y + H - rr);
  s.lineTo(x, y + rr);
  s.quadraticCurveTo(x, y, x + rr, y);
  const g = new THREE.ExtrudeGeometry(s, {
    depth: d - 2 * bevel,
    bevelEnabled: true,
    bevelThickness: bevel,
    bevelSize: bevel,
    bevelSegments: 2,
    curveSegments: 4,
  });
  g.translate(0, 0, -(d - 2 * bevel) / 2);
  return g;
}

/** Box with planar UVs on every face in 0..1 (BoxGeometry already does this). */
export const box = (w: number, h: number, d: number) => new THREE.BoxGeometry(w, h, d);
