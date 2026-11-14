export interface SseMessage {
  event: string;
  data: string;
  id?: string;
}

/**
 * Incremental Server-Sent-Events parser. Feed it arbitrary text chunks (they may split
 * lines or events anywhere); it returns the complete messages found so far.
 * Handles CRLF / LF / CR line endings, multi-line `data:`, comments (": ping") and
 * the optional single space after the colon.
 */
export class SseParser {
  private buffer = '';
  private event = '';
  private data: string[] = [];
  private id: string | undefined;
  private hasData = false;
  private lastWasCR = false;

  push(chunk: string): SseMessage[] {
    const out: SseMessage[] = [];
    let text = chunk;
    // A CR at the end of the previous chunk may be followed by LF now.
    if (this.lastWasCR && text.startsWith('\n')) text = text.slice(1);
    this.lastWasCR = text.endsWith('\r');
    this.buffer += text;
    const lines = this.buffer.split(/\r\n|\n|\r/);
    // Last element is an incomplete line (or empty if buffer ended on a terminator).
    this.buffer = lines.pop() ?? '';
    for (const line of lines) this.line(line, out);
    return out;
  }

  /** Call at end of stream; flushes a trailing event that lacked its blank line. */
  finish(): SseMessage[] {
    const out: SseMessage[] = [];
    if (this.buffer) {
      this.line(this.buffer, out);
      this.buffer = '';
    }
    this.dispatch(out);
    return out;
  }

  private line(line: string, out: SseMessage[]) {
    if (line === '') return this.dispatch(out);
    if (line.startsWith(':')) return;
    const i = line.indexOf(':');
    const field = i === -1 ? line : line.slice(0, i);
    let value = i === -1 ? '' : line.slice(i + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') this.event = value;
    else if (field === 'data') {
      this.data.push(value);
      this.hasData = true;
    } else if (field === 'id') this.id = value;
  }

  private dispatch(out: SseMessage[]) {
    if (this.hasData) {
      out.push({ event: this.event || 'message', data: this.data.join('\n'), id: this.id });
    }
    this.event = '';
    this.data = [];
    this.hasData = false;
  }
}

/** Parse a raw SSE message into a typed agent event; returns null for unknown/malformed ones. */
export function toAgentEvent(m: SseMessage): import('./types').AgentEvent | null {
  let data: unknown;
  try {
    data = JSON.parse(m.data);
  } catch {
    return null;
  }
  if (typeof data !== 'object' || data === null) return null;
  const d = data as Record<string, unknown>;
  switch (m.event) {
    case 'text':
      return typeof d.delta === 'string' ? { event: 'text', data: { delta: d.delta } } : null;
    case 'tool_call_start':
      if (typeof d.id !== 'string' || typeof d.name !== 'string') return null;
      return {
        event: 'tool_call_start',
        data: { id: d.id, name: d.name, args: (d.args && typeof d.args === 'object' ? d.args : {}) as Record<string, unknown> },
      };
    case 'tool_call_result':
      if (typeof d.id !== 'string') return null;
      return {
        event: 'tool_call_result',
        data: {
          id: d.id,
          summary: typeof d.summary === 'string' ? d.summary : '',
          is_error: d.is_error === true,
          result: typeof d.result === 'string' ? d.result : d.result == null ? undefined : JSON.stringify(d.result, null, 2),
        },
      };
    case 'done':
      return { event: 'done', data: { stop_reason: d.stop_reason as string | undefined, tool_calls: d.tool_calls as number | undefined } };
    case 'error':
      return { event: 'error', data: { message: typeof d.message === 'string' ? d.message : 'Unknown agent error' } };
    default:
      return null;
  }
}

/** POST and stream agent events. Resolves when the stream ends; rejects on network/HTTP failure or abort. */
export async function streamChat(
  url: string,
  body: unknown,
  onEvent: (e: import('./types').AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    let detail = '';
    try {
      detail = (await res.text()).slice(0, 300);
    } catch {
      /* ignore */
    }
    throw new HttpError(res.status, detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  const emit = (msgs: SseMessage[]) => {
    for (const m of msgs) {
      const e = toAgentEvent(m);
      if (e) onEvent(e);
    }
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    emit(parser.push(decoder.decode(value, { stream: true })));
  }
  emit(parser.push(decoder.decode()));
  emit(parser.finish());
}

export class HttpError extends Error {
  constructor(public status: number, public detail: string) {
    super(`Agent returned HTTP ${status}${detail ? `: ${detail}` : ''}`);
  }
}
