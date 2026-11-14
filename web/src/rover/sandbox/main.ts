/**
 * Rover sandbox: fixture terrain from the contract height + ortho tiles, the
 * rover, chase camera, HUD, and the API link (optional; works offline).
 *
 * Query params: ?base=<tile/manifest base URL, default same origin>
 *               &api=<API base, default http://localhost:8000>
 *               &level=<height level to load>  &x=&y=&heading=  (start pose)
 */

import * as THREE from 'three';
import {
  ChaseCamera,
  DEFAULT_ROVER_PROFILE,
  KeyboardControls,
  RoverDriver,
  RoverLink,
  applyPose,
  createRoverModel,
  loadHeightTiles,
  profileFromSite,
  tileBounds,
  type Manifest,
  type RoverProfile,
  type TileHeightSampler,
} from '../index';

const q = new URLSearchParams(location.search);
const BASE = q.get('base') ?? '';
const API = (q.get('api') ?? 'http://localhost:8000').replace(/\/$/, '');
const WS = API.replace(/^http/, 'ws') + '/ws';
const START = { x: Number(q.get('x') ?? 100), y: Number(q.get('y') ?? -60), heading_deg: Number(q.get('heading') ?? 90) };

const $ = (id: string) => document.getElementById(id)!;

async function fetchProfile(): Promise<RoverProfile> {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 1500);
  try {
    const r = await fetch(`${API}/site`, { signal: ctl.signal });
    if (!r.ok) throw new Error(String(r.status));
    return profileFromSite(await r.json()) ?? { ...DEFAULT_ROVER_PROFILE, source: 'assumed (built-in defaults; /site had no rover profile)' };
  } catch {
    return DEFAULT_ROVER_PROFILE;
  } finally {
    clearTimeout(t);
  }
}

