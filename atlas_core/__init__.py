"""Ares Atlas core: the spatial query engine behind the MCP tools and the devapi REST stand-in.

Architecture & module map
    viewer/chat -> agent/ (Claude tool loop) -> mcp/ (tools, text formatting) -> REST (devapi query engine, or
    Yousef's rover API for rover/route/marker calls) -> atlas_core -> site data (data/build/site/<name>/).

    site.py, sets.py, refs.py   data model: Site/Layer grids (site frame x east / y north, metres; row 0 = north),
                                location sets (set:N handles), location references -> members
    errors.py                   AtlasError(message, hint): the only exception that becomes a readable tool result
    catalog.py, describe.py     site overview, layer catalogue, describe_location
    conditions.py, search.py    condition language -> masks; find_locations / find_regions
    travel.py, route.py         MCP_Geometric least-cost walking time/distance, nearest/within, route planning
    visibility.py               viewshed (Franklin & Ray 1994 radial sweep), line of sight, vantage points
    sun.py, illumination.py     Mars24 sun position (Allison & McEwen 2000), shadows and lit windows
    lake.py, envelope.py        paleolake flooding, EVA safety envelope
    evidence.py                 evidence-tier provenance and registration caveats
    terrain.py                  Horn 1981 slope, Zevenbergen & Thorne 1987 curvature, Weiss 2001 TPI, Riley 1999 TRI
    testing.py                  synthetic site with known ground truth (never shown as Mars data)

Every result is a plain dict (JSON-safe after devapi.util.clean); failures raise AtlasError with a hint.
Spec: contracts/mcp-tools.md.
"""
from .errors import AtlasError
from .site import Site, Layer, load_site, TIERS, TIER_NAMES, NO_COVERAGE
from .sets import SetStore, LocationSet
from .refs import resolve_ref

__all__ = ["AtlasError", "Site", "Layer", "load_site", "TIERS", "TIER_NAMES", "NO_COVERAGE",
           "SetStore", "LocationSet", "resolve_ref"]
