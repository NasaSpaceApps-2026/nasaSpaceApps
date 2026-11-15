# chat-dev

Standalone dev page for `web/src/panels/chat/` (the chat panel), so it can be built and demoed without Rithvik's app.

    npm install
    npm run mock    # scripted fake agent on :8766, no API key needed (say "error" for a failed tool call)
    npm run dev     # http://localhost:5199 (use ?agent=http://host:port for another backend)
    npm test        # vitest: SSE parser, reducer, formatters
    npm run build   # typecheck + production build

The page is the standalone copilot UI: chat on the right; on the left the rover state (API WebSocket `?api=http://localhost:8000`),
a hillshade minimap with rover, trail, markers, places and the agent's latest results, and a tool-call log. It uses only our
APIs (`:8000/rover/state`, `/ws`, `/markers`, `:8766`), nothing from `web/` except the chat panel. The default agent is the
real one on :8766 (`stack/up.sh` starts everything, see `stack/README.md`); `npm run mock` is the offline stand-in on the same port.
The minimap image comes from `python -m devapi.render_minimap <site_dir> chat-dev/public/minimap` (done by `stack/up.sh`).
