"""Run every step for one state and return every site (call-list sites and excluded ones).

Order is chosen to keep costs down: the free steps (map, businesses, building footprints) run first,
the paid steps (Regrid, roof check) only on sites that still look commercial, biggest first,
and both are capped per run and cached so a site is never paid for twice.
"""
import logging
import math
from concurrent.futures import ThreadPoolExecutor

from . import buildings, imagery, osm, parcels, places, vision
from .config import settings
from .geo import cluster_points
from .score import categorize, est_panels, tier
from .store import Store

log = logging.getLogger(__name__)

SOLAR_FARM_M2 = 20000  # ground arrays this big are utility/community solar farms, not a building
BIG_ROOF_M2 = 2000      # building footprint this big = warehouse / big box / campus building


def build_sites(arrays: list[dict]) -> list[dict]:
    """Group arrays on the same property into one site."""
    sites = []
    for group in cluster_points(arrays, settings.cluster_meters):
        total = sum(a["area_m2"] for a in group) or 0.0
        w = [(a["area_m2"] or 1.0) for a in group]
        lat = sum(a["lat"] * wi for a, wi in zip(group, w)) / sum(w)
        lon = sum(a["lon"] * wi for a, wi in zip(group, w)) / sum(w)
        ids = sorted(a["osm_id"] for a in group)
        named = next((a for a in group if a.get("name") or a.get("operator")), None)
        sites.append({
            "site_id": f"{group[0]['state']}-{ids[0]}",
            "state": group[0]["state"],
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "members": [(a["lat"], a["lon"], a["area_m2"]) for a in group],
            "arrays": len(group),
            "area_m2": round(total, 1),
            "modules": sum(a.get("modules", 0) for a in group),
            "rooftop": any(a.get("location") in ("roof", "rooftop") for a in group),
            "is_solar_farm": any(a.get("plant") for a in group)
            or (total >= SOLAR_FARM_M2 and not any(a.get("location") == "roof" for a in group)),
            "osm_name": (named.get("name") or named.get("operator")) if named else "",
        })
    return sites


def _cell(lat: float, lon: float, step: float) -> tuple[float, float, float, float]:
    s = math.floor(lat / step) * step
    w = math.floor(lon / step) * step
    return (round(w, 4), round(s, 4), round(w + step, 4), round(s + step, 4))


def _set_business(s: dict, p: dict | None) -> None:
    for k in ("biz_name", "biz_category", "phone", "website", "biz_address", "biz_city", "biz_zip"):
        s.pop(k, None)
    if p:
        s.update(biz_name=p["name"], biz_category=p["category"], phone=p["phone"],
                 website=p["website"], biz_address=p["address"], biz_city=p["city"], biz_zip=p.get("zip", ""))


def attach_businesses(sites: list[dict]) -> dict:
    """First pass (free): closest business within PLACE_MATCH_METERS. Returns the place index per tile."""
    by_tile: dict[tuple, list[dict]] = {}
    for s in sites:
        by_tile.setdefault(_cell(s["lat"], s["lon"], settings.tile_degrees), []).append(s)
    indexes = {}
    for t, group in by_tile.items():
        try:
            idx = places.build_index(places.places_for_tile(t))
        except Exception as e:  # Overture outage should not kill the run
            log.warning("Business data unavailable for tile %s: %s", t, e)
            continue
        indexes[t] = idx
        for s in group:
            p, count = places.best_match(idx, s["lat"], s["lon"], settings.place_match_meters)
            s["businesses_nearby"] = count
            _set_business(s, p)
    return indexes


def attach_buildings(sites: list[dict], place_indexes: dict) -> None:
    """Second pass (free): put panels on their building, read its type and size, and re-pick the business
    from the businesses actually inside that building."""
    by_cell: dict[tuple, list[dict]] = {}
    for s in sites:
        by_cell.setdefault(_cell(s["lat"], s["lon"], settings.building_cell_deg), []).append(s)
    for cell, group in by_cell.items():
        try:
            bidx = buildings.BuildingIndex(buildings.buildings_for_cell(cell))
        except Exception as e:
            log.warning("Building footprints unavailable for %s: %s (keeping first-pass match)", cell, e)
            continue
        for s in group:
            b = buildings.snap_site(s, bidx)
            if not b:
                s["building_match"] = "no footprint"
                continue
            s.update(bldg_id=b.get("id", ""), bldg_subtype=b.get("subtype", ""), bldg_class=b.get("class", ""),
                     bldg_name=b.get("name", ""), roof_m2=b.get("area_m2", 0))
            pidx = place_indexes.get(_cell(s["lat"], s["lon"], settings.tile_degrees))
            tenants = buildings.businesses_inside(b, pidx) if pidx else []
            s["tenants"] = len(tenants)
            s["tenant_names"] = ", ".join(dict.fromkeys(p["name"] for p in tenants))[:200]
            anchor, how = pick_anchor(b, tenants, pidx)
            s["building_match"] = how
            _set_business(s, anchor)
            # 3+ businesses and none of them is "the" building (e.g. an office park or strip center)
            if s["tenants"] >= 3 and not how.endswith("(matches building name)"):
                s["multi_tenant"] = True
            if buildings.is_home(b) and not tenants:
                s["land_use"] = "home"
            elif buildings.is_multifamily(b):
                s["land_use"] = "multifamily"


