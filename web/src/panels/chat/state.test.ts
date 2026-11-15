import { describe, expect, it } from 'vitest';
import { initialState, reducer, toWireMessages } from './state';
import type { AgentEvent, ChatState } from './types';

const ev = (event: AgentEvent): { type: 'event'; event: AgentEvent } => ({ type: 'event', event });
const start = (s: ChatState = initialState) => reducer(s, { type: 'send', text: 'hi', userId: 'u1', assistantId: 'a1' });
const asst = (s: ChatState) => {
  const m = s.messages[s.messages.length - 1];
  if (m.role !== 'assistant') throw new Error('not assistant');
  return m;
};

describe('reducer', () => {
  it('send adds a user message and an empty streaming assistant message', () => {
    const s = start();
    expect(s.streaming).toBe(true);
    expect(s.messages.map((m) => m.role)).toEqual(['user', 'assistant']);
  });

  it('full sequence: text, tool start, result, text, done', () => {
    let s = start();
    s = reducer(s, ev({ event: 'text', data: { delta: 'Looking' } }));
    s = reducer(s, ev({ event: 'text', data: { delta: ' up.' } }));
    s = reducer(s, ev({ event: 'tool_call_start', data: { id: 't1', name: 'find_sites', args: { target: 'carbonate' } } }));
    let parts = asst(s).parts;
    expect(parts).toHaveLength(2);
    expect(parts[0]).toEqual({ kind: 'text', text: 'Looking up.' });
    expect(parts[1].kind === 'tool' && parts[1].call.status).toBe('running');

    s = reducer(s, ev({ event: 'tool_call_result', data: { id: 't1', summary: '3 sites', is_error: false, result: 'full' } }));
    s = reducer(s, ev({ event: 'text', data: { delta: 'Found 3.' } }));
    s = reducer(s, ev({ event: 'done', data: { stop_reason: 'end_turn', tool_calls: 1 } }));
    parts = asst(s).parts;
    expect(parts).toHaveLength(3);
    const call = (parts[1] as { kind: 'tool'; call: import('./types').ToolCall }).call;
    expect(call).toMatchObject({ status: 'ok', summary: '3 sites', result: 'full' });
    expect(s.streaming).toBe(false);
  });

  it('marks an error result', () => {
    let s = start();
    s = reducer(s, ev({ event: 'tool_call_start', data: { id: 't', name: 'x', args: {} } }));
    s = reducer(s, ev({ event: 'tool_call_result', data: { id: 't', summary: 'bad', is_error: true } }));
    const p = asst(s).parts[0];
    expect(p.kind === 'tool' && p.call.status).toBe('error');
  });

  it('parallel tool calls resolve by id', () => {
    let s = start();
    s = reducer(s, ev({ event: 'tool_call_start', data: { id: 'a', name: 'x', args: {} } }));
    s = reducer(s, ev({ event: 'tool_call_start', data: { id: 'b', name: 'y', args: {} } }));
    s = reducer(s, ev({ event: 'tool_call_result', data: { id: 'b', summary: 'B', is_error: false } }));
    const st = asst(s).parts.map((p) => (p.kind === 'tool' ? p.call.status : 'text'));
    expect(st).toEqual(['running', 'ok']);
  });

  it('error event stores the message and settles spinners', () => {
    let s = start();
    s = reducer(s, ev({ event: 'tool_call_start', data: { id: 't', name: 'x', args: {} } }));
    s = reducer(s, ev({ event: 'error', data: { message: 'boom' } }));
    expect(asst(s).error).toBe('boom');
    expect(s.streaming).toBe(false);
    const p = asst(s).parts[0];
    expect(p.kind === 'tool' && p.call.status).toBe('error');
  });

  it('stop and fail finish the stream', () => {
    const s = reducer(start(), { type: 'stop' });
    expect(s.streaming).toBe(false);
    expect(asst(s).stopped).toBe(true);
    expect(asst(reducer(start(), { type: 'fail', message: 'down' })).error).toBe('down');
  });

  it('clear resets', () => {
    expect(reducer(start(), { type: 'clear' })).toEqual(initialState);
  });
});

describe('toWireMessages', () => {
  it('sends only plain user/assistant text, skipping tool parts and empty turns', () => {
    let s = start();
    s = reducer(s, ev({ event: 'tool_call_start', data: { id: 't', name: 'x', args: {} } }));
    s = reducer(s, ev({ event: 'text', data: { delta: 'Answer' } }));
    s = reducer(s, ev({ event: 'done', data: {} }));
    s = reducer(s, { type: 'send', text: 'more', userId: 'u2', assistantId: 'a2' });
    s = reducer(s, { type: 'stop' });
    expect(toWireMessages(s.messages)).toEqual([
      { role: 'user', content: 'hi' },
      { role: 'assistant', content: 'Answer' },
      { role: 'user', content: 'more' },
    ]);
  });
});
