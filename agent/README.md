# Ares Atlas agent backend

FastAPI service (port 8766) that runs Claude's tool-calling loop against the Atlas MCP server and streams
text and tool events as Server-Sent Events. Spec: `docs/scope-c-d.md` (Sameer: tasks 4, 5, 7, 8).

## Quick start

`stack/up.sh` runs the whole chain (API :8000, devapi :8001, MCP :8765, this agent :8766, chat page :5199); the provider
and key come from the git-ignored `.env`. See `stack/README.md`. The manual steps are below.

## Run

```bash
export ANTHROPIC_API_KEY=...                      # not needed for replay
.venv/bin/python mcp/server.py --http &           # MCP server on :8765/mcp  (or use ATLAS_MCP_STDIO=1)
.venv/bin/python -m agent                         # agent on :8766
curl -N -X POST localhost:8766/chat -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"What layers exist?"}],"context":{"camera":{"x":0,"y":0,"z":800}}}'
```

The agent starts even if the MCP server or REST API is down: the MCP connection is made lazily on the
first chat and re-made if it drops; missing REST context is reported to the model as UNAVAILABLE.

## Endpoints and SSE events

`GET /health` -> `{ok, model, api_key_present, mcp_connected, tools, mcp_target, rest_api, replay, max_tool_calls}`.

`POST /chat` has two modes. **Stateful (the panel):** `{message, session_id?, context?, history?}`; the agent keeps the
full transcript per session (tool calls and results included, `agent/conversation.py`: in-memory LRU, 6 h TTL, old
tool results shortened and oldest turns dropped over a ~90k-char budget with tool pairs kept whole) and the first SSE
event is `session {session_id, resumed}`. `history` (plain text turns) only seeds a session the server does not know
(restart). The context block carries "conversation memory" handles (last set and its top items, last route, last place,
recent calls) so follow-ups still resolve after trimming. **Stateless (legacy):** `{messages: [{role, content}], context?}`
(messages must start and end with `user`). Response is `text/event-stream`:

| event | data |
|---|---|
| `text` | `{delta}` streamed model text |
| `tool_call_start` | `{id, name, args}` |
| `tool_call_result` | `{id, summary, is_error, result}`; `summary` is a short line for the card, `result` the full text the model saw |
| `done` | `{stop_reason, tool_calls}` |
| `error` | `{message}` (no key, tool server down, API error, bad request) |

## How a turn works

1. Tools are discovered from the MCP server on every turn (`tools/list`) and converted to Claude tool
   definitions (`eager_input_streaming` on).
2. Context is fetched (`GET /rover/state`, `GET /markers` on `ATLAS_API_URL`, 1.5 s timeout, failures tolerated)
   and merged with the client's `context` into an `<atlas_context>` block. It is its own system block after the
   stable system prompt (`system_prompt.md`, cached), never inside the user's message.
3. Streaming Messages call: `claude-opus-5-5`, adaptive thinking, `output_config.effort` (default `medium`),
   server-side refusal fallbacks (beta `server-side-fallback-2026-07-01`; retried without if the API rejects them).
4. For each `tool_use` block: arguments are checked against the tool's required fields, the tool runs over
   MCP (concurrently when the model made several calls), and all results go back in ONE user message.
   Tool results are the MCP text content, plus the structured content when it is under 800 chars; results
   over 12k chars are truncated with a note. Tool errors (`isError`, timeouts, unknown tools, bad args) go back
   with `is_error: true`.
5. At most 12 tool calls per user turn (`ATLAS_MAX_TOOL_CALLS`). When the cap is hit, excess calls get an error
   result, the next model call is made with `tool_choice: none` plus a note to answer with what it has.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `ATLAS_PROVIDER` | auto | `anthropic` or `deepseek`. Default: anthropic if `ANTHROPIC_API_KEY` is set, else deepseek if `DEEPSEEK_API_KEY` is set |
| `ANTHROPIC_API_KEY` | - | required for provider anthropic (except in replay mode) |
| `DEEPSEEK_API_KEY` | - | required for provider deepseek; may live in the git-ignored repo-root `.env` (`KEY = value`, loaded without overriding the environment; never logged or recorded) |
| `ATLAS_DEEPSEEK_THINKING` | `0` | `1` enables DeepSeek thinking mode (reasoning is passed back each turn, as the API requires with tools) |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | OpenAI-compatible endpoint |
| `ATLAS_MODEL` | `claude-opus-5-5` / `deepseek-chat` | model id per provider (Anthropic: use a 4.6+ model; `deepseek-chat` is currently an alias of `deepseek-flash`) |
| `ATLAS_EFFORT` | `medium` | `low`..`max`; empty string omits it |
| `ATLAS_DEEPSEEK_TEMPERATURE` | `0.3` | DeepSeek sampling temperature (`none` = API default); low keeps tool use and number quoting steady |
| `ATLAS_SYSTEM_PROMPT` | | path to an alternative system prompt (A/B runs) |
| `ATLAS_MAX_TOKENS` | `32000` | per model call |
| `ATLAS_FALLBACKS` | `0` | opt-in server-side refusal fallbacks (beta; unverified, off by default) |
| `ATLAS_MCP_URL` | `http://localhost:8765/mcp` | streamable HTTP MCP endpoint |
| `ATLAS_MCP_STDIO` | `0` | `1`: spawn `mcp/server.py` over stdio instead (inherits env, e.g. `ATLAS_SITE_DIR`) |
| `ATLAS_API_URL` | `http://localhost:8000` | REST API for context |
| `ATLAS_MAX_TOOL_CALLS` | `12` | cap per user turn |
| `ATLAS_TOOL_TIMEOUT` | `180` | seconds per tool call (driving tools block) |
| `ATLAS_AGENT_HOST` / `ATLAS_AGENT_PORT` | `127.0.0.1` / `8766` | bind address |
| `ATLAS_RECORD` | `1` | record sessions |
| `ATLAS_REPLAY_DIR` | `agent/replays` | where recordings go |
| `ATLAS_REPLAY` | - | file to replay (see below) |
| `ATLAS_REPLAY_REPLAY_TOOLS` | `0` | re-issue recorded tool calls during replay |
| `ATLAS_REPLAY_SPEED` | `1` | `2` = twice as fast, `0` = no delays |
| `ATLAS_REPLAY_MAX_GAP` | `3` | cap on any recorded pause, seconds |

