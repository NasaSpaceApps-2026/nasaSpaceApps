/**
 * Lightweight world-space dust: a ring buffer of THREE.Points with per-
 * particle size and alpha (small ShaderMaterial), soft canvas sprite, reddish
 * Mars dust, low gravity, drag, fade out. One draw call.
 *
 * The Points object is parented to the buggy group but its matrix is set to
 * the inverse of the group's world matrix each update, so particles live in
 * world coordinates and trail behind the vehicle.
 */

import * as THREE from 'three';
import { rng, softSprite } from './textures';

const MAX = 1200;
const G = 3.71; // Mars gravity, m/s^2

const vert = /* glsl */ `
  attribute float aSize;
  attribute float aAlpha;
  varying float vAlpha;
  uniform float uScale;
  void main() {
    vAlpha = aAlpha;
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = aSize * uScale / max(0.1, -mv.z);
    gl_Position = projectionMatrix * mv;
  }
`;
const frag = /* glsl */ `
  uniform sampler2D uMap;
  uniform vec3 uColor;
  varying float vAlpha;
  void main() {
    vec4 t = texture2D(uMap, gl_PointCoord);
    float a = t.a * vAlpha;
    if (a < 0.003) discard;
    gl_FragColor = vec4(uColor * (0.8 + 0.2 * gl_PointCoord.y), a);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }
`;

export class DustSystem {
  readonly points: THREE.Points;
  private pos = new Float32Array(MAX * 3);
  private vel = new Float32Array(MAX * 3);
  private age = new Float32Array(MAX);
  private life = new Float32Array(MAX);
  private size0 = new Float32Array(MAX);
  private size = new Float32Array(MAX);
  private alpha = new Float32Array(MAX);
  private a0 = new Float32Array(MAX);
  private head = 0;
  private r = rng(1234);
  private acc = 0;
  private mat: THREE.ShaderMaterial;
  private tex: THREE.Texture;

  constructor() {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(this.pos, 3).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute('aSize', new THREE.BufferAttribute(this.size, 1).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute('aAlpha', new THREE.BufferAttribute(this.alpha, 1).setUsage(THREE.DynamicDrawUsage));
    this.tex = softSprite('rgba(255,255,255,1)', 'rgba(255,255,255,0)', 64, true);
    this.mat = new THREE.ShaderMaterial({
      uniforms: {
        uMap: { value: this.tex },
        uColor: { value: new THREE.Color(0xe2b48c) },
        uScale: { value: 600 },
      },
      vertexShader: vert,
      fragmentShader: frag,
      transparent: true,
      depthWrite: false,
    });
    this.points = new THREE.Points(geo, this.mat);
    this.points.name = 'buggy:dust';
    this.points.frustumCulled = false;
    this.points.matrixAutoUpdate = false;
    this.points.renderOrder = 2;
  }

  /** Pixel scale for point size: viewport height / (2 tan(fov/2)). */
  setViewport(heightPx: number, fovDeg: number): void {
    this.mat.uniforms.uScale.value = heightPx / (2 * Math.tan((fovDeg * Math.PI) / 360));
  }

  /** Emit n particles at world position p with world velocity v (+ jitter). */
  emit(n: number, p: THREE.Vector3, v: THREE.Vector3, spread: number, size: number, life: number, alpha: number): void {
    const r = this.r;
    for (let k = 0; k < n; k++) {
      const i = this.head;
      this.head = (this.head + 1) % MAX;
      this.pos[i * 3] = p.x + (r() - 0.5) * 0.25;
      this.pos[i * 3 + 1] = p.y + r() * 0.05;
      this.pos[i * 3 + 2] = p.z + (r() - 0.5) * 0.25;
      this.vel[i * 3] = v.x + (r() - 0.5) * spread;
      this.vel[i * 3 + 1] = v.y + r() * spread * 0.8;
      this.vel[i * 3 + 2] = v.z + (r() - 0.5) * spread;
      this.age[i] = 0;
      this.life[i] = life * (0.6 + r() * 0.8);
      this.size0[i] = size * (0.6 + r() * 0.8);
      this.a0[i] = alpha * (0.6 + r() * 0.4);
    }
  }

  /** Accumulating emitter: rate in particles per second. Returns count to emit now. */
  take(rate: number, dt: number): number {
    this.acc += rate * dt;
    const n = Math.floor(this.acc);
    this.acc -= n;
    return n;
  }

  update(dt: number, parentWorld: THREE.Matrix4): void {
    const drag = Math.exp(-1.6 * dt);
    for (let i = 0; i < MAX; i++) {
      if (this.life[i] <= 0) {
        this.alpha[i] = 0;
        continue;
      }
      this.age[i] += dt;
      const t = this.age[i] / this.life[i];
      if (t >= 1) {
        this.life[i] = 0;
        this.alpha[i] = 0;
        continue;
      }
      const j = i * 3;
      this.vel[j] *= drag;
      this.vel[j + 2] *= drag;
      this.vel[j + 1] = this.vel[j + 1] * drag - G * 0.15 * dt; // fine dust settles slowly
      this.pos[j] += this.vel[j] * dt;
      this.pos[j + 1] += this.vel[j + 1] * dt;
      this.pos[j + 2] += this.vel[j + 2] * dt;
      this.size[i] = this.size0[i] * (0.5 + 2.2 * Math.sqrt(t));
      const fadeIn = Math.min(1, t * 8);
      this.alpha[i] = this.a0[i] * fadeIn * (1 - t) * (1 - t);
    }
    const g = this.points.geometry;
    g.attributes.position.needsUpdate = true;
    g.attributes.aSize.needsUpdate = true;
    g.attributes.aAlpha.needsUpdate = true;
    this.points.matrix.copy(parentWorld).invert();
    this.points.matrixWorldNeedsUpdate = true;
  }

  dispose(): void {
    this.points.geometry.dispose();
    this.mat.dispose();
    this.tex.dispose();
  }
}
