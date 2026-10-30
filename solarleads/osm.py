"""Step 1: pull every mapped solar array in a state from OpenStreetMap (Overpass API)."""
import logging
import time

import requests

from .config import settings
from .geo import polygon_area_m2, tiles

log = logging.getLogger(__name__)

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def _overpass(query: str, tries: int = 5) -> dict:
    headers = {"User-Agent": settings.user_agent}
    wait = 10
    for attempt in range(1, tries + 1):
        try:
            r = requests.post(settings.overpass_url, data={"data": query}, headers=headers, timeout=240)
            if r.status_code == 200 and r.text.lstrip().startswith("{"):
                return r.json()
            log.warning("Overpass returned %s (attempt %d); waiting %ds", r.status_code, attempt, wait)
        except requests.RequestException as e:
            log.warning("Overpass error %s (attempt %d); waiting %ds", e, attempt, wait)
        time.sleep(wait)
        wait = min(wait * 2, 120)
    raise RuntimeError("Overpass API did not answer after retries")


def state_bounds(state: str) -> tuple[float, float, float, float]:
    """(west, south, east, north) of a state's boundary relation."""
    q = f'[out:json][timeout:60];rel["ISO3166-2"="US-{state}"]["admin_level"="4"];out bb;'
    els = _overpass(q).get("elements", [])
    if not els or "bounds" not in els[0]:
        raise ValueError(f"Could not find boundary for state {state}")
    b = els[0]["bounds"]
    return b["minlon"], b["minlat"], b["maxlon"], b["maxlat"]


def _element_to_array(e: dict) -> dict | None:
    tags = e.get("tags", {})
    if e["type"] == "node":
        lat, lon, area = e["lat"], e["lon"], 0.0
    elif e["type"] == "way" and e.get("geometry"):
        ring = [(g["lat"], g["lon"]) for g in e["geometry"]]
        lat = sum(p[0] for p in ring) / len(ring)
        lon = sum(p[1] for p in ring) / len(ring)
        area = polygon_area_m2(ring)
    elif e["type"] == "relation" and e.get("members"):
        rings = [[(g["lat"], g["lon"]) for g in m.get("geometry", [])] for m in e["members"] if m.get("role") == "outer"]
        rings = [r for r in rings if len(r) >= 3]
        if not rings:
            return None
        pts = [p for r in rings for p in r]
        lat = sum(p[0] for p in pts) / len(pts)
        lon = sum(p[1] for p in pts) / len(pts)
        area = sum(polygon_area_m2(r) for r in rings)
    else:
        return None
    modules = tags.get("generator:solar:modules", "")
    return {
        "osm_id": f"{e['type'][0]}{e['id']}",
        "lat": lat,
        "lon": lon,
        "area_m2": round(area, 1),
        "modules": int(modules) if str(modules).isdigit() else 0,
        "location": tags.get("location", ""),
        "name": tags.get("name", ""),
        "operator": tags.get("operator", ""),
        "plant": tags.get("power") == "plant",
    }


def fetch_state_arrays(state: str, done_tiles: set[str] | None = None, on_tile=None) -> list[dict]:
    """All solar generators/plants in the state. Resumable: tiles already in `done_tiles` are skipped.

    `on_tile(tile_key, arrays)` is called after each tile so results can be saved as we go.
    """
    state = state.upper()
    if state not in STATES:
        raise ValueError(f"Unknown state code {state}")
    done_tiles = done_tiles or set()
    west, south, east, north = state_bounds(state)
    out: list[dict] = []
    all_tiles = list(tiles(west, south, east, north, settings.tile_degrees))
    log.info("%s: %d map tiles to scan", state, len(all_tiles))
    for n, (s, w, nn, e) in enumerate(all_tiles, 1):
        key = f"{state}:{s},{w},{nn},{e}"
        if key in done_tiles:
            continue
        q = f"""[out:json][timeout:180];
area["ISO3166-2"="US-{state}"]["admin_level"="4"]->.s;
(
  nwr["generator:source"="solar"](area.s)({s},{w},{nn},{e});
  nwr["power"="plant"]["plant:source"="solar"](area.s)({s},{w},{nn},{e});
);
out tags geom;"""
        data = _overpass(q)
        arrays = [a for a in (_element_to_array(el) for el in data.get("elements", [])) if a]
        for a in arrays:
            a["state"] = state
        log.info("  tile %d/%d: %d arrays", n, len(all_tiles), len(arrays))
        out.extend(arrays)
        if on_tile:
            on_tile(key, arrays)
        time.sleep(settings.overpass_pause_s)
    return out
