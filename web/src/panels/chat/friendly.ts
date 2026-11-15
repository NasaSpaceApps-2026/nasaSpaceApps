// Friendly error states: map the raw error text the agent (or the network) gives us to a sentence plus the exact fix.
export interface Friendly {
  title: string;
  fix: string;
  /** Raw message, shown small so nothing is hidden. */
  detail?: string;
}

const RESTART = 'stack/down.sh && stack/up.sh --with-viewer';

export function friendlyError(message: string): Friendly {
  const m = message || '';
  if (/cannot reach the agent|failed to fetch|networkerror|load failed/i.test(m))
    return { title: 'The copilot agent is not running.', fix: 'Start everything with: stack/up.sh --with-viewer (or just the agent: .venv/bin/python -m agent). Logs: stack/logs/agent.log.', detail: m };
  if (/tool server unavailable|could not connect to the mcp|mcp server/i.test(m))
    return { title: 'The tool server (MCP, port 8765) is down.', fix: `Restart the stack: ${RESTART}. Logs: stack/logs/mcp.log.`, detail: m };
  if (/api[_ ]key|credentials|no model|not configured|authentication|rejected the credentials/i.test(m))
    return { title: 'The model key is missing or was rejected.', fix: `Put DEEPSEEK_API_KEY=... (or ANTHROPIC_API_KEY=...) in the .env file at the repo root, then restart: ${RESTART}.`, detail: m };
  if (/insufficient balance|402/.test(m))
    return { title: 'The model account is out of credit.', fix: 'Top up the DeepSeek (or Anthropic) account, or switch provider in .env, then ask again.', detail: m };
  if (/rate limit|429/i.test(m))
    return { title: 'The model is rate limiting us.', fix: 'Wait about a minute and send the question again.', detail: m };
  if (/too many model round-trips|cut off/i.test(m))
    return { title: 'That question needed too many steps.', fix: 'Ask for one thing at a time, for example the search first, then the route.', detail: m };
  return { title: 'Something went wrong.', fix: 'Try again. If it keeps happening, check stack/logs/agent.log.', detail: m };
}

export interface AgentHealth {
  ok: boolean;
  provider?: string;
  model?: string;
  api_key_present?: boolean;
  mcp_connected?: boolean;
  tools?: string[];
  replay?: string | null;
}

/** One-line status for the header, plus the banner (if any) that explains what to do before the first question. */
export function describeHealth(h: AgentHealth | null, down: boolean): { label: string; level: 'ok' | 'warn' | 'down' | 'checking'; banner?: Friendly } {
  if (down) return { label: 'agent offline', level: 'down', banner: friendlyError('Cannot reach the agent') };
  if (!h) return { label: 'connecting…', level: 'checking' };
  const model = [h.provider, h.model].filter(Boolean).join(' · ') || 'model';
  if (h.replay) return { label: `${model} · replay`, level: 'ok' };
  if (h.api_key_present === false)
    return { label: `${model} · no key`, level: 'warn', banner: friendlyError('No API key: the model credentials are not configured') };
  return { label: model, level: 'ok' };
}
