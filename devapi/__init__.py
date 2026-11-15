"""devapi: in-repo stand-in for the REST + WS API (api/, owned by Yousef), built on atlas_core.
Run: python -m devapi --port 8000. See devapi/README.md."""
from .app import create_app

__all__ = ["create_app"]