## Record and replay

Every `/chat` is recorded to `agent/replays/<timestamp>.jsonl`: first a `meta` line (prompt, context block,
model, tool names), then one line per SSE event: `{"t": seconds_since_start, "event", "data"}`.

```bash
ATLAS_REPLAY=agent/replays/20261114T101500.jsonl .venv/bin/python -m agent   # no API key, no MCP needed
```

Every `POST /chat` then streams that file's events with the original timing (the request body is ignored).
Replay only streams events; it does not touch the viewer or rover. To re-trigger the viewer side effects
(teleports, camera flights, markers), also set `ATLAS_REPLAY_REPLAY_TOOLS=1`: each recorded `tool_call_start`
is re-issued as a real MCP tool call (needs the MCP server and REST API up); its result is discarded and the
recorded result is streamed. Failures of those calls are ignored. Replays are not re-recorded.

## Evals

`agent/evals/questions.yaml`: the 6 demo prompts from the scope doc and the 10 questions from
`contracts/mcp-tools.md` section 6, each with an expected tool path (required groups with alternatives,
ordering, optional allow/forbid lists) and reply checks. Scoring (`agent/evals/scoring.py`): tool choice, reply
checks, and **every measurement-like number in the reply must appear in a tool result of the same session**
(rounding and truncation tolerated, km/m, h/min and % conversions understood; identifiers, dates, list markers
and bare counts of 12 or less ignored; numbers quoted from the user's prompt or the app-state block count as
grounded). Cases whose required tools the MCP server does not offer yet are SKIP, not FAIL (`--strict` makes
skips fail).

```bash
.venv/bin/python -m agent.evals.run_evals                         # live, needs ANTHROPIC_API_KEY; records to agent/replays/evals/
.venv/bin/python -m agent.evals.run_evals --only demo_scout q06_slope_layer_limits
.venv/bin/python -m agent.evals.run_evals --replay agent/replays  # score recorded sessions, no key
```

Exit code 0 pass, 1 failure, 2 cannot run. Run it after every prompt or tool-description change.

## Tests

`.venv/bin/python -m pytest -q tests/test_agent_loop.py tests/test_agent_evals.py` runs with no key: the loop is
driven by `agent/testing.py::ScriptedModel` against the real MCP server over stdio (synthetic site).

## DeepSeek provider

`agent/model.py::DeepSeekModel` speaks the OpenAI-compatible Chat Completions API over httpx (no extra dependency). The loop keeps Anthropic-format messages; conversion happens at the API boundary (system blocks -> system message, `tool_use` -> `tool_calls`, `tool_result` -> `role: tool`, MCP tool defs -> function tools). Streamed tool-call fragments are assembled by index; malformed arguments become a non-object `input` that the loop reports back to the model as an error. 429/5xx are retried with backoff. Run live evals with `ATLAS_PROVIDER=deepseek`; results for the first run are in `docs/eval-run-deepseek.md`.

## Architecture & module map

```
viewer / chat -> agent/ (this backend) -> MCP server (mcp/) -> REST (devapi or Yousef's rover API) -> atlas_core -> site data
```

| File | Role |
|------|------|
| `app.py` | FastAPI app: `POST /chat` (SSE), `GET /health` |
| `session.py` | One recorded session: context fetch, system blocks, tool loop, recording (shared with the eval runner) |
| `loop.py` | `run_turn`: model text, tool calls through the bridge, SSE-ready events |
| `model.py` | Model clients (Anthropic, DeepSeek) behind the `ModelClient.stream_turn` protocol |
| `mcp_bridge.py` | Lazy MCP client: connect, list tools, call tools |
| `context.py` | Per-turn rover and marker context block (data, not instructions) |
| `replay.py`, `testing.py` | Session recording/replay; `ScriptedModel` fake for tests |
| `config.py` | Environment-driven `Settings` |
| `evals/` | Eval cases (`questions.yaml`), scoring and runner |
