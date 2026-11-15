import { toWireMessages } from './state';
import type { ChatMessage } from './types';

/** A fresh opaque conversation id. The agent keeps the full transcript (tool results included) under it. */
export function newSessionId(): string {
  const rnd = typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID().replace(/-/g, '').slice(0, 16) : Math.random().toString(36).slice(2, 18);
  return `w${rnd}`;
}

/** Earlier text turns sent only so a restarted agent can rebuild the chat; the agent ignores them when it knows the session. */
const HISTORY_TURNS = 16;

/** POST /chat body: just the new message and the session id (plus text history as a restart fallback and the viewer context). */
export function buildChatBody(
  sessionId: string,
  earlier: ChatMessage[],
  text: string,
  context?: Record<string, unknown>,
): Record<string, unknown> {
  const history = toWireMessages(earlier).slice(-HISTORY_TURNS);
  const body: Record<string, unknown> = { message: text, session_id: sessionId };
  if (history.length) body.history = history;
  if (context) body.context = context;
  return body;
}
