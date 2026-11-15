"""Gazetteer: turn everyday place words ("the crater", "seitah", "landing site", "the hill") into coordinates.

Sources, all inside the site frame (x east, y north, metres) and each entry says how its position was obtained:
  * POIs of the site (pois.geojson): landing site, base, sample sites, sampling areas ("Delta Front", "South Seitah").
  * Official IAU/USGS names that fall inside the site (names.geojson, centre points of the IAU gazetteer).
  * Craters detected from the elevation layer: closed depressions (priority-flood fill, skimage) that are round
    enough and deep enough relative to their width. Named when an IAU crater centre lies within the depression.
  * Curated aliases of the entries above ("the delta" -> Delta Front, "landing site" -> the landing POI). Names with
    no position in our data (Kodiak, Three Forks, Santa Cruz, Hawksbill Gap, Maaz) are mapped to the closest named
    area and flagged `approximate`; no position is invented.
  * Generic terms resolved from the terrain relative to a reference point (usually the rover): "the crater" (nearest
    detected crater, its near rim), "the hill / high ground" (highest ground nearby with a gentle slope), "flat
    ground" (nearest flat open spot).

`Gazetteer.resolve(text, near)` never dead-ends: it returns the best pick (with a plain-language explanation) and the
runner-up candidates with distances, or, when nothing matches, the nearest named places as candidates.
"""
from __future__ import annotations

import difflib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .site import Site
from .terrain import slope_deg

REPO = Path(__file__).resolve().parent.parent
NAMES_PATHS = ("names.geojson",)   # inside the site dir
NAMES_FALLBACKS = (REPO / "data/build/data/names.geojson", REPO / "stack/cache/work/data/build/data/names.geojson")

COARSE_M = 8.0                 # working resolution for crater / hill / flat searches
CRATER_MIN_DEPTH_M = 3.0
CRATER_MIN_DIAM_M = 60.0
CRATER_MIN_DEPTH_RATIO = 0.02  # depth / diameter; shallower closed depressions are swales, not craters
CRATER_MAX_AXIS_RATIO = 2.0
CRATER_MIN_SOLIDITY = 0.8
HILL_RADIUS_M = 1000.0
HILL_MAX_SLOPE_DEG = 20.0
FLAT_MAX_SLOPE_DEG = 4.0
FLAT_MIN_DIST_M = 60.0
RIM_MAX_SLOPE_DEG = 12.0   # a rim point to send a rover to must be on gentle ground

STOP = {"the", "a", "an", "to", "of", "at", "near", "nearest", "closest", "nearby", "that", "this", "my", "me", "please",
        "over", "there", "toward", "towards", "into", "onto", "on", "in", "up", "go", "drive", "take", "some", "any"}
CRATER_WORDS = {"crater", "craters", "bowl", "depression", "impact"}
SIZE_BIG = {"big", "biggest", "large", "largest", "huge", "bigger"}
SIZE_SMALL = {"small", "smallest", "little", "tiny"}
FLOOR_WORDS = {"floor", "bottom", "inside", "centre", "center", "middle"}
HIGH_WORDS = {"hill", "hills", "mountain", "mountains", "mount", "mons", "peak", "summit", "top", "high", "highest",
              "ground", "ridge", "butte", "mesa", "hilltop", "height", "heights", "tallest", "higher", "elevated"}
FLAT_WORDS = {"flat", "flattest", "level", "smooth", "flatter", "pad", "plain", "plains", "even"}
FLAT_FILLER = {"ground", "spot", "area", "place", "terrain", "patch", "surface", "land", "open", "safe", "site"}
HIGH_FILLER = {"spot", "area", "place", "point"}

# (alias text, ...) -> POI id. Applied only when the POI exists in the site.
POI_ALIASES: dict[str, list[str]] = {
    "octavia_e_butler_landing": ["landing site", "landing", "landing zone", "touchdown", "octavia butler",
                                 "octavia e butler", "octavia", "perseverance landing", "jezero", "where we landed",
                                 "starting point", "start"],
    "base": ["base", "home base", "mission base", "camp", "home", "habitat"],
    "region_south_seitah": ["seitah", "south seitah", "seitah region", "seitah area", "seitah unit", "seitah formation"],
    "region_delta_front": ["delta", "delta front", "jezero delta", "delta fan", "fan front", "delta scarp"],
    "region_artuby_ridge": ["artuby", "artuby ridge"],
    "region_margin_unit": ["margin", "margin unit", "crater margin"],
}
# Names the viewer's audience knows but our data has no position for -> (POI id, note). Flagged approximate.
APPROX_ALIASES: dict[str, tuple[str, list[str]]] = {
    "region_delta_front": ("Delta Front", ["kodiak", "kodiak butte", "three forks", "three forks depot"]),
    "region_south_seitah": ("South Séítah", ["maaz", "maaz formation", "santa cruz", "hawksbill gap", "hawksbill"]),
}


