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

## Mounting it in the viewer later

The panel needs only a 380 px wide, full-height slot and the agent URL; it does not touch the viewer's state. Checklist:

1. Import `ChatPanel` and render it in the right-hand slot with `agentUrl` (default `http://localhost:8766`, start the
   backend with `stack/up.sh`). Make sure the parent has a definite height.
2. Pass `getContext` to send the viewer's rover pose, camera and selected marker in site-frame metres.
3. The viewer and the agent must share one site frame. The agent moves the rover through Yousef's API, so use the same
   `ARES_DATA_DIR` the viewer renders (see `docs/frames.md`); with `VITE_SITE_DIR=../contracts/fixtures` the viewer shows
   a synthetic 1 km site, not the real Jezero the query engine answers from.
4. Optional: `onToolEvent` to flash search hits or destinations. `chat-dev/src/highlights.ts` shows how to turn
   `find_*`, `teleport_rover`, `goto_location` and `plan_route` results into map points; `chat-dev/src/Minimap.tsx` is a working example.
5. If the viewer already has a WebSocket to the API, do not open a second one just for the chat; the agent has no
   viewer-side channel of its own.
