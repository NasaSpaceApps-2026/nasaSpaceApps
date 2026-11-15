import { describe, expect, it } from 'vitest';
import { argPoints, highlightsFor } from './highlights';

describe('highlightsFor', () => {
  it('reads ranked list lines of find_locations', () => {
    const text = 'Found 1699 spots; showing top 2.\n1. 30.1 (2533, 1287) m, clear radius 58 m\n2. 30.2 (-2623.5, 1791) m, clear radius 50 m\nDiagnostics: x (1, 2)';
    const h = highlightsFor('find_locations', {}, text, false, 'c1');
    expect(h.map((e) => [e.x, e.y, e.label])).toEqual([[2533, 1287, '30.1'], [-2623.5, 1791, '30.2']]);
    expect(h.every((e) => e.kind === 'result')).toBe(true);
  });
  it('reads the destination of teleport and plan_route', () => {
    expect(highlightsFor('teleport_rover', {}, 'ARRIVED - teleported to (-833, 170)\nRover now (-833, 170) m', false, 'c')[0]).toMatchObject({ x: -833, y: 170, kind: 'target' });
    const r = 'Route route-6 (eva_walkable): 2868 m\nStops: goal (-665, 409) at 2868 m';
    expect(highlightsFor('plan_route', {}, r, false, 'c')[0]).toMatchObject({ x: -665, y: 409 });
  });
  it('falls back to argument points and ignores errors', () => {
    expect(highlightsFor('goto_location', { target: { x: 5, y: 6 } }, 'no coords', false, 'c')[0]).toMatchObject({ x: 5, y: 6 });
    expect(highlightsFor('teleport_rover', {}, 'to (1, 2)', true, 'c')).toEqual([]);
    expect(highlightsFor('list_layers', {}, '(1, 2)', false, 'c')).toEqual([]);
  });
});

describe('argPoints', () => {
  it('finds xy objects and pairs', () => {
    expect(argPoints({ a: { x: 1, y: 2 }, b: [3, 4], c: 'poi:base' })).toEqual([[1, 2], [3, 4]]);
  });
});