GLOBAL_PATH = Path(__file__).resolve().parent / "data" / "mars_global_names.json"
MARS_R_M = 3396190.0          # the sphere of our site frame and of the USGS/IAU Mars 2000 coordinates
TYPE_WORDS = {"mons": ["mons", "mount", "mt", "mountain", "volcano", "montes"], "vallis": ["vallis", "valley"],
              "planitia": ["planitia", "plain", "plains"], "crater": ["crater"], "chasma": ["chasma", "canyon"]}
TYPE_SYN = {w: k for k, ws in TYPE_WORDS.items() for w in ws}
GLOBAL_ALIASES = {   # everyday names -> IAU name
    "Aeolis Mons": ["mount sharp", "mt sharp", "sharp", "aeolis mons"],
    "Gale": ["gale crater", "gale"],
    "Valles Marineris": ["grand canyon", "grand canyon of mars", "marineris", "valles marineris"],
    "Olympus Mons": ["olympus", "mount olympus"],
    "Elysium Mons": ["elysium"], "Arsia Mons": ["arsia"], "Pavonis Mons": ["pavonis"], "Ascraeus Mons": ["ascraeus"],
    "Hellas Planitia": ["hellas", "hellas basin"], "Utopia Planitia": ["utopia"], "Isidis Planitia": ["isidis"],
}
FAMOUS = {"Olympus Mons": 0.10, "Valles Marineris": 0.10, "Aeolis Mons": 0.06, "Gale": 0.05, "Hellas Planitia": 0.06,
          "Utopia Planitia": 0.05, "Elysium Mons": 0.04, "Jezero": 0.04, "Arsia Mons": 0.02, "Pavonis Mons": 0.02,
          "Ascraeus Mons": 0.02}
# Street-view worlds of the viewer (web/src/bubbles.ts BUBBLES): global name -> bubble title shown under Drive.
STREET_WORLDS = {"Olympus Mons": "Olympus Mons scarp", "Jezero": "Jezero fan"}


