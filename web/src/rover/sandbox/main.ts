/**
 * Rover sandbox: fixture terrain from the contract height + ortho tiles, the
 * rover, chase camera, HUD, and the API link (optional; works offline).
 *
 * Query params: ?base=<tile/manifest base URL, default same origin>
 *               &api=<API base, default http://localhost:8000>
 *               &level=<height level to load>  &x=&y=&heading=  (start pose)
 *               &world=projection | vision   (omit world for the server default site)
 */

import * as THREE from 'three';
import {
  ChaseCamera,
  BUGGY_PARAMS,
  DEFAULT_BUGGY_PROFILE,
  KeyboardControls,
  RoverDriver,
  RoverLink,
  BUGGY_DIMENSIONS,
  applyPose,
  createBuggyModel,
  headingToDir,
  loadHeightTiles,
  profileFromSite,
  tileBounds,
  type Manifest,
  type RoverProfile,
  type TileHeightSampler,
} from '../index';

const q = new URLSearchParams(location.search);
// No `world` keeps the site the dev server was started with. projection is the
// Navcam drape on frozen heights; vision (alias llm) is the edited model.
const WORLD_BASE: Record<string, string> = {
  projection: '/worlds/projection',
  image: '/worlds/projection',
  vision: '/worlds/vision',
  llm: '/worlds/vision',
  crater: '/worlds/crater',
};
const rawWorld = (q.get('world') ?? '').trim().toLowerCase();
const worldBase = rawWorld ? WORLD_BASE[rawWorld] : undefined;
const worldName: 'projection' | 'vision' | 'crater' | null =
  worldBase === '/worlds/vision' ? 'vision' : worldBase === '/worlds/projection' ? 'projection' : worldBase === '/worlds/crater' ? 'crater' : null;
const worldError = rawWorld && !worldBase ? `Unknown world "${q.get('world')}". Use projection, vision, or crater.` : '';
const BASE = worldBase ?? q.get('base') ?? '';
const API = (q.get('api') ?? 'http://localhost:8000').replace(/\/$/, '');
const WS = API.replace(/^http/, 'ws') + '/ws';
const START = {
  x: Number(q.get('x') ?? (worldName ? 0 : 100)),
  y: Number(q.get('y') ?? (worldName ? 0 : -60)),
  heading_deg: Number(q.get('heading') ?? (worldName ? 0 : 90)),
};

function worldHref(name: 'projection' | 'vision' | 'crater' | null): string {
  const next = new URLSearchParams(location.search);
  next.delete('base');
  next.delete('x');
  next.delete('y');
  next.delete('heading');
  if (name) next.set('world', name);
  else next.delete('world');
  const s = next.toString();
  return s ? `?${s}` : location.pathname;
}

function renderWorldSwitch() {
  const items: Array<['default' | 'photo projection' | 'vision loop' | 'crater', 'projection' | 'vision' | 'crater' | null]> = [
    ['default', null],
    ['photo projection', 'projection'],
    ['vision loop', 'vision'],
    ['crater', 'crater'],
  ];
  $('worlds').innerHTML = items.map(([label, name]) => {
    const on = name === worldName;
    return `<a href="${worldHref(name)}" class="${on ? 'on' : ''}">${label}</a>`;
  }).join('');
}

/** Start at the site's base POI unless x/y are given in the URL. */
async function resolveStart(): Promise<void> {
  if (q.has('x') || q.has('y')) return;
  try {
    const r = await fetch(`${BASE}/data/pois.geojson`);
    const base = (await r.json()).features.find((f: any) => f.properties?.type === 'base')?.properties;
    if (base) Object.assign(START, { x: base.x + 6, y: base.y });
  } catch {
    /* keep the default */
  }
}

const $ = (id: string) => document.getElementById(id)!;