function buildTerrain(manifest: Manifest, sampler: TileHeightSampler, level: number, stride = 2): THREE.Group {
  const group = new THREE.Group();
  group.name = 'terrain';
  const ortho = manifest.layers.find((l) => l.id === 'ortho');
  const useOrtho = !!ortho && ortho.max_level >= level;
  const loader = new THREE.TextureLoader();
  const n = 2 ** level;
  for (let ty = 0; ty < n; ty++) {
    for (let tx = 0; tx < n; tx++) {
      const data = sampler.getTile(level, tx, ty);
      if (!data) continue;
      const px = Math.round(Math.sqrt(data.length));
      const b = tileBounds(manifest.size_m, level, tx, ty);
      const m = Math.floor((px - 1) / stride);
      const verts = (m + 1) * (m + 1);
      const pos = new Float32Array(verts * 3);
      const uv = new Float32Array(verts * 2);
      for (let j = 0; j <= m; j++) {
        for (let i = 0; i <= m; i++) {
          const k = j * (m + 1) + i;
          const pi = Math.min(px - 1, i * stride);
          const pj = Math.min(px - 1, j * stride);
          const x = b.west + (pi / (px - 1)) * b.size;
          const y = b.north - (pj / (px - 1)) * b.size;
          pos[k * 3] = x; // three X = x
          pos[k * 3 + 1] = data[pj * px + pi]; // three Y = z
          pos[k * 3 + 2] = -y; // three Z = -y
          uv[k * 2] = pi / (px - 1);
          uv[k * 2 + 1] = 1 - pj / (px - 1);
        }
      }
      const idx = new Uint32Array(m * m * 6);
      let o = 0;
      for (let j = 0; j < m; j++) {
        for (let i = 0; i < m; i++) {
          const a = j * (m + 1) + i;
          const c = a + m + 1;
          idx.set([a, c, a + 1, a + 1, c, c + 1], o);
          o += 6;
        }
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
      geo.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
      geo.setIndex(new THREE.BufferAttribute(idx, 1));
      geo.computeVertexNormals();
      geo.computeBoundingSphere();
      let mat: THREE.Material;
      if (useOrtho) {
        const tex = loader.load(`${BASE}/tiles/ortho/${level}/${tx}/${ty}.webp`);
        tex.colorSpace = THREE.SRGBColorSpace;
        tex.anisotropy = 4;
        tex.wrapS = tex.wrapT = THREE.ClampToEdgeWrapping;
        // Imagery already carries shading; draw it unlit so we do not shade it twice.
        mat = new THREE.MeshBasicMaterial({ map: tex });
      } else {
        mat = new THREE.MeshLambertMaterial({ color: 0x9b8a78 });
      }
      const mesh = new THREE.Mesh(geo, mat);
      mesh.receiveShadow = true;
      group.add(mesh);
    }
  }
  return group;
}

const f = (v: number, d = 1) => (Number.isFinite(v) ? v.toFixed(d) : '-');

async function main() {
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setSize(innerWidth, innerHeight);
  renderer.shadowMap.enabled = true;
  $('view').appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x2a2522);
  const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.3, 6000);
  scene.add(new THREE.HemisphereLight(0xe8e2d8, 0x3a2c22, 1.2));
  const sun = new THREE.DirectionalLight(0xffffff, 2.0);
  sun.castShadow = true;
  sun.shadow.mapSize.set(1024, 1024);
  Object.assign(sun.shadow.camera, { left: -8, right: 8, top: 8, bottom: -8, near: 1, far: 80 });
  scene.add(sun, sun.target);

  let loaded;
  try {
    loaded = await loadHeightTiles({ baseUrl: BASE, level: q.has('level') ? Number(q.get('level')) : undefined });
  } catch (e) {
    $('msg').textContent = `Could not load terrain from "${BASE || location.origin}": ${(e as Error).message}`;
    return;
  }
  const { manifest, sampler, level } = loaded;
  scene.add(buildTerrain(manifest, sampler, level));
  scene.fog = new THREE.Fog(0x2a2522, manifest.size_m * 0.6, manifest.size_m * 1.6);

  const ortho = manifest.layers.find((l) => l.id === 'ortho');
  $('data').innerHTML =
    `<b>Terrain</b>: ${manifest.site}${manifest.fixture ? ' (fixture)' : ''}, ${manifest.size_m} m square, height level ${level} ` +
    `(${f(manifest.size_m / 2 ** level / 256, 2)} m posts), no vertical exaggeration.<br>` +
    `<b>Imagery</b>: ${ortho ? `${ortho.title} - ${ortho.credit ?? ''}` : 'none'}`;

  const profile = await fetchProfile();
  const controls = new KeyboardControls();
  const link = new RoverLink({ url: WS });
  const driver = new RoverDriver({ sampler, profile, controls, link, start: START });

  const rover = createRoverModel();
  scene.add(rover);
  const chase = new ChaseCamera(camera, { sampler });

  addEventListener('resize', () => {
    camera.aspect = innerWidth / innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(innerWidth, innerHeight);
  });
  renderer.domElement.addEventListener('wheel', (e) => {
    e.preventDefault();
    const k = Math.exp(e.deltaY * 0.001);
    chase.distance = Math.min(400, Math.max(5, chase.distance * k));
    chase.height = chase.distance * 0.43;
  }, { passive: false });
  addEventListener('keydown', (e) => {
    if (e.code === 'KeyR' && !e.repeat) {
      driver.teleport(START.x, START.y, START.heading_deg);
      chase.snap();
    }
  });

  $('msg').remove();

  const hud = $('hud');
  let hudT = 0;
  let last = performance.now();
  const loop = () => {
    const t = performance.now();
    const dt = (t - last) / 1000;
    last = t;
    const p = driver.step(dt);
    applyPose(rover, p);
    chase.update(dt, p);
    sun.position.set(rover.position.x + 20, rover.position.y + 35, rover.position.z + 10);
    sun.target.position.copy(rover.position);
    renderer.render(scene, camera);

    hudT += dt;
    if (hudT > 0.1) {
      hudT = 0;
      const prof = driver.physics.profile;
      const st = driver.physics.state;
      const slopeCls = p.slope_deg > prof.nogo_slope_deg ? 'bad' : p.slope_deg > prof.caution_slope_deg ? 'warn' : '';
      const aheadCls = p.ahead_slope_deg > prof.nogo_slope_deg ? 'bad' : p.ahead_slope_deg > prof.caution_slope_deg ? 'warn' : '';
      const statusCls = String(p.status).startsWith('blocked') ? 'bad' : p.status === 'caution' ? 'warn' : '';
      hud.innerHTML = `<table>
        <tr><td>x, y</td><td>${f(p.x)} E, ${f(p.y)} N m</td></tr>
        <tr><td>elevation</td><td>${f(p.z, 2)} m</td></tr>
        <tr><td>heading</td><td>${f(p.heading_deg)}&deg;</td></tr>
        <tr><td>pitch / roll</td><td>${f(p.pitch_deg)}&deg; / ${f(p.roll_deg)}&deg;</td></tr>
        <tr><td>slope under</td><td class="${slopeCls}">${f(p.slope_deg)}&deg;</td></tr>
        <tr><td>slope ahead</td><td class="${aheadCls}">${f(p.ahead_slope_deg)}&deg;</td></tr>
        <tr><td>speed</td><td>${f(Math.abs(p.speed_mps) * 100, 1)} cm/s mission (${f(Math.abs(p.display_speed_mps), 1)} m/s on screen, x${prof.display_speed_factor})</td></tr>
        <tr><td>mode</td><td>${p.mode}</td></tr>
        <tr><td>status</td><td class="${statusCls}">${p.status}</td></tr>
        <tr><td>stop reason</td><td>${p.stop_reason ?? '-'}</td></tr>
        <tr><td>odometer</td><td>${f(st.odometer_m)} m, mission time ${f(st.mission_time_s / 60)} min</td></tr>
        <tr><td>API link</td><td>${driver.linkStatus} (${WS})</td></tr>
        <tr class="sep"><td>caution slope</td><td>${prof.caution_slope_deg}&deg;</td></tr>
        <tr><td>no-go slope</td><td>${prof.nogo_slope_deg}&deg;</td></tr>
        <tr><td>flat speed</td><td>${prof.flat_speed_mps} m/s, curve k ${prof.speed_curve_k}</td></tr>
        <tr><td colspan="2" class="src">Limits source: ${prof.source}. Speed curve speed = flat &middot; exp(-k&middot;|tan slope|) is an Earth hiking-function shape (placeholder).</td></tr>
      </table>`;
    }
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}

main();
