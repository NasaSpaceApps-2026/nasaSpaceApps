// Dependency-free mock of the agent's POST /chat SSE API (port 8766 by default).
// Usage: npm run mock   (PORT=8766 node mock-agent.mjs)
import http from 'node:http';

const PORT = Number(process.env.PORT ?? 8766);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Scripted session shaped exactly like the real agent's SSE events and tool names
// (see agent/loop.py and contracts/mcp-tools.md). Numbers are illustrative, not Mars data.
const overview = `[MOCK] Jezero site, 8192 m square, 2 m cells. Base at (2294, -907).
Layers: elevation, slope_2m, rough_5x5, quality_flag, carbonate (evidence).
Profiles: landing_pad, eva_walkable, rover_scout. Evidence tiers: not_detected < inferred_from_unit < weak_orbital < strong_orbital < confirmed_in_situ.`;
const regions = `[MOCK] 3 carbonate regions within 120 walk_time_min of base (set:1):
1. 1.1 at (812, 455): best tier strong_orbital (carbonate, CRISM on MRO), 6400 m2, 41 min walk.
2. 1.2 at (1203, 610): best tier weak_orbital, 2800 m2, 58 min walk.
3. 1.3 at (-640, 980): best tier inferred_from_unit, 9100 m2, 97 min walk.
Assumptions: walking speed curve is a placeholder; times approximate.`;

const t = (delta) => ['text', { delta }];

function frames(msgs) {
  const last = [...msgs].reverse().find((m) => m.role === 'user')?.content ?? '';
  if (/error|fail/i.test(last)) {
    return [
      ['tool_call_start', { id: 't1', name: 'plan_route', args: { start: 'marker:1', goal: 'marker:2', max_slope_deg: 15 } }],
      ['wait', 900],
      ['tool_call_result', { id: 't1', is_error: true, summary: '[MOCK] Goal is on a 31 deg slope; nearest reachable point is (402, 88).', result: '[MOCK] infeasible: goal on 31 deg slope' }],
      t('That route is not feasible: the goal sits on a 31° slope. The nearest reachable point is (402, 88). Want me to plan to that point instead?'),
      ['done', { stop_reason: 'end_turn', tool_calls: 1 }],
    ];
  }
  return [
    ['tool_call_start', { id: 't1', name: 'get_site_overview', args: {} }],
    ['wait', 700],
    ['tool_call_result', { id: 't1', is_error: false, summary: '[MOCK] Jezero site, 8192 m square; 5 layers incl. carbonate evidence.', result: overview }],
    ['tool_call_start', { id: 't2', name: 'find_regions', args: {
      conditions: [{ evidence: 'carbonate', min_tier: 'weak_orbital' }],
      within: { from: 'base', max: 120, metric: 'walk_time_min' }, objective: 'best_evidence:carbonate', limit: 3 } }],
    ['wait', 1400],
    ['tool_call_result', { id: 't2', is_error: false, summary: '[MOCK] 3 regions; best strong_orbital at (812, 455), 41 min walk.', result: regions }],
    ['tool_call_start', { id: 't3', name: 'fly_camera', args: { target: '1.1' } }],
    ['wait', 500],
    ['tool_call_result', { id: 't3', is_error: false, summary: '[MOCK] Camera flying to 1.1.', result: '[MOCK] ok' }],
    ...[
      'The strongest carbonate evidence within a 2-hour walk of base is ',
      '**region 1.1** at (812, 455), about **41 min** away.\n\n',
      '- Evidence: `strong_orbital` for carbonate (CRISM on MRO). This is an orbital detection, not rover-confirmed.\n',
      '- Runner-up: region 1.2, `weak_orbital`, 58 min walk.\n',
      '- Walk times use a placeholder speed curve and are approximate.\n\n',
      'I moved the camera to region 1.1.',
    ].map(t),
    ['done', { stop_reason: 'end_turn', tool_calls: 3 }],
  ];
}

http
  .createServer((req, res) => {
    const cors = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': '*', 'Access-Control-Allow-Headers': '*' };
    if (req.method === 'OPTIONS') return res.writeHead(204, cors).end();
    if (req.url === '/health') return res.writeHead(200, { ...cors, 'Content-Type': 'application/json' }).end('{"ok":true,"mock":true}');
    if (req.method !== 'POST' || req.url !== '/chat') return res.writeHead(404, cors).end();
    let body = '';
    req.on('data', (c) => (body += c));
    req.on('end', async () => {
      let msgs = [];
      try {
        const parsed = JSON.parse(body);
        msgs = parsed.messages ?? [];
        console.log('chat request, context:', JSON.stringify(parsed.context ?? null));
      } catch {
        return res.writeHead(400, cors).end('bad json');
      }
      res.writeHead(200, { ...cors, 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' });
      let closed = false;
      res.on('close', () => (closed = true));
      for (const [name, data] of frames(msgs)) {
        if (closed) return;
        if (name === 'wait') {
          await sleep(data);
          continue;
        }
        res.write(`event: ${name}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`);
        if (name === 'text') await sleep(120);
      }
      res.end();
    });
  })
  .listen(PORT, () => console.log(`mock agent on http://localhost:${PORT}  (say "error" to see a failed tool call)`));
