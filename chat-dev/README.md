# chat-dev

Standalone dev page for `web/src/panels/chat/` (the chat panel), so it can be built and demoed without Rithvik's app.

    npm install
    npm run mock    # scripted fake agent on :8766, no API key needed (say "error" for a failed tool call)
    npm run dev     # http://localhost:5199 (use ?agent=http://host:port for another backend)
    npm test        # vitest: SSE parser, reducer, formatters
    npm run build   # typecheck + production build

To use the real agent instead of the mock: `.venv/bin/python -m agent` (port 8766).
