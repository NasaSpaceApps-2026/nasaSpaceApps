import { useCallback, useEffect, useReducer, useRef } from 'react';
import { HttpError, streamChat } from './sse';
import { buildChatBody, newSessionId } from './session';
import { initialState, reducer } from './state';
import type { ToolEvent } from './types';

export const DEFAULT_AGENT_URL = '/__agent'; // same-origin dev proxy to the agent on :8766 (web/vite.config.ts)
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
  const optsRef = useRef(opts);
  const sessionRef = useRef<string>('');
  if (!sessionRef.current) sessionRef.current = newSessionId();
  useEffect(() => {
    stateRef.current = state;
    optsRef.current = opts;
  });

  useEffect(() => () => abortRef.current?.abort(), []);

  const send = useCallback((raw: string) => {
    const text = raw.trim();
    if (!text || stateRef.current.streaming) return;
    const { agentUrl = DEFAULT_AGENT_URL, getContext, onToolEvent } = optsRef.current;
    const base = agentUrl.replace(/\/+$/, '');
    const earlier = stateRef.current.messages;
    const sessionId = sessionRef.current;
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
    // Smooth streaming: coalesce text deltas into one render per animation frame (tool/done events flush first, order kept).
    let pendingText = '';
    let raf = 0;
    const flush = () => {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      if (!pendingText || ctrl.signal.aborted) { pendingText = ''; return; }
      const delta = pendingText;
      pendingText = '';
      dispatch({ type: 'event', event: { event: 'text', data: { delta } } });
    };
    streamChat(
      `${base}/chat`,
      buildChatBody(sessionId, earlier, text, context),
      (event) => {
        if (ctrl.signal.aborted) return;
        if (event.event === 'text') {
          pendingText += event.data.delta;
          if (!raf) raf = requestAnimationFrame(flush);
          return;
        }
        flush();
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
              structured: event.data.structured,
            });
          }
        } catch (e) {
          console.error('onToolEvent handler threw', e);
        }
      },
      ctrl.signal,
    ).then(
      () => {
        flush();
        // Stream closed without a done/error event: finalize so the UI never hangs.
        if (!ctrl.signal.aborted && stateRef.current.streaming) dispatch({ type: 'event', event: { event: 'done', data: {} } });
      },
      (err) => {
        flush();
        if (ctrl.signal.aborted) return;
        dispatch({ type: 'fail', message: describeFailure(err, base) });
      },
    );
  }, []);

  const stop = useCallback(() => {
    abortRef.current?.abort(); // pending (unflushed) text is dropped by flush()
    dispatch({ type: 'stop' });
  }, []);

  const clear = useCallback(() => {
    abortRef.current?.abort();
    sessionRef.current = newSessionId(); // a cleared chat is a new conversation on the agent too
    dispatch({ type: 'clear' });
  }, []);

  return { state, send, stop, clear };
}
