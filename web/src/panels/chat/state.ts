import type { AgentEvent, ChatMessage, ChatState, Part } from './types';

export const initialState: ChatState = { messages: [], streaming: false };

export type Action =
  | { type: 'send'; text: string; userId: string; assistantId: string }
  | { type: 'event'; event: AgentEvent }
  | { type: 'fail'; message: string }
  | { type: 'stop' }
  | { type: 'clear' };

function mapLastAssistant(state: ChatState, f: (m: Extract<ChatMessage, { role: 'assistant' }>) => ChatMessage): ChatState {
  const i = state.messages.length - 1;
  const last = state.messages[i];
  if (!last || last.role !== 'assistant') return state;
  const messages = state.messages.slice();
  messages[i] = f(last);
  return { ...state, messages };
}

/** Close any tool call still spinning (stream ended or aborted before its result). */
function settle(parts: Part[], summary: string): Part[] {
  return parts.map((p) =>
    p.kind === 'tool' && p.call.status === 'running' ? { kind: 'tool', call: { ...p.call, status: 'error', summary } } : p,
  );
}

export function reducer(state: ChatState, a: Action): ChatState {
  switch (a.type) {
    case 'send':
      return {
        streaming: true,
        messages: [
          ...state.messages,
          { id: a.userId, role: 'user', text: a.text },
          { id: a.assistantId, role: 'assistant', parts: [] },
        ],
      };
    case 'event':
      return applyEvent(state, a.event);
    case 'fail':
      return mapLastAssistant({ ...state, streaming: false }, (m) => ({
        ...m,
        error: a.message,
        parts: settle(m.parts, 'Interrupted'),
      }));
    case 'stop':
      return mapLastAssistant({ ...state, streaming: false }, (m) => ({
        ...m,
        stopped: true,
        parts: settle(m.parts, 'Stopped'),
      }));
    case 'clear':
      return initialState;
  }
}

function applyEvent(state: ChatState, e: AgentEvent): ChatState {
  switch (e.event) {
    case 'text':
      return mapLastAssistant(state, (m) => {
        const parts = m.parts.slice();
        const last = parts[parts.length - 1];
        if (last && last.kind === 'text') parts[parts.length - 1] = { kind: 'text', text: last.text + e.data.delta };
        else parts.push({ kind: 'text', text: e.data.delta });
        return { ...m, parts };
      });
    case 'tool_call_start':
      return mapLastAssistant(state, (m) => ({
        ...m,
        parts: [
          ...m.parts,
          { kind: 'tool', call: { id: e.data.id, name: e.data.name, args: e.data.args, status: 'running' } },
        ],
      }));
    case 'tool_call_result':
      return mapLastAssistant(state, (m) => ({
        ...m,
        parts: m.parts.map((p) =>
          p.kind === 'tool' && p.call.id === e.data.id
            ? {
                kind: 'tool',
                call: { ...p.call, status: e.data.is_error ? 'error' : 'ok', summary: e.data.summary, result: e.data.result },
              }
            : p,
        ),
      }));
    case 'done':
      return mapLastAssistant({ ...state, streaming: false }, (m) => ({ ...m, parts: settle(m.parts, 'No result received') }));
    case 'error':
      return mapLastAssistant({ ...state, streaming: false }, (m) => ({
        ...m,
        error: e.data.message,
        parts: settle(m.parts, 'Interrupted'),
      }));
  }
}

/** Plain-text history sent to the backend: only user text and assistant text. */
export function toWireMessages(messages: ChatMessage[]): { role: 'user' | 'assistant'; content: string }[] {
  const out: { role: 'user' | 'assistant'; content: string }[] = [];
  for (const m of messages) {
    if (m.role === 'user') out.push({ role: 'user', content: m.text });
    else {
      const text = m.parts.filter((p): p is Extract<Part, { kind: 'text' }> => p.kind === 'text').map((p) => p.text).join('').trim();
      if (text) out.push({ role: 'assistant', content: text });
    }
  }
  return out;
}
