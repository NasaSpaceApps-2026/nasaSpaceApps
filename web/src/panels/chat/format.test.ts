import { describe, expect, it } from 'vitest';
import { humanToolName, summarizeArgs } from './format';
import { parseBlocks } from './markdown';

describe('summarizeArgs', () => {
  it('formats scalars', () => {
    expect(summarizeArgs({ target: 'carbonate', radius_m: 2000, flag: true })).toBe('target: carbonate, radius_m: 2000, flag: true');
  });
  it('rounds numbers and formats points', () => {
    expect(summarizeArgs({ near: { x: 10.123456, y: -4 }, d: 1.23456 })).toBe('near: (10.12, -4), d: 1.23');
  });
  it('handles empty, null, arrays and nested objects', () => {
    expect(summarizeArgs({})).toBe('no arguments');
    expect(summarizeArgs(null)).toBe('');
    expect(summarizeArgs({ a: [1, 2], b: [{}, {}, {}, {}], c: { k: 1 } })).toBe('a: [1, 2], b: 4 items, c: {1 fields}');
  });
  it('truncates long strings and long lists of args', () => {
    const s = summarizeArgs({ q: 'x'.repeat(100) });
    expect(s.length).toBeLessThan(40);
    const many = summarizeArgs(Object.fromEntries(Array.from({ length: 20 }, (_, i) => [`key${i}`, 'value'])));
    expect(many).toMatch(/more/);
  });
});

describe('humanToolName', () => {
  it('humanizes', () => {
    expect(humanToolName('get_site_overview')).toBe('Get site overview');
    expect(humanToolName('mcp__ares__find_sites')).toBe('Find sites');
  });
});

describe('parseBlocks', () => {
  it('splits paragraphs and lists', () => {
    const b = parseBlocks('Intro\nline2\n\n- a\n- b\n\n1. one\n2. two');
    expect(b.map((x) => x.type)).toEqual(['p', 'ul', 'ol']);
  });
  it('keeps html as plain text (rendered by React as text)', () => {
    const b = parseBlocks('<img src=x onerror=alert(1)>');
    expect(b).toEqual([{ type: 'p', lines: ['<img src=x onerror=alert(1)>'] }]);
  });
});

describe('summarizeArgs for Atlas tools', () => {
  it('renders find_regions conditions, within and objective', () => {
    const s = summarizeArgs({
      conditions: [
        { evidence: 'carbonate', min_tier: 'weak_orbital' },
        { layer: 'slope_2m', op: '<=', value: 10 },
      ],
      within: { from: 'base', max: 120, metric: 'walk_time_min', profile: 'eva_walkable' },
      objective: 'best_evidence:carbonate',
    });
    expect(s).toBe('carbonate ≥ weak_orbital · slope_2m ≤ 10 · within 120 min walk of base · objective best_evidence:carbonate');
  });
  it('renders any_of, in-lists, profiles and location refs', () => {
    expect(
      summarizeArgs({
        profile: 'landing_pad',
        conditions: [
          { any_of: [{ evidence: 'carbonate', min_tier: 'weak_orbital' }, { evidence: 'olivine', min_tier: 'weak_orbital' }] },
          { layer: 'geologic_unit', op: 'in', value: ['Seitah', 'Maaz'] },
        ],
        within: { from: { x: 812, y: 455 }, max: 500, metric: 'straight_m' },
        limit: 3,
      }),
    ).toBe('profile landing_pad · (carbonate ≥ weak_orbital or olivine ≥ weak_orbital) · geologic_unit in [Seitah, Maaz] · within 500 m of (812, 455) · limit 3');
    expect(summarizeArgs({ within: { from: ['marker:m1', 'set:3'], max: 300, metric: 'walk_m' } })).toBe('within 300 m walk of marker:m1, set:3');
  });
  it('falls back for unknown condition shapes and non-Atlas args', () => {
    expect(summarizeArgs({ conditions: [{ weird: 1 }], objective: 'flattest' })).toBe('weird 1 · objective flattest');
    expect(summarizeArgs({ from: 'base', to: { x: 1, y: 2 } })).toBe('from: base, to: (1, 2)');
  });
});
