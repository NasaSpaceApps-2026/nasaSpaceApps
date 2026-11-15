# Deploy Ares Atlas to a Hugging Face Docker Space

One image runs everything (rover API, query engine, MCP server, DeepSeek agent, viewer). The public port is 7860: `vite preview`
serves the built viewer and proxies `/__engine`, `/__rover` (websocket) and `/__agent` (SSE) to the internal services.
The street-view sandbox (Drive tab) is built into the image and served statically at `/sandbox/`.

## Steps

1. Create the Space: huggingface.co -> New Space -> SDK **Docker** (blank template), hardware **CPU basic (free)**. Note `<user>/<space>`.
2. Space -> Settings -> **Variables and secrets** -> New secret `DEEPSEEK_API_KEY` = your (rotated) key.
   Optional variables: `ATLAS_CHAT_RATE_PER_MIN` (default 6 per visitor), `ATLAS_CHAT_DAILY_CAP` (default 500 across everyone; 0 = off).
3. Log in: `pip install -U huggingface_hub` then `hf auth login` (a token with write access).
4. Stage the Space repo (needs the caches from one `stack/up.sh --with-viewer` run; takes about 3 minutes, writes only to the staging dir):
   `deploy/hf/stage.sh /tmp/ares-space`
5. Upload: `hf upload <user>/<space> /tmp/ares-space --repo-type=space`
   (large files go through Xet/LFS automatically; the upload is a few hundred MB).
6. Watch the **Logs** tab: the image builds (about 5-8 min), then `Ares Atlas is up on :7860`. Open the Space URL.

## Notes

- The key is read from the environment at runtime only; it is not in the image or the staged files (stage.sh scans for key-like strings).
- Writable state lives in `/tmp` (rover state, no replay recording). It resets on restart.
- Free Spaces sleep after inactivity; the first visit wakes them (about a minute for the services to load the 4096 x 4096 site).
- Update after a code change: re-run stage.sh and `hf upload` again.
- Local test: `docker build -t ares-space /tmp/ares-space && docker run --rm -u 1000 -p 7860:7860 -e DEEPSEEK_API_KEY=... ares-space`.