# Listings that sit inside a building but are never the customer (kiosks, ATMs, exchanges, lockers).
MINOR = ("atm", "kiosk", "vending", "propane", "redbox", "bitcoin", "locker", "ev_charging", "charging_station",
         "little free library", "drop box", "dropbox")


def _is_minor(p: dict) -> bool:
    text = f"{p.get('name', '')} {p.get('category', '')}".lower()
    return any(w in text for w in MINOR)


def _name_words(s: str) -> set[str]:
    import re

    stop = {"LLC", "INC", "CO", "CORP", "THE", "OF", "AND", "LP", "LTD", "COMPANY", "KC", "MO",
            # place words that make unrelated names look alike ("... Kansas City-Liberty")
            "KANSAS", "CITY", "NORTH", "SOUTH", "EAST", "WEST", "NORTHLAND", "DOWNTOWN", "METRO", "AT"}
    return {w.rstrip("S") for w in re.split(r"[^A-Z0-9]+", (s or "").upper().replace("'", "")) if len(w) > 2 and w not in stop}


def _best_name_match(cands: list[dict], bname: str) -> dict | None:
    """Candidate whose name is really the building's name.

    Accepted when its words are all part of the building's name ("Comfort Suites" in "Comfort Suites
    Kansas City-Liberty"), or it shares 2+ words covering two-thirds of it. Shared single words like
    "Northpoint" in "Northpoint Foundation" vs "NorthPoint Development" are not enough.
    """
    target = _name_words(bname)
    if not target:
        return None
    best, best_score = None, 0.0
    for p in cands:
        words = _name_words(p["name"])
        inter = len(words & target)
        if not words or not inter:
            continue
        cover = inter / len(target)
        ok = (words <= target and cover >= 0.6) or len(target) == 1 or (inter >= 2 and cover >= 0.66) or target <= words
        if not ok:
            continue
        score = cover + 0.01 * inter / len(words)
        if score > best_score:
            best, best_score = p, score
    return best


def pick_anchor(b: dict, tenants: list[dict], pidx) -> tuple[dict | None, str]:
    """The business to call for this building.

    1. A tenant whose name matches the building's own name (ALDI in the 'ALDI' building).
    2. A business just outside the outline whose name matches the building name (listing pin on the parking lot).
    3. The building's own name with no phone (tenants are listed separately) rather than guess a tenant.
    4. The most confident, non-kiosk tenant (phone numbers preferred).
    5. The closest business just outside the building.
    """
    bname = b.get("name") or ""
    if bname:
        best = _best_name_match(tenants, bname)
        if best:
            return best, "business inside building (matches building name)"
        if pidx:
            best = _best_name_match(buildings.businesses_near(b, pidx, settings.outside_match_m * 2), bname)
            if best:
                return best, "business next to building (matches building name)"
    if bname:  # the map names the building but no listing matches: show the name, leave the phone blank
        return {"name": bname, "category": b.get("class") or b.get("subtype") or "", "phone": "", "website": "",
                "address": "", "city": "", "zip": ""}, "building name only (no phone listing)"
    real = [p for p in tenants if not _is_minor(p)]
    real.sort(key=lambda p: (-(p.get("confidence") or 0), not p.get("phone")))
    if real:
        return real[0], "business inside building"
    p = buildings.business_next_to(b, pidx, settings.outside_match_m) if pidx else None
    if p and not _is_minor(p):
        return p, "business next to building"
    return None, "no business found"


def looks_commercial(s: dict) -> bool:
    """Cheap pre-filter before spending money: a business is there, or the array is too big for a house."""
    return bool(s.get("biz_name") or s.get("osm_name") or s["area_m2"] >= settings.min_cluster_m2
                or s["arrays"] >= 3 or s["is_solar_farm"])


def _exclude(s: dict, why: str) -> None:
    s.setdefault("panels", "not_checked")
    s.setdefault("building_type", "")
    s["category"] = ""
    s["tier"] = why
    s["est_panels"] = s["modules"] or est_panels(s["area_m2"])


