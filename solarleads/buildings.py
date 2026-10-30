"""Building footprints (Overture Maps): put each site's panels on the exact building they sit on.

That gives us (a) the real roof size, (b) the building type from the map (house, warehouse, school...),
and (c) a reliable business match: businesses *inside* that building, instead of whatever is closest.
"""
import json
import logging
import math
import os
import struct

from .config import settings
from .geo import EARTH_R, GridIndex, haversine_m, polygon_area_m2

log = logging.getLogger(__name__)

HOME_CLASSES = {"house", "detached", "semidetached_house", "terrace", "bungalow", "residential", "static_caravan",
                "cabin", "hut", "garage", "garages", "shed", "carport"}
MULTI_CLASSES = {"apartments", "dormitory", "residential_complex"}
COMMERCIAL_SUBTYPES = {"commercial", "industrial", "education", "religious", "civic", "medical", "service",
                       "transportation", "entertainment", "agricultural", "military"}


# --- WKB (the binary geometry format Overture uses) --------------------------------------------
def parse_wkb(b: bytes) -> list[list[list[tuple[float, float]]]]:
    """Polygon or MultiPolygon WKB -> list of polygons, each a list of rings of (lat, lon). Z/M values ignored."""
    polys, _ = _read_geom(memoryview(b), 0)
    return polys


def _read_geom(buf, off):
    order = "<" if buf[off] == 1 else ">"
    (gtype,) = struct.unpack_from(order + "I", buf, off + 1)
    off += 5
    g = gtype & 0x0FFFFFFF
    iso, base = divmod(g, 1000)  # ISO WKB: 1003 = Polygon Z, 2003 = Polygon M, 3003 = Polygon ZM
    has_z = bool(gtype & 0x80000000) or iso in (1, 3)
    has_m = bool(gtype & 0x40000000) or iso in (2, 3)
    dims = 2 + int(has_z) + int(has_m)
    if base == 3:  # Polygon
        rings, off = _read_rings(buf, off, order, dims)
        return [rings], off
    if base == 6:  # MultiPolygon
        (n,) = struct.unpack_from(order + "I", buf, off)
        off += 4
        polys = []
        for _ in range(n):
            p, off = _read_geom(buf, off)
            polys.extend(p)
        return polys, off
    raise ValueError(f"Unsupported geometry type {gtype}")


def _read_rings(buf, off, order, dims):
    (nr,) = struct.unpack_from(order + "I", buf, off)
    off += 4
    rings = []
    for _ in range(nr):
        (npts,) = struct.unpack_from(order + "I", buf, off)
        off += 4
        vals = struct.unpack_from(order + "d" * (npts * dims), buf, off)
        off += 8 * npts * dims
        rings.append([(vals[i * dims + 1], vals[i * dims]) for i in range(npts)])  # (lat, lon)
    return rings, off


# --- point in polygon / distance ---------------------------------------------------------------
def point_in_rings(lat: float, lon: float, rings: list[list[tuple[float, float]]]) -> bool:
    """Even-odd rule over all rings, so courtyard holes count as outside."""
    inside = False
    for ring in rings:
        n = len(ring)
        for i in range(n):
            y1, x1 = ring[i]
            y2, x2 = ring[(i + 1) % n]
            if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
                inside = not inside
    return inside


def distance_to_rings_m(lat: float, lon: float, rings) -> float:
    """Shortest distance from a point to the outline (0 if inside)."""
    if point_in_rings(lat, lon, rings):
        return 0.0
    kx = math.cos(math.radians(lat)) * EARTH_R * math.pi / 180
    ky = EARTH_R * math.pi / 180
    best = float("inf")
    for ring in rings:
        pts = [((p[1] - lon) * kx, (p[0] - lat) * ky) for p in ring]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
            dx, dy = x2 - x1, y2 - y1
            L = dx * dx + dy * dy
            t = 0 if L == 0 else max(0, min(1, -(x1 * dx + y1 * dy) / L))
            best = min(best, math.hypot(x1 + t * dx, y1 + t * dy))
    return best


# --- download + cache ------------------------------------------------------------------------
def _flatten(row: dict) -> dict | None:
    try:
        geom = row.get("geometry")
        polys = parse_wkb(bytes(geom)) if geom is not None else []
        if not polys:
            return None
        rings = polys[0] if len(polys) == 1 else [r for p in polys for r in p]
        outer = [r for p in polys for r in p[:1]]
        area = sum(polygon_area_m2(r) for r in outer) - sum(polygon_area_m2(r) for p in polys for r in p[1:])
        pts = [pt for r in outer for pt in r]
        names = row.get("names") or {}
        return {
            "id": row.get("id", ""),
            "subtype": (row.get("subtype") or "").lower(),
            "class": (row.get("class") or "").lower(),
            "name": (names.get("primary") if isinstance(names, dict) else "") or "",
            "height": row.get("height"),
            "rings": [[[round(a, 7), round(b, 7)] for a, b in r] for r in rings],
            "area_m2": round(area, 1),
            "lat": sum(p[0] for p in pts) / len(pts),
            "lon": sum(p[1] for p in pts) / len(pts),
            "bbox": [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)],
        }
    except Exception:
        return None


