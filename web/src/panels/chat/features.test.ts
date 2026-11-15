import { describe, it, expect } from 'vitest';
import { extractFromResult, extractFromText, focusFromCamera, formatFeatureLabel, formatRouteLabel } from './features';

describe('extractFromResult', () => {
  it('reads find_locations results[] with scores', () => {
    const r = extractFromResult({ name: 'find_locations', args: {}, structured: { set_id: 'set:1', results: [
      { id: '1.1', x: 10, y: 20, score: 1.15, clear_radius_m: 50 }, { id: '1.2', x: 30, y: 40 }, { id: 'bad' }] } });
    expect(r.features.map((f) => f.id)).toEqual(['1.1', '1.2']);
    expect(r.features[0]).toMatchObject({ kind: 'spot', radius: 50, tier: 'score 1.15', source: 'find_locations' });
    expect(r.focus).toMatchObject({ x: 10, y: 20 });
  });
  it('reads find_sites with tier names and a route', () => {
    const s = extractFromResult({ name: 'find_sites', args: {}, structured: { sites: [{ id: 's1', x: 1, y: 2, best_tier_name: 'strong_orbital', area_m2: 3000 }] } });
    expect(s.features[0]).toMatchObject({ kind: 'region', tier: 'strong orbital' });
    const p = extractFromResult({ name: 'plan_route', args: {}, structured: { route_id: 'r1', distance_m: 1500, points: [{ x: 0, y: 0 }, { x: 5, y: 5 }, { x: 9, y: 9 }] } });
    expect(p.routes[0].points).toHaveLength(3);
    expect(p.routes[0].label).toBe('Route · 1.5 km');
  });
  it('takes the camera target of fly_camera, ignores overview and errors, never throws', () => {
    expect(extractFromResult({ name: 'fly_camera', args: { distance_m: 300 }, structured: { target: { id: 'poi:x', x: 5, y: 6 } } }).focus)
      .toMatchObject({ x: 5, y: 6, label: 'poi:x', distance_m: 300 });
    expect(extractFromResult({ name: 'get_site_overview', args: {}, structured: { pois: [{ id: 'a', x: 1, y: 1 }] } }).features).toEqual([]);
    expect(extractFromResult({ name: 'find_regions', args: {}, structured: { members: 'nope' }, isError: false }).features).toEqual([]);
    expect(extractFromResult({ name: 'find_regions', args: {}, structured: undefined }).features).toEqual([]);
    expect(extractFromResult({ name: 'find_regions', args: {}, structured: { members: [{ id: 'a', x: 1, y: 1 }] }, isError: true }).features).toEqual([]);
  });
  it('parses view.camera websocket messages', () => {
    expect(focusFromCamera({ type: 'view.camera', x: 1, y: 2, mode: 'orbit', distance: 400, target: 'Delta' })).toMatchObject({ x: 1, y: 2, distance_m: 400, label: 'Delta' });
    expect(focusFromCamera({ type: 'view.camera', mode: 'orbit' })).toBeNull();
  });

  it('labels pads, regions, POIs and routes from the result kind, never showing a raw score', () => {
    const pad = extractFromResult({ name: 'find_locations', args: { profile: 'landing_pad' }, structured: { results: [
      { id: '1.1', x: 1, y: 2, score: 1.15, attributes: { slope_2m_mean: 1.1471, walk_time_min: 14.04 } }, { id: '1.2', x: 3, y: 4, score: 1.2 }] } });
    expect(pad.features[0].label).toBe('Pad 1.1 · slope 1.1° · 14 min walk');
    expect(pad.features[1].label).toBe('Pad 1.2');
    expect(pad.features.map((f) => f.label).join()).not.toMatch(/score/);
    const poi = extractFromResult({ name: 'search_pois', args: {}, structured: { pois: [{ id: 'sample_11_skyland', name: 'Sample 11: Skyland', x: 1, y: 2 }] } });
    expect(poi.features[0].label).toBe('Skyland');
  });
  it('formats regions and routes, omitting missing fields', () => {
    expect(formatFeatureLabel('region', { area_m2: 430000, evidence: { olivine: { best_tier_name: 'strong_orbital' } } }, '1.2')).toBe('Region 1.2 · strong_orbital olivine · 0.43 km²');
    expect(formatFeatureLabel('region', {}, '3')).toBe('Region 3');
    expect(formatFeatureLabel('spot', { attributes: { walk_time_min: 29.6 } }, '1.2', { profile: 'eva_walkable' })).toBe('Spot 1.2 · 30 min walk');
    expect(formatFeatureLabel('marker', {}, 'm1')).toBe('m1');
    expect(formatRouteLabel({ distance_m: 2100, duration_min: 47.6 })).toBe('Route · 2.1 km · 48 min');
    expect(formatRouteLabel({ route: { duration_min: 10 } })).toBe('Route · 10 min');
    expect(formatRouteLabel({})).toBe('Route');
  });

  it('falls back to the tool text when no structuredContent was forwarded', () => {
    const pads = extractFromResult({ name: 'find_locations', args: { profile: 'landing_pad' }, isError: false, result:
      'Found 149 spots; showing top 2 by flattest. Full ranked list stored as set:3.\n1. 3.1 (2393, -81) m, clear radius 50 m, score 1.15, slope_2m_mean=1.1, slope_2m_max=4, walk_time_min=14\n2. 3.2 (699, 31) m, clear radius 50 m, score 1.16, slope_2m_mean=1.2, walk_time_min=29.6\nDiagnostics: x' });
    expect(pads.features.map((f) => [f.id, f.x, f.y, f.radius])).toEqual([['3.1', 2393, -81, 50], ['3.2', 699, 31, 50]]);
    expect(pads.features[0].label).toBe('Pad 3.1 · slope 1.1° · 14 min walk');
    expect(pads.focus).toMatchObject({ x: 2393, y: -81 });
  });
  it('parses sites, POIs, markers, camera and rover text', () => {
    const t = (tool: string, text: string, args = {}) => extractFromText(tool, text, args);
    const sites = t('find_sites', '131 site(s) for carbonate. top 8.\n1. 1.1 (1311, -1149) m, best tier 4 confirmed_in_situ, region area 1060 m2 in total; mean slope 4.9 deg; layer carbonate');
    expect(sites.features[0]).toMatchObject({ id: '1.1', kind: 'region', x: 1311, y: -1149, tier: 'confirmed in situ' });
    expect(sites.features[0].label).toBe('Region 1.1 · confirmed_in_situ carbonate · 1060 m²');
    const pois = t('search_pois', '10 POI(s):\nDistances are x\n- poi:region_delta_front Delta Front (sampling area) [named_region] (-833, 170) m, 0 m away\n- poi:sample_16_kukaklek Sample 16: Kukaklek [sample_site] (-931, -22) m, 216 m away');
    expect(pois.features.map((f) => [f.id, f.label, f.x])).toEqual([['poi:region_delta_front', 'Delta Front (sampling area)', -833], ['poi:sample_16_kukaklek', 'Kukaklek', -931]]);
    expect(t('place_marker', 'Placed marker:m1 "Overlook" at (1361, -1017) m (z -2567).').features[0]).toMatchObject({ id: 'm1', kind: 'marker', label: 'Overlook' });
    expect(t('fly_camera', 'View update sent: mode orbit; target Delta Front (sampling area) (-833, 170) m.', { distance_m: 400 }).focus)
      .toMatchObject({ x: -833, y: 170, mode: 'orbit', distance_m: 400 });
    expect(t('teleport_rover', 'ARRIVED\nRover now (-833, 170) m, heading 0 deg').focus).toMatchObject({ x: -833, y: 170 });
    const d = t('drive_to', 'ARRIVED with buggy: ok\nRoute r9: 2.1 km, 30 min planned.\nDrove 2.1 km; rover now at (-1410, 985) m, z -2446 m, vehicle buggy.\nGoal highest point (-1400, 980) m, z -2440 m.');
    expect(d.routeRef).toEqual({ id: 'r9', goal: undefined });
    expect(d.focus).toMatchObject({ x: -1410, y: 985 });
    expect(d.features[0]).toMatchObject({ id: 'goal', x: -1400, y: 980 });
    expect(t('plan_route', 'Route route-2 (eva_walkable, slope limit 25 deg): 2698 m.\nStops: goal (1311, -1149) at 2698 m').routeRef).toEqual({ id: 'route-2', goal: [1311, -1149] });
    expect(t('find_locations', 'Found 0 spots').features).toEqual([]);
  });
});
