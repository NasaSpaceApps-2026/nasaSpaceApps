import { useEffect, useState } from 'react';
import type { AgentHealth } from './friendly';

/** Polls GET {agentUrl}/health (every 8 s while visible). `down` = the agent did not answer. */
export function useAgentHealth(agentUrl: string, enabled = true): { health: AgentHealth | null; down: boolean } {
  const [state, setState] = useState<{ health: AgentHealth | null; down: boolean }>({ health: null, down: false });
  useEffect(() => {
    if (!enabled) return;
    let dead = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const base = agentUrl.replace(/\/+$/, '');
    const tick = async () => {
      let next: { health: AgentHealth | null; down: boolean };
      try {
        const r = await fetch(`${base}/health`, { signal: AbortSignal.timeout(4000) });
        next = r.ok ? { health: (await r.json()) as AgentHealth, down: false } : { health: null, down: true };
      } catch {
        next = { health: null, down: true };
      }
      if (dead) return;
      setState((p) => (p.down === next.down && JSON.stringify(p.health) === JSON.stringify(next.health) ? p : next));
      timer = setTimeout(() => void tick(), document.hidden ? 20000 : 8000);
    };
    void tick();
    return () => {
      dead = true;
      if (timer) clearTimeout(timer);
    };
  }, [agentUrl, enabled]);
  return state;
}