def download_buildings(bbox: tuple[float, float, float, float]) -> list[dict]:
    from overturemaps import core

    reader = core.record_batch_reader("building", bbox=bbox)
    out = []
    if reader is None:
        return out
    for batch in reader:
        for row in batch.to_pylist():
            b = _flatten(row)
            if b:
                out.append(b)
    return out


def cell_of(lat: float, lon: float, step: float) -> tuple[float, float, float, float]:
    s = math.floor(lat / step) * step
    w = math.floor(lon / step) * step
    return (round(w, 4), round(s, 4), round(w + step, 4), round(s + step, 4))


def buildings_for_cell(cell: tuple[float, float, float, float]) -> list[dict]:
    w, s, e, n = cell
    path = os.path.join(settings.data_dir, "buildings", f"{s:.3f}_{w:.3f}_{n:.3f}_{e:.3f}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    blds = download_buildings(cell)
    with open(path, "w") as f:
        json.dump(blds, f)
    log.info("  downloaded %d building footprints for cell %s", len(blds), cell)
    return blds


class BuildingIndex:
    def __init__(self, buildings: list[dict]):
        self.idx = GridIndex(cell_deg=0.002)
        for b in buildings:
            self.idx.add(b["lat"], b["lon"], b)

    def containing(self, lat: float, lon: float, search_m: float = 400) -> dict | None:
        for _, b in self.idx.near(lat, lon, search_m):
            s, w, n, e = b["bbox"]
            if s <= lat <= n and w <= lon <= e and point_in_rings(lat, lon, b["rings"]):
                return b
        return None

    def nearest(self, lat: float, lon: float, max_m: float) -> tuple[dict | None, float]:
        best, bd = None, float("inf")
        for _, b in self.idx.near(lat, lon, max_m + 300):
            d = distance_to_rings_m(lat, lon, b["rings"])
            if d < bd:
                best, bd = b, d
        return (best, bd) if bd <= max_m else (None, bd)


def snap_site(site: dict, bidx: BuildingIndex) -> dict | None:
    """Pick the building that holds most of the site's arrays. Returns the building or None."""
    votes: dict[str, list] = {}
    for lat, lon, area in site.get("members", [(site["lat"], site["lon"], site.get("area_m2", 0))]):
        b = bidx.containing(lat, lon)
        if b:
            votes.setdefault(b["id"] or f"{b['lat']},{b['lon']}", [b, 0.0])[1] += max(area, 1.0)
    if votes:
        b = max(votes.values(), key=lambda v: v[1])[0]
        site["on_building"] = True
        return b
    b, d = bidx.nearest(site["lat"], site["lon"], settings.building_near_m)
    site["on_building"] = False  # carport, ground mount, or footprint missing
    return b


def is_home(b: dict) -> bool:
    """A house-sized residential building. Big 'residential' footprints are usually apartments, so not homes."""
    if b.get("class") in MULTI_CLASSES or b.get("area_m2", 0) >= 1000:
        return False
    return b.get("class") in HOME_CLASSES or b.get("subtype") == "residential"


def is_multifamily(b: dict) -> bool:
    return b.get("class") in MULTI_CLASSES


def businesses_inside(b: dict, place_idx: GridIndex) -> list[dict]:
    s, w, n, e = b["bbox"]
    radius = haversine_m(s, w, n, e) / 2 + 10
    out = []
    for _, p in place_idx.near(b["lat"], b["lon"], radius):
        if point_in_rings(p["lat"], p["lon"], b["rings"]):
            out.append(p)
    return out


def business_next_to(b: dict, place_idx: GridIndex, max_m: float) -> dict | None:
    """Closest business within max_m of the building's outline (not its center), for when none is inside."""
    s, w, n, e = b["bbox"]
    radius = haversine_m(s, w, n, e) / 2 + max_m
    best, bd = None, float("inf")
    for _, p in place_idx.near(b["lat"], b["lon"], radius):
        d = distance_to_rings_m(p["lat"], p["lon"], b["rings"])
        if d <= max_m and d < bd:
            best, bd = p, d
    return best


def businesses_near(b: dict, place_idx: GridIndex, max_m: float) -> list[dict]:
    """Businesses inside or within max_m of the building outline, nearest first."""
    s, w, n, e = b["bbox"]
    radius = haversine_m(s, w, n, e) / 2 + max_m
    out = []
    for _, p in place_idx.near(b["lat"], b["lon"], radius):
        d = distance_to_rings_m(p["lat"], p["lon"], b["rings"])
        if d <= max_m:
            out.append((d, p))
    out.sort(key=lambda t: t[0])
    return [p for _, p in out]
