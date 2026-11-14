/** Shared materials for the buggy and astronaut. */

import * as THREE from 'three';
import {
  carbonTexture,
  crinkleNormalMap,
  fabricTextures,
  liveryTexture,
  marsEnvTexture,
  panelTextures,
  patchTexture,
  screenTexture,
  solarTexture,
  tireTextures,
} from './textures';

export interface BuggyMaterials {
  frame: THREE.MeshStandardMaterial;
  darkMetal: THREE.MeshStandardMaterial;
  chrome: THREE.MeshStandardMaterial;
  paint: THREE.MeshStandardMaterial;
  carbon: THREE.MeshPhysicalMaterial;
  gold: THREE.MeshStandardMaterial;
  orange: THREE.MeshStandardMaterial;
  black: THREE.MeshStandardMaterial;
  seat: THREE.MeshStandardMaterial;
  lamp: THREE.MeshStandardMaterial;
  screen: THREE.MeshStandardMaterial;
  solar: THREE.MeshPhysicalMaterial;
  dish: THREE.MeshStandardMaterial;
  livery: THREE.MeshStandardMaterial;
  tire: THREE.MeshStandardMaterial;
  rim: THREE.MeshStandardMaterial;
  bolt: THREE.MeshStandardMaterial;
  cableOrange: THREE.MeshStandardMaterial;
  // astronaut
  suit: THREE.MeshStandardMaterial;
  suitSeam: THREE.MeshStandardMaterial;
  bearing: THREE.MeshStandardMaterial;
  helmet: THREE.MeshPhysicalMaterial;
  visor: THREE.MeshPhysicalMaterial;
  glove: THREE.MeshStandardMaterial;
  boot: THREE.MeshStandardMaterial;
  accentRed: THREE.MeshStandardMaterial;
  patch: THREE.MeshStandardMaterial;
  helmetLamp: THREE.MeshStandardMaterial;
  all: THREE.Material[];
  textures: THREE.Texture[];
}

export function createMaterials(): BuggyMaterials {
  const env = marsEnvTexture();
  const crinkle = crinkleNormalMap();
  crinkle.repeat.set(2, 2);
  const carbonMap = carbonTexture();
  const panel = panelTextures();
  const tireTex = tireTextures();
  const fabric = fabricTextures();
  const solarMap = solarTexture();
  const patchMap = patchTexture();
  const liveryMap = liveryTexture();
  const screenMap = screenTexture();

  const std = (name: string, p: THREE.MeshStandardMaterialParameters) => {
    const m = new THREE.MeshStandardMaterial({ envMap: env, envMapIntensity: 0.9, ...p });
    m.name = name;
    return m;
  };
  const phys = (name: string, p: THREE.MeshPhysicalMaterialParameters) => {
    const m = new THREE.MeshPhysicalMaterial({ envMap: env, envMapIntensity: 1.0, ...p });
    m.name = name;
    return m;
  };

  const mats = {
    // anodised titanium-grey aluminium tubing
    frame: std('frame', { color: 0x9aa1aa, metalness: 0.85, roughness: 0.32 }),
    // black anodised brackets, arms
    darkMetal: std('darkMetal', { color: 0x2b2e33, metalness: 0.75, roughness: 0.4 }),
    chrome: std('chrome', { color: 0xe6e8ea, metalness: 1.0, roughness: 0.12, envMapIntensity: 1.2 }),
    paint: std('paint', { map: panel.map, bumpMap: panel.bump, bumpScale: 1.2, metalness: 0.05, roughness: 0.5 }),
    carbon: phys('carbon', { map: carbonMap, metalness: 0.2, roughness: 0.42, clearcoat: 0.7, clearcoatRoughness: 0.2 }),
    gold: std('gold', {
      color: 0xd8a64a,
      metalness: 1.0,
      roughness: 0.3,
      normalMap: crinkle,
      normalScale: new THREE.Vector2(0.9, 0.9),
      envMapIntensity: 1.3,
    }),
    orange: std('orange', { color: 0xff5a1f, metalness: 0.1, roughness: 0.42 }),
    black: std('black', { color: 0x16171a, metalness: 0.1, roughness: 0.75 }),
    seat: std('seat', { color: 0x2e3238, map: fabric.map, bumpMap: fabric.bump, bumpScale: 2, metalness: 0.0, roughness: 0.9 }),
    lamp: std('lamp', { color: 0xffffff, emissive: 0xfff3dc, emissiveIntensity: 1.6, metalness: 0, roughness: 0.15 }),
    screen: std('screen', { map: screenMap, emissive: 0xffffff, emissiveMap: screenMap, emissiveIntensity: 0.9, roughness: 0.15 }),
    solar: phys('solar', { map: solarMap, metalness: 0.3, roughness: 0.18, clearcoat: 1, clearcoatRoughness: 0.05 }),
    dish: std('dish', { color: 0xf4f2ee, metalness: 0.15, roughness: 0.38, side: THREE.DoubleSide }),
    livery: std('livery', {
      map: liveryMap,
      transparent: true,
      metalness: 0.05,
      roughness: 0.45,
      polygonOffset: true,
      polygonOffsetFactor: -4,
      polygonOffsetUnits: -4,
      depthWrite: false,
    }),
    tire: std('tire', {
      map: tireTex.map,
      bumpMap: tireTex.bump,
      bumpScale: 3,
      roughnessMap: tireTex.rough,
      color: 0xffffff,
      metalness: 0,
      roughness: 0.92,
      envMapIntensity: 0.4,
    }),
    rim: std('rim', { color: 0x40444a, metalness: 0.9, roughness: 0.3 }),
    bolt: std('bolt', { color: 0xc8ccd0, metalness: 1.0, roughness: 0.25 }),
    cableOrange: std('cableOrange', { color: 0xe0601a, metalness: 0, roughness: 0.6 }),

    suit: std('suit', { map: fabric.map, bumpMap: fabric.bump, bumpScale: 1.5, color: 0xffffff, metalness: 0, roughness: 0.85, envMapIntensity: 0.6 }),
    suitSeam: std('suitSeam', { color: 0xb9b8b2, metalness: 0, roughness: 0.9 }),
    bearing: std('bearing', { color: 0xc9ced6, metalness: 0.95, roughness: 0.22 }),
    helmet: phys('helmet', { color: 0xf6f6f4, metalness: 0.05, roughness: 0.25, clearcoat: 1, clearcoatRoughness: 0.08 }),
    visor: phys('visor', {
      color: 0xffc35a,
      metalness: 1.0,
      roughness: 0.06,
      clearcoat: 1,
      clearcoatRoughness: 0.02,
      envMapIntensity: 1.6,
    }),
    glove: std('glove', { color: 0xd9d6cf, map: fabric.map, metalness: 0, roughness: 0.8 }),
    boot: std('boot', { color: 0x9a9893, metalness: 0.05, roughness: 0.75 }),
    accentRed: std('accentRed', { color: 0xc8261c, metalness: 0, roughness: 0.6 }),
    patch: std('patch', { map: patchMap, metalness: 0, roughness: 0.7, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 }),
    helmetLamp: std('helmetLamp', { color: 0xffffff, emissive: 0xfff6e0, emissiveIntensity: 2.0, roughness: 0.1 }),
  };
  const all = Object.values(mats) as THREE.Material[];
  const textures = [env, crinkle, carbonMap, panel.map, panel.bump, tireTex.map, tireTex.bump, tireTex.rough, fabric.map, fabric.bump, solarMap, patchMap, liveryMap, screenMap];
  return { ...mats, all, textures };
}
