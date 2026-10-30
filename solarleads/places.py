"""Step 5: match each site to a business (name, category, phone, website) using Overture Maps places.

Overture is free, nationwide, and published as cloud GeoParquet. We download only the places inside
the bounding box of each group of sites, then cache the result on disk.
"""
import json
import logging
import os

from .config import settings
from .geo import GridIndex

log = logging.getLogger(__name__)


def _first(v, key=None):
    if isinstance(v, list) and v:
        v = v[0]
    if key and isinstance(v, dict):
        return v.get(key)
    return v


def _flatten(row: dict) -> dict | None:
    """Turn one Overture place record into the few fields we use. Defensive: the schema evolves between releases."""
    try:
        names = row.get("names") or {}
        name = names.get("primary") if isinstance(names, dict) else None
        if not name:
            return None
        status = (row.get("operating_status") or "").lower()
        if status and status != "open":  # e.g. permanently_closed / temporarily_closed
            return None
        # Category moved from `categories.primary` to `taxonomy.primary` in 2026 releases; read either.
        tax = row.get("taxonomy") or {}
        cats = row.get("categories") or {}
        category = ((tax.get("primary") if isinstance(tax, dict) else None)
                    or (cats.get("primary") if isinstance(cats, dict) else None)
                    or row.get("basic_category") or "")
        addr = _first(row.get("addresses")) or {}
        lat = row.get("lat")
        lon = row.get("lon")
        if lat is None or lon is None:
            bb = row.get("bbox") or {}
            lat = (bb.get("ymin", 0) + bb.get("ymax", 0)) / 2
            lon = (bb.get("xmin", 0) + bb.get("xmax", 0)) / 2
        return {
            "name": name,
            "category": category or "",
            "phone": _first(row.get("phones")) or "",
            "website": _first(row.get("websites")) or "",
            "address": addr.get("freeform", "") if isinstance(addr, dict) else "",
            "city": addr.get("locality", "") if isinstance(addr, dict) else "",
            "zip": addr.get("postcode", "") if isinstance(addr, dict) else "",
            "confidence": float(row.get("confidence") or 0),
            "lat": float(lat),
            "lon": float(lon),
        }
    except Exception:  # one bad record should never stop a state run
        return None


def download_places(bbox: tuple[float, float, float, float]) -> list[dict]:
    """Places inside (west, south, east, north). Uses the official `overturemaps` package."""
    from overturemaps import core  # imported here so tests do not need it

    west, south, east, north = bbox
    reader = core.record_batch_reader("place", bbox=(west, south, east, north))
    out = []
    if reader is None:
        return out
    for batch in reader:
        for row in batch.to_pylist():
            p = _flatten(row)
            if p:
                out.append(p)
    return out


def places_for_tile(tile_bbox: tuple[float, float, float, float]) -> list[dict]:
    """Cached per map tile so re-runs do not download again."""
    west, south, east, north = tile_bbox
    path = os.path.join(settings.data_dir, "places", f"{south:.3f}_{west:.3f}_{north:.3f}_{east:.3f}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    places = download_places(tile_bbox)
    with open(path, "w") as f:
        json.dump(places, f)
    log.info("  downloaded %d business places for tile %s", len(places), tile_bbox)
    return places


# Overture categories that are homes, not businesses — never treat these as a match.
NON_BUSINESS = ("residential", "home", "house", "apartment_building", "neighborhood", "landmark", "street", "park")


def build_index(places: list[dict]) -> GridIndex:
    idx = GridIndex(cell_deg=0.002)
    for p in places:
        if any(k in (p.get("category") or "").lower() for k in NON_BUSINESS):
            continue
        idx.add(p["lat"], p["lon"], p)
    return idx


def best_match(idx: GridIndex, lat: float, lon: float, meters: float) -> tuple[dict | None, int]:
    """Closest well-formed business within `meters`, plus how many businesses are that close."""
    near = idx.near(lat, lon, meters)
    if not near:
        return None, 0
    # Prefer higher-confidence records that have a phone number, then distance.
    ranked = sorted(near, key=lambda t: (t[0] - 25 * t[1].get("confidence", 0) - (15 if t[1].get("phone") else 0)))
    return ranked[0][1], len(near)