def great_circle_m(lat1: float, lon1: float, lat2: float, lon2: float, radius_m: float = MARS_R_M) -> float:
    """Haversine distance on a sphere (default the 3396190 m Mars sphere of our site frame)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * radius_m * math.asin(min(1.0, math.sqrt(a)))


def norm(s: str) -> str:
    """Lower case, strip accents and punctuation: 'Séítah' -> 'seitah'."""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def tokens(s: str) -> list[str]:
    return [t for t in norm(s).split() if t not in STOP]


def compass(dx: float, dy: float) -> str:
    """8-point compass word for a displacement (x east, y north)."""
    names = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]
    return names[int(((math.degrees(math.atan2(dx, dy)) % 360) + 22.5) // 45) % 8]


def bearing(dx: float, dy: float) -> float:
    return math.degrees(math.atan2(dx, dy)) % 360.0


def dist_words(m: float) -> str:
    """'about 180 m' / 'about 1.4 km' (rounded, for plain-language explanations)."""
    if m < 100:
        return f"about {int(round(m / 5.0) * 5)} m" if m >= 10 else f"about {int(round(m))} m"
    if m < 950:
        return f"about {int(round(m / 10.0) * 10)} m"
    return f"about {m / 1000.0:.1f} km"


@dataclass
class Place:
    """One gazetteer entry; `names` are the normalised spellings that match it."""
    id: str
    name: str
    kind: str                      # poi | crater | iau | alias
    x: float
    y: float
    names: list[str] = field(default_factory=list)
    method: str = ""
    approximate: bool = False
    note: str | None = None
    poi_id: str | None = None
    ptype: str | None = None
    diameter_m: float | None = None
    depth_m: float | None = None

    def ref(self) -> str:
        return f"poi:{self.poi_id}" if self.poi_id else f"{self.x:.1f},{self.y:.1f}"


# ---------------------------------------------------------------------------------------------------------------
# crater detection
# ---------------------------------------------------------------------------------------------------------------

def _coarse(site: Site) -> tuple[np.ndarray, float, int]:
    """Block-mean elevation at about COARSE_M resolution -> (grid, cell metres, factor). NaN where any fine cell is NaN."""
    z = site.layers["elevation"].data
    f = max(1, int(round(COARSE_M / site.res_m)))
    n = z.shape[0] // f
    zb = z[: n * f, : n * f].reshape(n, f, n, f).astype(np.float64).mean(axis=(1, 3))
    return zb, site.res_m * f, f


def _cell_xy(site: Site, res: float, r, c):
    half = site.half_m
    return -half + (np.asarray(c, float) + 0.5) * res, half - (np.asarray(r, float) + 0.5) * res


def detect_craters(site: Site) -> list[dict]:
    """Craters as closed, round, deep-enough depressions of the elevation layer.

    Method: block-mean the DEM to ~8 m, fill every closed depression (morphological reconstruction by erosion seeded at
    the grid border, so a bowl on a tilted plain fills only up to its lowest rim point), depth = filled - DEM. Connected
    areas deeper than 3 m become candidates; kept when the equivalent diameter is at least 60 m, depth/diameter at least
    0.02, the area is compact (solidity >= 0.8, axis ratio <= 2) and no NaN touches it. Returns dicts sorted by diameter,
    largest first: id, x, y (centroid of the depression), diameter_m, depth_m, area_m2, floor_z, rim_z.
    """
    from scipy import ndimage as ndi
    from skimage.measure import regionprops
    from skimage.morphology import reconstruction

    zb, res, _ = _coarse(site)
    bad = ~np.isfinite(zb)
    hi = float(np.nanmax(zb)) if (~bad).any() else 0.0
    z = np.where(bad, hi, zb)               # unknown cells act as walls: they never fill, so they never create a bowl
    seed = z.copy()
    seed[1:-1, 1:-1] = hi + 1.0
    filled = reconstruction(seed, z, method="erosion")
    depth = filled - z
    depth[bad] = 0.0
    lab, n = ndi.label(depth > CRATER_MIN_DEPTH_M)
    near_bad = ndi.binary_dilation(bad, iterations=2) if bad.any() else bad
    out: list[dict] = []
    for p in regionprops(lab):
        area_m2 = p.area * res * res
        diam = 2.0 * math.sqrt(area_m2 / math.pi)
        mask = lab[p.slice] == p.label
        dmax = float(depth[p.slice][mask].max())
        if diam < CRATER_MIN_DIAM_M or dmax < CRATER_MIN_DEPTH_RATIO * diam:
            continue
        minor = max(p.axis_minor_length, 1e-6)
        if p.axis_major_length / minor > CRATER_MAX_AXIS_RATIO or p.solidity < CRATER_MIN_SOLIDITY:
            continue
        if near_bad[p.slice][mask].any():
            continue
        r, c = p.centroid
        x, y = _cell_xy(site, res, r, c)
        floor = float(z[p.slice][mask].min())
        out.append({"x": round(float(x), 1), "y": round(float(y), 1), "diameter_m": round(diam, 0),
                    "depth_m": round(dmax, 1), "area_m2": float(round(area_m2, 0)), "floor_z": round(floor, 1),
                    "rim_z": round(floor + dmax, 1)})
    out.sort(key=lambda d: -d["diameter_m"])
    for i, d in enumerate(out, 1):
        d["id"] = f"crater_{i}"
    return out


def _load_names(site_dir: str | Path | None) -> list[dict]:
    paths = ([Path(site_dir) / n for n in NAMES_PATHS] if site_dir else []) + list(NAMES_FALLBACKS)
    for p in paths:
        if p.exists():
            try:
                fc = json.loads(p.read_text())
                return [dict(f["properties"]) for f in fc.get("features", [])]
            except (OSError, ValueError, KeyError):
                continue
    return []


def friendly_name(name: str) -> str:
    """Display name: 'Sample 8: Ha'ahoni (aka "Hahonih")' -> "Ha'ahoni (sample 8)"; drops '(sampling area)' and '(assumed)'."""
    n = str(name)
    m = re.match(r"\s*Sample\s+(\d+)\s*:\s*(.*)", n, flags=re.I)
    num = None
    if m:
        num, n = m.group(1), m.group(2)
    n = re.sub(r"\((?:sampling area|assumed)\)", "", n, flags=re.I)
    n = re.sub(r"\s*\(aka [^)]*\)", "", n).strip()
    return f"{n} (sample {num})" if num else n


def _poi_names(p: dict) -> list[str]:
    """Spellings for a POI: its name, name without 'Sample N:' / '(sampling area)', quoted 'aka' names, the id, 'sample N'."""
    name = str(p.get("name") or "")
    out = {norm(name), norm(str(p.get("id") or "").replace("_", " "))}
    core = re.sub(r"\(sampling area\)", "", name, flags=re.I)
    m = re.match(r"\s*Sample\s+(\d+)\s*:\s*(.*)", core, flags=re.I)
    if m:
        out.add(f"sample {m.group(1)}")
        core = m.group(2)
    for aka in re.findall(r'aka\s+"?([^")]+)"?', core):
        out.add(norm(aka))
    core = re.sub(r"\(.*?\)", "", core)
    out.add(norm(core))
    return [o for o in out if o]


