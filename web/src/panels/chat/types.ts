/** Events streamed by the agent backend (POST /chat, SSE). */
export type AgentEvent =
  | { event: 'text'; data: { delta: string } }
  | { event: 'tool_call_start'; data: { id: string; name: string; args: Record<string, unknown> } }
  | { event: 'tool_call_result'; data: { id: string; summary: string; is_error: boolean; result?: string } }
  | { event: 'done'; data: { stop_reason?: string; tool_calls?: number } }
  | { event: 'error'; data: { message: string } };

export type ToolCallStatus = 'running' | 'ok' | 'error';

export interface ToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
  status: ToolCallStatus;
  summary?: string;
  result?: string;
}

/** An assistant turn is an ordered list of text and tool-call parts, as they arrived. */
export type Part = { kind: 'text'; text: string } | { kind: 'tool'; call: ToolCall };

export type ChatMessage =
  | { id: string; role: 'user'; text: string }
  | { id: string; role: 'assistant'; parts: Part[]; error?: string; stopped?: boolean };

export interface ChatState {
  messages: ChatMessage[];
  streaming: boolean;
}

/** What the host app is told whenever the agent acts. */
export interface ToolEvent {
  phase: 'start' | 'result';
  id: string;
  name: string;
  args: Record<string, unknown>;
  summary?: string;
  isError?: boolean;
  result?: string;
}
