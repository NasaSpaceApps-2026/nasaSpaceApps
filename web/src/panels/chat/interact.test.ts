import { describe, expect, it } from 'vitest';
import { fmtDist, fmtLatLon, fmtMinutes, mineralLines, noMineralData, placeQuestions, placesFromPois, shortCitation, slopeWords, type DescribeLoc, type Poi } from '../../interact/words';

const loc = (values: DescribeLoc['values'], evidence?: DescribeLoc['evidence']): DescribeLoc => ({ x: 0, y: 0, lat: 18, lon: 77, values, evidence });

describe('mineral words', () => {
  it('says confirmed minerals with sample and citation', () => {
    const l = loc({ carbonate: { tier: 4, tier_name: 'confirmed_in_situ' } }, {
      carbonate: { in_situ: { samples: [{ name: 'Sample 5: Coulettes', citation: 'Farley et al. 2022, Science 377, doi:10.1126/science.abo2196' }] } },
    });
    const c = mineralLines(l).find((m) => m.layer === 'carbonate')!;
    expect(c.tier).toBe('confirmed');
    expect(c.text).toBe('confirmed by the rover at Sample 5 (Farley et al. 2022)');
  });
  it('orbital tiers and missing data', () => {
    const l = loc({ olivine: { tier: 3, tier_name: 'strong_orbital' }, kaolinite: { tier: 2, tier_name: 'weak_orbital' } });
    const lines = mineralLines(l);
    expect(lines[0].text).toBe('seen from orbit (strong)');
    expect(lines[1].text).toBe('seen from orbit (weak)');
    expect(lines.find((m) => m.layer === 'carbonate')!.text).toBe('no orbital data here');
    expect(noMineralData(mineralLines(loc({})))).toBe(true);
  });
});

describe('formatting', () => {
  it('formats', () => {
    expect(shortCitation('Williams et al. 2023, LPSC 54')).toBe('Williams et al. 2023');
    expect(fmtMinutes(0.4)).toBe('<1 min');
    expect(fmtMinutes(125)).toBe('2 h 5 min');
    expect(fmtDist(2850)).toBe('2.85 km');
    expect(fmtLatLon(21.55, -137.68)).toBe('21.5500°N  222.3200°E');
    expect(slopeWords(30)).toBe('too steep to walk');
  });
});

describe('places', () => {
  const pois: Poi[] = [
    { id: 'base', name: 'Mission base (assumed)', type: 'base', x: 10, y: 10 },
    { id: 'land', name: 'Octavia E. Butler Landing', type: 'landmark', x: 10, y: 10 },
    { id: 'r', name: 'South Séítah (sampling area)', type: 'named_region', x: 0, y: 0 },
    { id: 's4', name: 'Sample 4: Salette', type: 'sample_site', x: 100, y: 100, sol: 262 },
    { id: 's5', name: 'Sample 5: Coulettes', type: 'sample_site', x: 100, y: 100, sol: 271 },
  ];
  it('merges base into landing, groups samples, cleans names', () => {
    const pl = placesFromPois(pois);
    expect(pl.map((p) => p.name)).toEqual(['Octavia E. Butler Landing', 'South Séítah', 'Salette · sample 4 +1']);
    expect(placeQuestions(pl[1])).toEqual(['What did the rover find at South Séítah?', 'Drive the buggy to South Séítah']);
  });
});