class Gazetteer:
    """Named places of a site plus generic-term resolution; build once per site (craters are detected lazily)."""

    def __init__(self, site: Site, site_dir: str | Path | None = None, names: list[dict] | None = None):
        self.site = site
        self._names_src = names if names is not None else _load_names(site_dir)
        self._craters: list[dict] | None = None
        self._coarse_cache: dict = {}
        self.entries: list[Place] = []
        self._build_named()

    # ---- construction ---------------------------------------------------------------------------
    def _build_named(self) -> None:
        site = self.site
        by_poi: dict[str, Place] = {}
        for p in site.pois:
            if p.get("x") is None or p.get("y") is None or not site.in_bounds(p["x"], p["y"]):
                continue
            e = Place(id=f"poi:{p['id']}", name=friendly_name(p.get("name") or p["id"]), kind="poi", x=float(p["x"]),
                      y=float(p["y"]), names=_poi_names(p), poi_id=str(p["id"]), ptype=p.get("type"),
                      method="site point of interest (pois.geojson)" + (
                          "; published coordinate" if p.get("source_url") else ""))
            self.entries.append(e)
            by_poi[e.poi_id] = e
        for pid, aliases in POI_ALIASES.items():
            if pid in by_poi:
                by_poi[pid].names.extend(norm(a) for a in aliases)
        for pid, (area, aliases) in APPROX_ALIASES.items():
            if pid in by_poi:
                src = by_poi[pid]
                for a in aliases:
                    self.entries.append(Place(
                        id=f"alias:{norm(a).replace(' ', '_')}", name=a.title(), kind="alias", x=src.x, y=src.y,
                        names=[norm(a)], poi_id=src.poi_id, approximate=True, ptype="named_region",
                        method=f"alias of the {area} area: {a.title()} has no position in our data",
                        note=f"I don't have an exact position for {a.title()}; the closest named area is {area}."))
        for n in self._names_src:
            if n.get("x") is None or not site.in_bounds(n["x"], n["y"]):
                continue
            ft = str(n.get("feature_type") or "")
            self.entries.append(Place(
                id=f"iau:{n['id']}", name=str(n["name"]), kind="iau", x=float(n["x"]), y=float(n["y"]),
                names=[norm(n["name"])], ptype="crater" if "rater" in ft else "landmark",
                diameter_m=float(n["diameter_km"]) * 1000.0 if n.get("diameter_km") else None,
                method="official IAU/USGS place name, gazetteer centre point"))

    @property
    def craters(self) -> list[dict]:
        if self._craters is None:
            try:
                self._craters = detect_craters(self.site)
            except Exception:  # noqa: BLE001  (a site without elevation or scikit-image: no crater list, not a crash)
                self._craters = []
            iau = [e for e in self.entries if e.kind == "iau" and e.ptype == "crater"]
            for c in self._craters:
                m = [e for e in iau if math.hypot(e.x - c["x"], e.y - c["y"]) <= max(300.0, c["diameter_m"])]
                if m:
                    e = min(m, key=lambda e: math.hypot(e.x - c["x"], e.y - c["y"]))
                    c["name"] = e.name
        return self._craters

    def crater_entries(self) -> list[Place]:
        out = []
        for c in self.craters:
            nm = c.get("name")
            out.append(Place(id=c["id"], name=f"{nm} crater" if nm else f"crater {c['id'].split('_')[1]}",
                             kind="crater", x=c["x"], y=c["y"], names=[norm(nm)] if nm else [],
                             diameter_m=c["diameter_m"], depth_m=c["depth_m"], ptype="crater",
                             method=f"detected from the elevation data: closed depression about {c['diameter_m']:.0f} m "
                                    f"wide and {c['depth_m']:.0f} m deep"))
        return out

    # ---- helpers -----------------------------------------------------------------------------
    def elevation(self, x: float, y: float) -> float:
        r, c = self.site.rc_index(x, y)
        z = self.site.layers["elevation"].data
        if 0 <= r < z.shape[0] and 0 <= c < z.shape[1]:
            return float(z[r, c])
        return float("nan")

    def _coarse_grid(self):
        if "z" not in self._coarse_cache:
            zb, res, _ = _coarse(self.site)
            self._coarse_cache.update(z=zb, res=res, slope=slope_deg(zb, res))
        return self._coarse_cache["z"], self._coarse_cache["res"], self._coarse_cache["slope"]

    def _desc(self, e: Place, near: tuple[float, float] | None) -> dict:
        d = {"id": e.id, "name": e.name, "kind": e.kind, "x": round(e.x, 1), "y": round(e.y, 1), "ref": e.ref(),
             "method": e.method, "approximate": e.approximate, "type": e.ptype}
        if e.diameter_m is not None:
            d["diameter_m"] = e.diameter_m
        if e.depth_m is not None:
            d["depth_m"] = e.depth_m
        if e.note:
            d["note"] = e.note
        if near is not None:
            dx, dy = e.x - near[0], e.y - near[1]
            d["distance_m"] = round(math.hypot(dx, dy), 1)
            d["direction"] = compass(dx, dy)
        return d

    # ---- fuzzy named search ---------------------------------------------------------------------
    @staticmethod
    def _score(qn: str, qt: list[str], names: list[str]) -> float:
        best = 0.0
        for n in names:
            if not n:
                continue
            if qn == n:
                return 1.0
            nt = n.split()
            s = difflib.SequenceMatcher(None, qn, n).ratio()
            if qt:
                toks = [max(difflib.SequenceMatcher(None, t, u).ratio() for u in nt) for t in qt]
                s = max(s, 0.92 * sum(toks) / len(toks) * (1.0 if len(qt) >= len(nt) or min(toks) > 0.8 else 0.9))
                if set(qt) <= set(nt) and len(qt[0]) >= 3 or (set(nt) <= set(qt) and len(nt) >= 1 and len(n) >= 4):
                    s = max(s, 0.9)
            best = max(best, s)
        return best

    def search(self, text: str, near: tuple[float, float] | None = None, limit: int = 5, kinds: set | None = None,
               include_craters: bool = True) -> list[dict]:
        """Named places ranked by fuzzy match (case, accents, typos, aliases). Each hit has score 0..1 and, with `near`, distance."""
        qt = tokens(text)
        qn = " ".join(qt)
        pool = list(self.entries) + (self.crater_entries() if include_craters else [])
        hits = []
        for e in pool:
            if kinds and e.kind not in kinds:
                continue
            s = self._score(qn, qt, e.names + [norm(e.name)]) if qn else 0.0
            if s >= 0.45:
                d = self._desc(e, near)
                d["score"] = round(s, 2)
                hits.append(d)
        hits.sort(key=lambda d: (-d["score"], d.get("distance_m", 0.0)))
        return hits[:limit]

    # ---- global (whole-planet) names -------------------------------------------------------------
    @property
    def global_names(self) -> list[dict]:
        if getattr(self, "_global", None) is None:
            try:
                self._global = json.loads(GLOBAL_PATH.read_text())["names"]
            except (OSError, ValueError, KeyError):
                self._global = []
        return self._global

    @staticmethod
    def _global_tokens(text: str) -> list[str]:
        """Query tokens with type words folded ('mount', 'mt', 'sons' -> 'mons') and de-duplicated."""
        out: list[str] = []
        for t in tokens(text):
            t = TYPE_SYN.get(t, t)
            if t not in TYPE_WORDS and len(t) <= 5:      # a near-miss of a type word ('sons' ~ 'mons')
                best = max(TYPE_WORDS, key=lambda w: difflib.SequenceMatcher(None, t, w).ratio())
                if difflib.SequenceMatcher(None, t, best).ratio() >= 0.74:
                    t = best
            if t not in out:
                out.append(t)
        return out

    def search_global(self, text: str, near: tuple[float, float] | None = None, limit: int = 4) -> list[dict]:
        """Fuzzy match against the IAU/USGS names of the whole planet (large features) with lat/lon, distance (great circle)
        and whether the point lies inside the site. A small prior favours famous features when the words fit several."""
        qt = self._global_tokens(text)
        if not qt:
            return []
        qn = " ".join(qt)
        lat0 = lon0 = None
        if near is not None:
            lat0, lon0 = self.site.xy_to_latlon(*near)
        alias_of = {norm(a): n for n, al in GLOBAL_ALIASES.items() for a in al}
        hits = []
        for g in self.global_names:
            nm = g["name"]
            names = [norm(nm)] + [norm(a) for a in GLOBAL_ALIASES.get(nm, [])]
            names = [" ".join(self._global_tokens(n)) for n in names]
            base = self._score(qn, qt, names)
            if base < 0.6:
                continue
            score = min(1.0, base + FAMOUS.get(nm, 0.0))
            x, y = self.site.latlon_to_xy(g["lat"], g["lon"] if g["lon"] <= 180 else g["lon"] - 360)
            d = {"id": "global:" + norm(nm).replace(" ", "_"), "name": nm if "Mons" in nm or "Planitia" in nm or "Vallis" in nm
                 or "Valles" in nm or "Fossae" in nm else (f"{nm} crater" if "rater" in g["feature_type"] else nm),
                 "kind": "global", "lat": g["lat"], "lon": g["lon"], "x": round(x, 1), "y": round(y, 1),
                 "diameter_km": g.get("diameter_km"), "feature_type": g.get("feature_type"), "score": round(score, 2),
                 "base_score": round(base, 2), "in_site": self.site.in_bounds(x, y), "approximate": False,
                 "source_url": g.get("source_url"),
                 "method": "IAU/USGS Gazetteer of Planetary Nomenclature centre point (Mars 2000 sphere)"}
            if lat0 is not None:
                d["distance_m"] = round(great_circle_m(lat0, lon0, g["lat"], g["lon"]), 0)
            hits.append(d)
        hits.sort(key=lambda d: (-d["score"], -(d.get("diameter_km") or 0)))
        return hits[:limit]

    # ---- generic terms --------------------------------------------------------------------------
    def _generic_kind(self, qt: list[str]) -> tuple[str, dict] | None:
        s = set(qt)
        if not s:
            return None
        if "rim" in s and (s - {"rim", "crater", "craters", "edge", "wall", "lip"} == set() or s <= CRATER_WORDS | {"rim"}):
            return "rim", {}
        if "pad" in s and s <= FLAT_WORDS | FLAT_FILLER | {"landing"}:
            return "flat", {}
        if s & CRATER_WORDS and s <= CRATER_WORDS | SIZE_BIG | SIZE_SMALL | FLOOR_WORDS | {"another", "other", "second"}:
            return "crater", {"size": "big" if s & SIZE_BIG else "small" if s & SIZE_SMALL else None,
                              "floor": bool(s & FLOOR_WORDS)}
        if s & FLAT_WORDS and s <= FLAT_WORDS | FLAT_FILLER:
            return "flat", {}
        if s & HIGH_WORDS and s <= HIGH_WORDS | HIGH_FILLER:
            return "high", {}
        return None

    def _rim_point(self, c: dict, near: tuple[float, float] | None) -> tuple[float, float, float]:
        """Rim crest of crater c on the side facing `near`: the highest ground along the ray from the centre."""
        R = c["diameter_m"] / 2.0
        cx, cy = c["x"], c["y"]
        if near is None:
            ux, uy = 0.0, 1.0
        else:
            dx, dy = near[0] - cx, near[1] - cy
            L = math.hypot(dx, dy)
            ux, uy = (dx / L, dy / L) if L > 1e-6 else (0.0, 1.0)
        slope = self.site.layers["slope_2m"].data if "slope_2m" in self.site.layers else None
        best = (-1e18, cx + ux * R, cy + uy * R)
        fallback = (1e18, best[1], best[2], float("nan"))     # (slope, x, y, z) of the gentlest point if none is gentle
        for f in np.linspace(0.85, 1.4, 23):
            x, y = cx + ux * R * f, cy + uy * R * f
            if not self.site.in_bounds(x, y):
                continue
            z = self.elevation(x, y)
            if not math.isfinite(z):
                continue
            sl = 0.0
            if slope is not None:
                r_, c_ = self.site.rc_index(x, y)
                sl = float(slope[r_, c_]) if 0 <= r_ < slope.shape[0] and 0 <= c_ < slope.shape[1] else 90.0
                if not math.isfinite(sl):
                    sl = 90.0
            if sl <= RIM_MAX_SLOPE_DEG and z > best[0]:
                best = (z, x, y)
            if sl < fallback[0]:
                fallback = (sl, x, y, z)
        if best[0] < -1e17 and fallback[0] < 1e17:
            best = (fallback[3], fallback[1], fallback[2])
        return best[1], best[2], best[0]

    def _resolve_generic(self, kind: str, opts: dict, near: tuple[float, float] | None, text: str) -> dict | None:
        ref = near if near is not None else (0.0, 0.0)
        if kind in ("crater", "rim"):
            cands = self.craters
            if not cands:
                return {"kind": kind, "empty": "I can't find any crater-shaped depression in this site's elevation data."}
            def far(c): return math.hypot(c["x"] - ref[0], c["y"] - ref[1])
            pool = [c for c in cands if far(c) > 0.5 * c["diameter_m"] / 2.0] or cands   # not the one you are already in
            if opts.get("size") == "big":
                near_pool = [c for c in pool if far(c) <= 3000.0] or pool
                pick = max(near_pool, key=lambda c: c["diameter_m"])
            elif opts.get("size") == "small":
                pick = min(pool, key=lambda c: c["diameter_m"])
            else:
                pick = min(pool, key=far)
            ranked = sorted(pool, key=far)
            nm = pick.get("name")
            label = f"{nm} crater" if nm else "the nearest crater"
            if kind == "rim" or not opts.get("floor"):
                x, y, z = self._rim_point(pick, near)
                where = "its near rim"
                x, y = round(x, 1), round(y, 1)
            else:
                x, y, z = pick["x"], pick["y"], pick["floor_z"]
                where = "its floor"
            dx, dy = x - ref[0], y - ref[1]
            expl = (f"{'Nearest' if opts.get('size') is None else 'Biggest' if opts.get('size') == 'big' else 'Smallest'} "
                    f"crater: {dist_words(pick['diameter_m'])} wide, {pick['depth_m']:.0f} m deep"
                    f"{', called ' + nm if nm else ''}; {dist_words(math.hypot(dx, dy))} "
                    f"{compass(dx, dy)} of {'the rover' if near is not None else 'the site centre'}. Using {where}.")
            chosen = {"id": pick["id"], "name": label, "kind": "crater", "x": x, "y": y, "ref": f"{x:.1f},{y:.1f}",
                      "diameter_m": pick["diameter_m"], "depth_m": pick["depth_m"], "elevation_m": round(z, 1),
                      "distance_m": round(math.hypot(dx, dy), 1), "direction": compass(dx, dy), "approximate": False,
                      "method": f"nearest crater detected from the elevation data (closed depression); position is {where}"}
            alts = []
            for c in ranked[:4]:
                if c["id"] == pick["id"]:
                    continue
                cdx, cdy = c["x"] - ref[0], c["y"] - ref[1]
                alts.append({"id": c["id"], "name": c.get("name") or c["id"], "x": c["x"], "y": c["y"],
                             "diameter_m": c["diameter_m"], "distance_m": round(math.hypot(cdx, cdy), 1),
                             "direction": compass(cdx, cdy), "ref": f"{c['x']:.1f},{c['y']:.1f}"})
            return {"kind": kind, "chosen": chosen, "explanation": expl, "candidates": alts[:3]}
        if kind == "high":
            zb, res, slope = self._coarse_grid()
            n = zb.shape[0]
            rr, cc = np.indices(zb.shape)
            X, Y = _cell_xy(self.site, res, rr, cc)
            d = np.hypot(X - ref[0], Y - ref[1])
            ok = (d <= HILL_RADIUS_M) & np.isfinite(zb) & (slope <= HILL_MAX_SLOPE_DEG)
            if not ok.any():
                return {"kind": kind, "empty": "There is no gently sloping high ground within 1 km."}
            zz = np.where(ok, zb, -np.inf)
            i = np.unravel_index(int(np.argmax(zz)), zz.shape)
            x, y = float(X[i]), float(Y[i])
            z0 = self.elevation(*ref)
            gain = float(zb[i]) - z0 if math.isfinite(z0) else None
            dx, dy = x - ref[0], y - ref[1]
            expl = (f"Highest gently sloping ground within 1 km: "
                    f"{('about ' + str(int(round(gain))) + ' m above the rover, ') if gain is not None else ''}"
                    f"{dist_words(math.hypot(dx, dy))} {compass(dx, dy)}.")
            chosen = {"id": "highest_nearby", "name": "the high ground nearby", "kind": "highest", "x": round(x, 1),
                      "y": round(y, 1), "ref": f"{x:.1f},{y:.1f}", "elevation_m": round(float(zb[i]), 1),
                      "distance_m": round(math.hypot(dx, dy), 1), "direction": compass(dx, dy), "approximate": False,
                      "radius_m": HILL_RADIUS_M, "method": "highest 8 m cell within 1 km whose slope is at most 20 degrees"}
            return {"kind": "highest", "chosen": chosen, "explanation": expl, "candidates": [], "radius_m": HILL_RADIUS_M}
        if kind == "flat":
            zb, res, slope = self._coarse_grid()
            from scipy import ndimage as ndi
            smooth = ndi.maximum_filter(np.nan_to_num(slope, nan=90.0), size=3)
            flat = (smooth <= FLAT_MAX_SLOPE_DEG) & np.isfinite(zb)
            for c in self.craters:       # a crater floor is flat but ringed by walls: not a place to send anyone
                rr, cc = np.indices(zb.shape)
                X, Y = _cell_xy(self.site, res, rr, cc)
                flat &= np.hypot(X - c["x"], Y - c["y"]) > 0.7 * c["diameter_m"]
            rr, cc = np.indices(zb.shape)
            X, Y = _cell_xy(self.site, res, rr, cc)
            d = np.hypot(X - ref[0], Y - ref[1])
            flat &= d >= FLAT_MIN_DIST_M
            if not flat.any():
                return {"kind": kind, "empty": "I can't find flat open ground in this site's data."}
            dd = np.where(flat, d, np.inf)
            i = np.unravel_index(int(np.argmin(dd)), dd.shape)
            x, y = float(X[i]), float(Y[i])
            dx, dy = x - ref[0], y - ref[1]
            expl = (f"Nearest flat ground (slope under {FLAT_MAX_SLOPE_DEG:.0f} degrees all around): "
                    f"{dist_words(math.hypot(dx, dy))} {compass(dx, dy)} of the rover.")
            chosen = {"id": "flat_nearby", "name": "flat ground nearby", "kind": "flat", "x": round(x, 1), "y": round(y, 1),
                      "ref": f"{x:.1f},{y:.1f}", "distance_m": round(math.hypot(dx, dy), 1),
                      "direction": compass(dx, dy), "approximate": False,
                      "method": "nearest 8 m cell whose 3x3 neighbourhood has slope at most 4 degrees, outside craters"}
            return {"kind": kind, "chosen": chosen, "explanation": expl, "candidates": []}
        return None

    def _global_result(self, text: str, gl: list[dict], near) -> dict:
        top = gl[0]
        guess = top["base_score"] < 0.95 or (len(gl) > 1 and gl[1]["score"] >= top["score"] - 0.02)
        km = top.get("distance_m")
        lat = f"{abs(top['lat']):.0f} {'N' if top['lat'] >= 0 else 'S'}"
        lon = f"{top['lon']:.0f} E"
        who = f"{top['name']} ({lat}, {lon})"
        lead = f"I think you mean {who}" if guess else f"{who}"
        if top["in_site"]:
            where = " It is inside the Jezero site, so the rover can go there."
        else:
            far = f" about {km / 1000.0:,.0f} km from the rover" if km is not None else ""
            where = (f" That is{far} (great-circle on our {MARS_R_M / 1000:.0f} km Mars sphere), far outside the 8 km "
                     "Jezero area the rover can drive in.")
        sw = STREET_WORLDS.get(top["name"])
        if sw and not top["in_site"]:
            where += f" The viewer has a street-view patch there: switch to Drive and pick '{sw}'."
        top = {**top, "street_world": sw, "outside_site": not top["in_site"]}
        return {"resolved": True, "kind": "global", "chosen": top, "explanation": f"{lead}.{where}",
                "candidates": gl[1:4] if guess else [], "ambiguous": guess, "outside_site": not top["in_site"]}

    # ---- the one entry point --------------------------------------------------------------------
    def resolve(self, text: str, near: tuple[float, float] | None = None, limit: int = 4) -> dict:
        """Resolve free text to a place. Always returns {query, resolved, chosen?, explanation, candidates, kind}.
        resolved=False only when nothing plausible matches; candidates then lists the nearest named places."""
        qt = tokens(text)
        out: dict = {"query": text, "resolved": False, "candidates": []}
        g = self._generic_kind(qt)
        if g:
            r = self._resolve_generic(g[0], g[1], near, text)
            if r and r.get("chosen"):
                return {**out, **r, "resolved": True, "kind": r["kind"]}
            if r and r.get("empty"):
                out["explanation"] = r["empty"]
        hits = self.search(text, near, limit=limit + 2)
        gl = self.search_global(text, near, limit=limit + 1)
        if gl and gl[0]["score"] >= 0.6 and (not hits or gl[0]["score"] > hits[0]["score"] + 1e-9):
            return {**out, **self._global_result(text, gl, near)}
        if hits and hits[0]["score"] >= 0.6:
            top = hits[0]
            lead = top["score"] - (hits[1]["score"] if len(hits) > 1 else 0.0)
            ok = top["score"] >= 0.8 or lead >= 0.08 or len(hits) == 1
            if ok or top["score"] >= 0.6:
                where = ""
                if "distance_m" in top:
                    where = (" The rover is already there." if top["distance_m"] < 15 else
                             f" It is {dist_words(top['distance_m'])} {top['direction']} of the rover.")
                label = top["name"]
                if top["approximate"]:
                    expl = (top.get("note") or "") + where
                elif top["score"] < 0.8:
                    expl = f"Closest match to \"{text}\": {label}.{where}"
                elif top["kind"] == "crater":
                    expl = f"{label}: {dist_words(top.get('diameter_m') or 0)} wide.{where}"
                else:
                    expl = f"{label}.{where}"
                return {**out, "resolved": True, "kind": "named", "chosen": top, "explanation": expl.strip(),
                        "candidates": hits[1:limit + 1] if lead < 0.2 else [], "ambiguous": not (top["score"] >= 0.8 or lead >= 0.08)}
        # nothing matched: offer the nearest named places (never a dead end)
        pool = [self._desc(e, near) for e in self.entries if e.kind in ("poi", "iau") and e.ptype != "rover_waypoint"]
        pool += [self._desc(e, near) for e in self.crater_entries()]
        if near is not None:
            pool.sort(key=lambda d: d.get("distance_m", 1e18))
        out["candidates"] = pool[:limit]
        out.setdefault("explanation", f"I don't know a place called \"{text}\".")
        out["explanation"] += " Nearby places I do know: " + ", ".join(
            f"{c['name']} ({dist_words(c['distance_m'])} {c['direction']})" if "distance_m" in c else c["name"]
            for c in out["candidates"]) + "."
        return out
