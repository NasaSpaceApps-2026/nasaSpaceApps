import { useCallback, useEffect, useReducer, useRef } from 'react';
import { HttpError, streamChat } from './sse';
import { initialState, reducer, toWireMessages } from './state';
import type { ToolEvent } from './types';

export const DEFAULT_AGENT_URL = 'http://localhost:8766';
export const START_HINT = 'Start it with: .venv/bin/python -m agent';

export interface UseChatOptions {
  agentUrl?: string;
  getContext?: () => Record<string, unknown> | undefined;
  onToolEvent?: (e: ToolEvent) => void;
}

let counter = 0;
const uid = () => `m${Date.now().toString(36)}${(counter++).toString(36)}`;

export function describeFailure(err: unknown, base: string): string {
  if (err instanceof HttpError) return `${err.message}. Check the agent logs.`;
  return `Cannot reach the agent at ${base}. ${START_HINT}`;
}

export function useChat(opts: UseChatOptions) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const abortRef = useRef<AbortController | null>(null);
  const stateRef = useRef(state);
  stateRef.current = state;
  const optsRef = useRef(opts);
  optsRef.current = opts;

  useEffect(() => () => abortRef.current?.abort(), []);

  const send = useCallback((raw: string) => {
    const text = raw.trim();
    if (!text || stateRef.current.streaming) return;
    const { agentUrl = DEFAULT_AGENT_URL, getContext, onToolEvent } = optsRef.current;
    const base = agentUrl.replace(/\/+$/, '');
    const history = [...toWireMessages(stateRef.current.messages), { role: 'user' as const, content: text }];
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    dispatch({ type: 'send', text, userId: uid(), assistantId: uid() });

    let context: Record<string, unknown> | undefined;
    try {
      context = getContext?.();
    } catch {
      context = undefined;
    }
    const names = new Map<string, { name: string; args: Record<string, unknown> }>();
    streamChat(
      `${base}/chat`,
      context ? { messages: history, context } : { messages: history },
      (event) => {
        if (ctrl.signal.aborted) return;
        dispatch({ type: 'event', event });
        try {
          if (event.event === 'tool_call_start') {
            names.set(event.data.id, { name: event.data.name, args: event.data.args });
            onToolEvent?.({ phase: 'start', id: event.data.id, name: event.data.name, args: event.data.args });
          } else if (event.event === 'tool_call_result') {
            const s = names.get(event.data.id);
            onToolEvent?.({
              phase: 'result',
              id: event.data.id,
              name: s?.name ?? '',
              args: s?.args ?? {},
              summary: event.data.summary,
              isError: event.data.is_error,
              result: event.data.result,
            });
          }
        } catch (e) {
          console.error('onToolEvent handler threw', e);
        }
      },
      ctrl.signal,
    ).then(
      () => {
        // Stream closed without a done/error event: finalize so the UI never hangs.
        if (!ctrl.signal.aborted && stateRef.current.streaming) dispatch({ type: 'event', event: { event: 'done', data: {} } });
      },
      (err) => {
        if (ctrl.signal.aborted) return;
        dispatch({ type: 'fail', message: describeFailure(err, base) });
      },
    );
  }, []);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    dispatch({ type: 'stop' });
  }, []);

  const clear = useCallback(() => {
    abortRef.current?.abort();
    dispatch({ type: 'clear' });
  }, []);

  return { state, send, stop, clear };
}
