/**
 * Contract 1 (PRD): site coordinate frame helpers. No three.js dependency.
 *
 * Site frame: metres, x east, y north, z elevation (source DEM), origin at the
 * region centre. three.js world: X = x, Y = z, Z = -y.
 *
 * Heading: degrees clockwise from north (0 = +y/north, 90 = +x/east).
 * Pitch:   degrees, positive = nose up.
 * Roll:    degrees, positive = right side down (rover rolling to its right).
 */

export const DEG = Math.PI / 180;
export const MARS_RADIUS_M = 3396190;

export interface SitePose {
  x: number;
  y: number;
  z: number;
  heading_deg: number;
  pitch_deg: number;
  roll_deg: number;
}

/** Site (x, y, z) -> three.js [X, Y, Z]. */
export function siteToThree(x: number, y: number, z: number): [number, number, number] {
  return [x, z, -y];
}

/** three.js (X, Y, Z) -> site [x, y, z]. */
export function threeToSite(X: number, Y: number, Z: number): [number, number, number] {
  return [X, -Z, Y];
}

/** Unit vector in site frame (dx east, dy north) for a heading. */
export function headingToDir(heading_deg: number): [number, number] {
  const h = heading_deg * DEG;
  return [Math.sin(h), Math.cos(h)];
}

/** Heading (deg, clockwise from north, in [0, 360)) of a site-frame direction. */
export function dirToHeading(dx: number, dy: number): number {
  return normalizeHeading(Math.atan2(dx, dy) / DEG);
}

export function normalizeHeading(h: number): number {
  const r = h % 360;
  return r < 0 ? r + 360 : r;
}

/** Signed shortest angular difference b - a in degrees, in (-180, 180]. */
export function headingDelta(a: number, b: number): number {
  let d = normalizeHeading(b - a);
  if (d > 180) d -= 360;
  return d;
}

/**
 * Euler angles (radians, order 'YXZ') for a three.js object whose model faces
 * -Z (forward), +Y up, +X right. Apply as `obj.rotation.set(x, y, z, 'YXZ')`.
 */
export function poseToEulerYXZ(heading_deg: number, pitch_deg: number, roll_deg: number): [number, number, number] {
  return [pitch_deg * DEG, -heading_deg * DEG, -roll_deg * DEG];
}

/** Site metres -> planetocentric lat/lon (degrees), sphere R = 3,396,190 m. */
export function siteToLatLon(x: number, y: number, lat0: number, lon0: number, R = MARS_RADIUS_M): [number, number] {
  const lat = lat0 + y / R / DEG;
  const lon = lon0 + x / (R * Math.cos(lat0 * DEG)) / DEG;
  return [lat, lon];
}

/** Planetocentric lat/lon (degrees) -> site metres. */
export function latLonToSite(lat: number, lon: number, lat0: number, lon0: number, R = MARS_RADIUS_M): [number, number] {
  return [R * Math.cos(lat0 * DEG) * (lon - lon0) * DEG, R * (lat - lat0) * DEG];
}