def run_state(state: str, refresh_map: bool = False, store: Store | None = None) -> list[dict]:
    state = state.upper()
    store = store or Store()
    if refresh_map:
        store.reset_tiles(state)

    # 1. Map: solar arrays (resumable per tile)
    osm.fetch_state_arrays(state, store.done_tiles(), on_tile=store.save_tile)
    arrays = store.arrays(state)
    log.info("%s: %d mapped solar arrays", state, len(arrays))
    sites = build_sites(arrays)

    # Free filters: business nearby? array big enough? then building footprint check.
    place_indexes = attach_businesses(sites)
    candidates = [s for s in sites if looks_commercial(s)]
    cand_ids = {s["site_id"] for s in candidates}
    for s in sites:
        if s["site_id"] not in cand_ids:
            s["land_use"] = "home"  # small array, no business nearby: almost always a house
            _exclude(s, "X - Home, excluded")
    if settings.use_buildings:
        attach_buildings(candidates, place_indexes)
        for s in candidates:
            if s.get("land_use") == "home":
                _exclude(s, "X - Home, excluded")
        candidates = [s for s in candidates if s.get("land_use") != "home"]
    candidates.sort(key=lambda s: (-max(s["area_m2"], s.get("roof_m2", 0) / 4), -s["arrays"]))
    log.info("%s: %d sites, %d look commercial", state, len(sites), len(candidates))

    # Paid steps: decide up front (biggest sites first) which uncached sites get a lookup,
    # so the per-run spending caps always go to the most valuable sites.
    def allot(kind: str, enabled: bool, cap: int, pool: list[dict]) -> set[str]:
        if not enabled:
            return set()
        need = [s["site_id"] for s in pool if store.get(kind, s["site_id"]) is None]
        return set(need[:cap])

    # 2-3. Regrid owner + land use
    regrid_ok = allot("regrid", bool(settings.regrid_token), settings.max_regrid_lookups, candidates)

    def do_regrid(s):
        cached = store.get("regrid", s["site_id"])
        if cached is None and s["site_id"] in regrid_ok:
            cached = parcels.lookup(s["lat"], s["lon"])
            if cached is not None:
                store.put("regrid", s["site_id"], cached)
        if cached:
            s.update(owner=cached.get("owner", ""), owner2=cached.get("owner2", ""),
                     mail_addr=cached.get("mail_addr", ""), mail_city=cached.get("mail_city", ""),
                     mail_state=cached.get("mail_state", ""), mail_zip=cached.get("mail_zip", ""),
                     site_addr=cached.get("site_addr", ""), site_city=cached.get("site_city", ""),
                     site_zip=cached.get("site_zip", ""),
                     parcel_id=cached.get("parcel_id", ""), use_desc=cached.get("use_desc", ""))
            s["land_use"] = parcels.land_use_class(cached)
        else:
            s.setdefault("land_use", "unknown")

    with ThreadPoolExecutor(settings.workers) as ex:
        list(ex.map(do_regrid, candidates))
    for s in candidates:
        if s["land_use"] == "home":
            _exclude(s, "X - Home, excluded")
    candidates = [s for s in candidates if s["land_use"] != "home"]

    # 4. Roof check on the aerial photo
    roof_ok = allot("roof", bool(settings.anthropic_api_key), settings.max_roof_checks, candidates)

    def do_roof(s):
        cached = store.get("roof", s["site_id"])
        if cached is None and s["site_id"] in roof_ok:
            img = imagery.naip_image(s["lat"], s["lon"], cache_key=s["site_id"])
            cached = vision.check_roof(img) if img else {"panels": "unclear", "building_type": "unknown",
                                                         "roof_size": "", "note": "no aerial photo here"}
            if cached.get("panels") != "error":
                store.put("roof", s["site_id"], cached)
        cached = cached or {"panels": "not_checked", "building_type": "unknown", "roof_size": "", "note": ""}
        s.update(panels=cached["panels"], building_type=cached["building_type"],
                 roof_size=cached.get("roof_size", ""), roof_note=cached.get("note", ""))
        if s.get("roof_m2", 0) >= BIG_ROOF_M2:
            s["roof_size"] = "large"

    with ThreadPoolExecutor(settings.workers) as ex:
        list(ex.map(do_roof, candidates))

    # Score
    for s in candidates:
        s["category"] = categorize(s)
        s["tier"] = tier(s)
        s["est_panels"] = s["modules"] or est_panels(s["area_m2"])
    new_ids = store.mark_seen(state, [s["site_id"] for s in candidates])
    for s in candidates:
        s["new_this_run"] = s["site_id"] in new_ids
    log.info("%s: done. %d Regrid lookups and %d roof checks this run", state, len(regrid_ok), len(roof_ok))
    return sites
