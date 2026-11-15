import { describe, expect, it } from 'vitest';
import { SseParser, toAgentEvent } from './sse';

const run = (chunks: string[]) => {
  const p = new SseParser();
  const out = chunks.flatMap((c) => p.push(c));
  return [...out, ...p.finish()];
};

describe('SseParser', () => {
  it('parses one event', () => {
    expect(run(['event: text\ndata: {"delta":"hi"}\n\n'])).toEqual([{ event: 'text', data: '{"delta":"hi"}', id: undefined }]);
  });
  it('handles multiple events in one chunk', () => {
    const r = run(['event: a\ndata: 1\n\nevent: b\ndata: 2\n\n']);
    expect(r.map((m) => [m.event, m.data])).toEqual([['a', '1'], ['b', '2']]);
  });
  it('reassembles events split at arbitrary byte boundaries', () => {
    const full = 'event: text\ndata: {"delta":"hello"}\n\nevent: done\ndata: {}\n\n';
    for (let i = 1; i < full.length; i++) {
      const r = run([full.slice(0, i), full.slice(i)]);
      expect(r.map((m) => m.event)).toEqual(['text', 'done']);
      expect(r[0].data).toBe('{"delta":"hello"}');
    }
    const char = run(full.split(''));
    expect(char).toHaveLength(2);
  });
  it('supports CRLF, including a split between CR and LF', () => {
    const r = run(['event: text\r\ndata: x\r', '\n\r\n']);
    expect(r).toEqual([{ event: 'text', data: 'x', id: undefined }]);
  });
  it('joins multi-line data with newlines', () => {
    expect(run(['data: a\ndata: b\ndata:c\n\n'])[0].data).toBe('a\nb\nc');
  });
  it('ignores comments and keep-alive pings', () => {
    const r = run([': ping\n\nevent: x\n: note\ndata: 1\n\n']);
    expect(r).toHaveLength(1);
    expect(r[0].event).toBe('x');
  });
  it('defaults event name to message and does not leak fields between events', () => {
    const r = run(['event: a\ndata: 1\n\ndata: 2\n\n']);
    expect(r.map((m) => m.event)).toEqual(['a', 'message']);
  });
  it('flushes a trailing event without blank line on finish', () => {
    expect(run(['event: done\ndata: {}'])[0].event).toBe('done');
  });
  it('does not emit events with no data', () => {
    expect(run(['event: x\n\n'])).toEqual([]);
  });
});

describe('toAgentEvent', () => {
  it('maps known events and validates shape', () => {
    expect(toAgentEvent({ event: 'text', data: '{"delta":"a"}' })).toEqual({ event: 'text', data: { delta: 'a' } });
    expect(toAgentEvent({ event: 'tool_call_start', data: '{"id":"1","name":"t"}' })).toEqual({
      event: 'tool_call_start',
      data: { id: '1', name: 't', args: {} },
    });
    expect(toAgentEvent({ event: 'tool_call_result', data: '{"id":"1","summary":"s","is_error":true,"result":"r"}' })).toEqual({
      event: 'tool_call_result',
      data: { id: '1', summary: 's', is_error: true, result: 'r' },
    });
  });
  it('returns null for malformed JSON or unknown events', () => {
    expect(toAgentEvent({ event: 'done', data: 'nope' })).toBeNull();
    expect(toAgentEvent({ event: 'weird', data: '{}' })).toBeNull();
    expect(toAgentEvent({ event: 'text', data: '{}' })).toBeNull();
  });
});

describe('toAgentEvent tolerance', () => {
  it('accepts a bare string or JSON string as text data', async () => {
    const { toAgentEvent } = await import('./sse');
    expect(toAgentEvent({ event: 'text', data: 'hello world' })).toEqual({ event: 'text', data: { delta: 'hello world' } });
    expect(toAgentEvent({ event: 'text', data: '"hi"' })).toEqual({ event: 'text', data: { delta: 'hi' } });
    expect(toAgentEvent({ event: 'text', data: '{"delta":"x"}' })).toEqual({ event: 'text', data: { delta: 'x' } });
    expect(toAgentEvent({ event: 'done', data: 'nope' })).toBeNull();
  });
});