async function fetchProfile(): Promise<RoverProfile> {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 1500);
  try {
    const r = await fetch(`${API}/site`, { signal: ctl.signal });
    if (!r.ok) throw new Error(String(r.status));
    return profileFromSite(await r.json(), 'crew_buggy') ?? { ...DEFAULT_BUGGY_PROFILE, source: `${DEFAULT_BUGGY_PROFILE.source}; /site had no crew_buggy profile` };
  } catch {
    return DEFAULT_BUGGY_PROFILE;
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
        // toneMapped: false keeps the imagery's own colours under ACES tone mapping.
        mat = new THREE.MeshBasicMaterial({ map: tex, toneMapped: false });
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
  renderWorldSwitch();
  if (worldError) {
    $('msg').textContent = worldError;
    return;
  }
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setSize(innerWidth, innerHeight);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  $('view').appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x2a2522);
  const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.3, 6000);
  // Lights: butterscotch sky / rust ground fill + a low warm sun with soft shadows.
  scene.add(new THREE.HemisphereLight(0xf3dcc2, 0x5a3424, 1.1));
  scene.add(new THREE.AmbientLight(0xffffff, 0.12));
  const sun = new THREE.DirectionalLight(0xfff2e0, 3.2);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.02;
  sun.shadow.radius = 3;
  Object.assign(sun.shadow.camera, { left: -6, right: 6, top: 6, bottom: -6, near: 1, far: 90 });
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
  if (!worldName) await resolveStart();
  const controls = new KeyboardControls();
  const link = new RoverLink({ url: WS });
  const driver = new RoverDriver({ sampler, profile, params: BUGGY_PARAMS, controls, link, start: START });

  const buggy = createBuggyModel({ castShadow: true });
  const rover = buggy.group;
  scene.add(rover);
  const wheelOffsets = (p: { x: number; y: number; z: number; heading_deg: number; pitch_deg: number; roll_deg: number }): [number, number, number, number] => {
    // Terrain height under each wheel minus the body plane, so the suspension follows bumps.
    const [fx, fy] = headingToDir(p.heading_deg);
    const rx = fy;
    const ry = -fx;
    const a = BUGGY_DIMENSIONS.wheelbase_m / 2;
    const b = BUGGY_DIMENSIONS.track_m / 2;
    const tp = Math.tan((p.pitch_deg * Math.PI) / 180);
    const tr = Math.tan((p.roll_deg * Math.PI) / 180);
    const out: [number, number, number, number] = [0, 0, 0, 0];
    [[a, -b], [a, b], [-a, -b], [-a, b]].forEach(([fa, rb], i) => {
      const h = sampler.sampleHeight(p.x + fx * fa + rx * rb, p.y + fy * fa + ry * rb);
      const plane = p.z + fa * tp - rb * tr;
      out[i] = Number.isFinite(h) ? Math.max(-0.12, Math.min(0.12, h - plane)) : 0;
    });
    return out;
  };
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
    buggy.update({ speedMps: p.display_speed_mps, steerDeg: p.steer * 25, boost: p.boost_active, dt, wheelGroundOffsets: wheelOffsets(p) });
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
      const statusCls = ['sliding', 'slipping', 'tip_risk', 'blocked_edge', 'steep'].includes(String(p.status)) ? 'bad' : p.status === 'caution' ? 'warn' : '';
      const fc = st.forces;
      hud.innerHTML = `<table>
        <tr><td>x, y</td><td>${f(p.x)} E, ${f(p.y)} N m</td></tr>
        <tr><td>elevation</td><td>${f(p.z, 2)} m</td></tr>
        <tr><td>heading</td><td>${f(p.heading_deg)}&deg;</td></tr>
        <tr><td>pitch / roll</td><td>${f(p.pitch_deg)}&deg; / ${f(p.roll_deg)}&deg;</td></tr>
        <tr><td>slope under</td><td class="${slopeCls}">${f(p.slope_deg)}&deg;</td></tr>
        <tr><td>slope ahead</td><td class="${aheadCls}">${f(p.ahead_slope_deg)}&deg;</td></tr>
        <tr><td>speed</td><td>${f(Math.abs(p.speed_mps) * 3.6, 1)} km/h mission (${f(Math.abs(p.display_speed_mps), 1)} m/s on screen, time x${f(prof.display_speed_factor * p.time_scale, 2)})</td></tr>
        <tr><td>boost</td><td class="${p.boost_active ? 'warn' : ''}"><span class="meter"><span style="width:${Math.round(p.boost_charge * 100)}%"></span></span> ${p.boost_active ? 'BOOST' : prof.boost_speed_mps ? `${f(prof.boost_speed_mps * 3.6, 0)} km/h max` : 'n/a'}</td></tr>
        <tr><td>mode</td><td>${p.mode}</td></tr>
        <tr><td>status</td><td class="${statusCls}">${p.status}</td></tr>
        <tr><td>note</td><td>${p.stop_reason ?? '-'}</td></tr>
        <tr><td>forces</td><td>motor ${f(fc.motor, 0)} N, gravity ${f(fc.gravity, 0)} N, rolling ${f(fc.resistance, 0)} N (grip limit ${f(fc.grip_limit, 0)} N)</td></tr>
        <tr><td>odometer</td><td>${f(st.odometer_m)} m, mission time ${f(st.mission_time_s / 60)} min</td></tr>
        <tr><td>API link</td><td>${driver.linkStatus} (${WS})</td></tr>
        <tr class="sep"><td>caution slope</td><td>${prof.caution_slope_deg}&deg;</td></tr>
        <tr><td>no-go slope</td><td>${prof.nogo_slope_deg}&deg;</td></tr>
        <tr><td>vehicle</td><td>${prof.mass_kg} kg, ${prof.power_w} W, grip &mu; ${prof.traction_mu}, rolling ${prof.rolling_coeff}, cap ${prof.flat_speed_mps} m/s${prof.min_turn_radius_m ? `, turn radius ${prof.min_turn_radius_m} m` : ''}</td></tr>
        <tr><td colspan="2" class="src">W/S drive &middot; A/D steer &middot; Shift/Space boost &middot; [ ] time-lapse &middot; R reset &middot; wheel zoom</td></tr>
        <tr><td colspan="2" class="src">Limits source: ${prof.source}. Force model: Mars g 3.71 m/s&sup2;; motor limited by power and grip. Slope limits are safety advisories, not walls.</td></tr>
      </table>`;
    }
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}

main();
