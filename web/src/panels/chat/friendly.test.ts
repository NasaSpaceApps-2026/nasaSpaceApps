import { describe, it, expect } from 'vitest';
import { describeHealth, friendlyError } from './friendly';

describe('friendlyError', () => {
  it('maps the common failures to a fix', () => {
    expect(friendlyError('Cannot reach the agent at http://localhost:8766. Start it').fix).toMatch(/stack\/up\.sh/);
    expect(friendlyError('Tool server unavailable: Could not connect to the MCP server: x').title).toMatch(/tool server/i);
    expect(friendlyError('The DeepSeek API rejected the credentials (check DEEPSEEK_API_KEY).').fix).toMatch(/DEEPSEEK_API_KEY=/);
    expect(friendlyError('DeepSeek API: insufficient balance (402).').title).toMatch(/credit/);
    expect(friendlyError('Rate limited by the DeepSeek API.').title).toMatch(/rate/);
    expect(friendlyError('weird').title).toBe('Something went wrong.');
  });
});

describe('describeHealth', () => {
  it('reports provider/model, missing key and offline', () => {
    expect(describeHealth({ ok: true, provider: 'deepseek', model: 'deepseek-chat', api_key_present: true }, false)).toMatchObject({ label: 'deepseek · deepseek-chat', level: 'ok' });
    expect(describeHealth({ ok: true, provider: 'deepseek', model: 'm', api_key_present: false }, false)).toMatchObject({ level: 'warn' });
    expect(describeHealth(null, true).banner?.title).toMatch(/not running/);
    expect(describeHealth(null, false).level).toBe('checking');
  });
});
