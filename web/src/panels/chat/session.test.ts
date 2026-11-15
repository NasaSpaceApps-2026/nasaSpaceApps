import { describe, expect, it } from 'vitest';
import { buildChatBody, newSessionId } from './session';
import type { ChatMessage } from './types';

const earlier: ChatMessage[] = [
  { id: 'a', role: 'user', text: 'find landing pads' },
  { id: 'b', role: 'assistant', parts: [{ kind: 'text', text: 'Three pads.' }] },
];

describe('chat session', () => {
  it('ids are unique and URL-safe', () => {
    const a = newSessionId();
    expect(a).not.toBe(newSessionId());
    expect(a).toMatch(/^[\w.-]{1,64}$/);
  });
  it('body carries only the new message, the session id and a text history fallback', () => {
    const b = buildChatBody('s1', earlier, 'the second one', { camera: { z: 1 } });
    expect(b).toEqual({
      message: 'the second one',
      session_id: 's1',
      history: [
        { role: 'user', content: 'find landing pads' },
        { role: 'assistant', content: 'Three pads.' },
      ],
      context: { camera: { z: 1 } },
    });
    expect(b).not.toHaveProperty('messages');
  });
  it('first message has no history', () => {
    expect(buildChatBody('s1', [], 'hi')).toEqual({ message: 'hi', session_id: 's1' });
  });
});
