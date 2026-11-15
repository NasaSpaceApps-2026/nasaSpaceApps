# Chat panel (Ares copilot)

Self-contained React + TypeScript panel. Only dependency: `react` (>=18). It talks to the agent backend (`POST /chat`, SSE, default `http://localhost:8766`) and shows every tool call the AI makes as a collapsible card.

## Mount it (380 px right-hand slot)

```tsx
import { ChatPanel, type ToolEvent } from './panels/chat';

<div style={{ width: 380, height: '100%' }}>
  <ChatPanel
    agentUrl="http://localhost:8766"
    getContext={() => {
      const s = useStore.getState();           // read the store at send time, do not subscribe
      return { rover: s.roverPose, camera: s.camera, selected_marker: s.selectedMarker };
    }}
    onToolEvent={(e: ToolEvent) => { /* react to the agent, see below */ }}
  />
</div>
```

The panel is `width: 380px; height: 100%`, so give its parent a definite height. CSS ships as `chat.css` (imported by `ChatPanel.tsx`; your bundler must handle CSS imports, Vite does). All rules and variables are scoped under `.ares-chat`; nothing global is touched. Theme by overriding `--ares-*` variables on `.ares-chat`.

## Props

| Prop | Type | Purpose |
|---|---|---|
| `agentUrl?` | `string` | Agent base URL. Default `http://localhost:8766`. |
| `getContext?` | `() => Record<string, unknown> \| undefined` | Called once per message; result is sent as `context` (rover pose, camera, selected marker; site-frame metres). The backend also fetches rover pose and markers from the REST API itself, so this is for client-only state. |
| `onToolEvent?` | `(e: ToolEvent) => void` | Fired on `start` (args known) and on `result` (summary, `isError`, full `result` text). Errors thrown by your handler are caught. |
| `suggestedPrompts?` | `string[]` | Overrides the demo prompts shown on an empty conversation. |

`ToolEvent = { phase: 'start' | 'result'; id; name; args; summary?; isError?; result? }`

## Events worth wiring

The agent already drives the viewer through the REST API/WebSocket (teleport, camera, markers). Use `onToolEvent` only for UI niceties on top:

- `find_sites` / `find_locations` result: highlight or pin the returned sites.
- `fly_camera`, `set_view`, `teleport_rover`: optionally flash the affected map area.
- `plan_route`, `plan_science_traverse`: show the route layer.
- Any `isError: true`: nothing required, the card already shows it.

## Also exported

`useChat` (headless hook, if you want your own UI), `SseParser` / `streamChat`, `summarizeArgs`, `DEMO_PROMPTS`.

## Notes

- Messages sent to the backend are plain `{role, content}` text; tool cards are not replayed.
- Requires CORS on the agent (already enabled with `*`).
- Tests live next to the source (`*.test.ts`); run them from `chat-dev/` with `npm test`. Dev page: `cd chat-dev && npm run mock` (terminal 1), `npm run dev` (terminal 2), open http://localhost:5199.

## Mounted in the viewer

Done: `AtlasDock.tsx` wraps `ChatPanel` for the Cesium viewer (highlights, camera flights, rover follow, street-view mirroring, frame
check). What changed in other people's files, and how to revert: `web/INTEGRATION.md`. Run: `stack/up.sh --with-viewer`.

Extra panel behaviour: header shows `provider · model` and agent status from `GET /health` (polled), friendly error cards with the exact
fix (`friendly.ts`), text deltas are rendered once per animation frame, long tool results are collapsed and truncated with "Show all",
Enter sends, Shift+Enter adds a line, Esc stops, focus returns to the box after a send. The agent does not forward MCP
`structuredContent`; `features.ts` parses the result text instead and prefers `structured` when present.
