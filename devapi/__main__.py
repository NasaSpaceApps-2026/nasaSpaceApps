"""`python -m devapi [--host H] [--port P]`: serve the stand-in REST + WS API (default 127.0.0.1:8000).

Site: ATLAS_SITE_DIR (a built site directory) or the synthetic test site; see devapi.app.load_configured_site.
"""
import argparse

import uvicorn

from .app import create_app, load_configured_site


def main() -> None:
    """CLI entry point: serve the stand-in API with uvicorn (default 127.0.0.1:8000)."""
    ap = argparse.ArgumentParser(description="Ares Atlas dev REST API (stand-in for api/)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    site, synthetic = load_configured_site()
    uvicorn.run(create_app(site, synthetic=synthetic), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
