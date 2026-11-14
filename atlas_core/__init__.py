"""Ares Atlas core: spatial query engine shared by mcp/ and api/. See contracts/mcp-tools.md."""
from .errors import AtlasError
from .site import Site, Layer, load_site, TIERS, TIER_NAMES, NO_COVERAGE
from .sets import SetStore, LocationSet
from .refs import resolve_ref

__all__ = ["AtlasError", "Site", "Layer", "load_site", "TIERS", "TIER_NAMES", "NO_COVERAGE",
           "SetStore", "LocationSet", "resolve_ref"]
