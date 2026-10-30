"""Offline tests. Network steps are replaced with fakes built from real Kansas City Northland sites
that were checked by hand on Oct 29, 2026 (e.g. ALDI Gladstone, Oakhill Day School, a house in Parkville)."""
import os

import pytest

from solarleads import buildings, export, geo, parcels, pipeline, places, score, scorecard
from solarleads.config import settings
from solarleads.store import Store


def square(lat, lon, side_m):
    """Closed ring of a side_m x side_m square centered on lat/lon, as Overpass 'geometry' dicts."""
    w, s, e, n = geo.bbox_around(lat, lon, side_m / 2)
    return [{"lat": s, "lon": w}, {"lat": s, "lon": e}, {"lat": n, "lon": e}, {"lat": n, "lon": w}, {"lat": s, "lon": w}]


def arr(osm_id, lat, lon, side_m, **extra):
    ring = [(g["lat"], g["lon"]) for g in square(lat, lon, side_m)]
    a = {"osm_id": osm_id, "lat": lat, "lon": lon, "area_m2": round(geo.polygon_area_m2(ring[:-1]), 1),
         "modules": 0, "location": "roof", "name": "", "operator": "", "plant": False, "state": "MO"}
    a.update(extra)
    return a


# Real Northland locations
ALDI = (39.232291, -94.577591)      # ALDI, 7604 N Oak Trfy, Gladstone (4 rooftop arrays)
OAKHILL = (39.222313, -94.573249)   # Oakhill Day School (4 arrays)
HOUSE = (39.207663, -94.700000)     # a detached house with a small rooftop system (made-up exact spot)
WAREHOUSE = (39.269646, -94.672681) # Platte Industrial Park building (17 arrays)
STRIP = (39.245862, -94.647675)     # Embassy Plaza area: a strip center with several tenants

FAKE_ARRAYS = (
    [arr(f"w{i}", ALDI[0] + i * 0.00008, ALDI[1], 12) for i in range(4)]
    + [arr(f"w1{i}", OAKHILL[0], OAKHILL[1] + i * 0.0001, 10) for i in range(4)]
    + [arr("w900", HOUSE[0], HOUSE[1], 6)]
    + [arr(f"w5{i:02d}", WAREHOUSE[0] + (i % 6) * 0.0001, WAREHOUSE[1] + (i // 6) * 0.0001, 14) for i in range(17)]
    + [arr(f"w7{i}", STRIP[0], STRIP[1] + i * 0.00015, 9) for i in range(3)]
)

M_LAT = 1 / 111_320  # degrees per meter (north-south)


def m_lon(lat):
    import math
    return 1 / (111_320 * math.cos(math.radians(lat)))


def building(bid, lat, lon, half_ns, half_ew, subtype="", cls="", name=""):
    """Fake Overture building: rectangle centered on lat/lon, half-sizes in meters."""
    dl, dn = half_ns * M_LAT, half_ew * m_lon(lat)
    ring = [(lat - dl, lon - dn), (lat - dl, lon + dn), (lat + dl, lon + dn), (lat + dl, lon - dn), (lat - dl, lon - dn)]
    return {"id": bid, "subtype": subtype, "class": cls, "name": name, "height": None,
            "rings": [[list(p) for p in ring]], "area_m2": round(geo.polygon_area_m2(ring[:-1]), 1),
            "lat": lat, "lon": lon, "bbox": [lat - dl, lon - dn, lat + dl, lon + dn]}


ALDI_C = (ALDI[0] + 0.00012, ALDI[1])  # center of the 4 ALDI arrays
FAKE_BUILDINGS = [
    # ALDI store: extends 23 m north and 57 m south of the arrays
    building("b-aldi", ALDI_C[0] - 17 * M_LAT, ALDI_C[1], 40, 35, "commercial", "retail"),
    building("b-oak", OAKHILL[0], OAKHILL[1] + 0.00015, 40, 40, "education", "school"),
    building("b-house", HOUSE[0], HOUSE[1], 7, 7, "residential", "house"),
    building("b-wh", WAREHOUSE[0] + 0.00025, WAREHOUSE[1] + 0.00005, 60, 60, "industrial", "warehouse"),
    building("b-strip", STRIP[0], STRIP[1] + 0.00015, 15, 40, "commercial", "retail", "Embassy Plaza"),
]


FAKE_PLACES = [
    # ALDI's listing point sits 40 m south of the panels, inside the store...
    {"name": "ALDI", "category": "grocery_store", "phone": "+18555552534", "website": "https://aldi.us", "address": "7604 N Oak Trfy",
     "city": "Gladstone", "confidence": 0.95, "lat": ALDI_C[0] - 40 * M_LAT, "lon": ALDI[1]},
    # ...while a gas station across the street is closer (30 m north) but outside the building.
    {"name": "QuikTrip", "category": "gas_station", "phone": "+18165550199", "website": "", "address": "7700 N Oak Trfy",
     "city": "Gladstone", "confidence": 0.95, "lat": ALDI_C[0] + 30 * M_LAT, "lon": ALDI[1]},
    {"name": "Oakhill Day School", "category": "private_school", "phone": "+18164360000", "website": "", "address": "7019 N Cherry St",
     "city": "Gladstone", "confidence": 0.9, "lat": OAKHILL[0], "lon": OAKHILL[1] + 0.0002},
    {"name": "Smith Residence", "category": "residential", "phone": "", "website": "", "address": "", "city": "",
     "confidence": 0.3, "lat": HOUSE[0], "lon": HOUSE[1]},
] + [
    {"name": n, "category": c, "phone": "+1816555010" + str(i), "website": "", "address": "", "city": "Kansas City",
     "confidence": 0.8, "lat": STRIP[0], "lon": STRIP[1] + i * 0.0001}
    for i, (n, c) in enumerate([("Commerce Bank", "bank"), ("Great Clips", "hair_salon"), ("Jimmy John's", "restaurant"), ("UPS Store", "shipping_center")])
]

FAKE_REGRID = {
    "ALDI": {"owner": "ALDI INC", "mail_addr": "PO Box 460049 DEPT 501", "mail_city": "Houston", "mail_state": "TX", "mail_zip": "77056",
             "site_addr": "7604 N OAK TRFY", "site_city": "GLADSTONE", "parcel_id": "13605001100100", "use_desc": "Retail Store",
             "lbcs_activity": "2100", "lbcs_activity_desc": "Shopping, business, or trade activities"},
    "OAKHILL": {"owner": "OAKHILL DAY SCHOOL", "site_addr": "7019 N CHERRY ST", "use_desc": "Exempt - School", "lbcs_activity": "4100",
                "lbcs_activity_desc": "School or library"},
    "STRIP": {"owner": "EMBASSY PLAZA LLC", "use_desc": "Shopping Center", "lbcs_activity": "2100"},
    "WAREHOUSE": {"owner": "PIP INDUSTRIAL LLC", "site_addr": "8137 NW 97TH TER", "use_desc": "Warehouse", "lbcs_activity": "3000",
                  "lbcs_activity_desc": "Industrial, manufacturing, and waste-related activities"},
}


def nearest_key(lat, lon):
    pts = {"ALDI": ALDI, "OAKHILL": OAKHILL, "WAREHOUSE": WAREHOUSE, "HOUSE": HOUSE, "STRIP": STRIP}
    return min(pts, key=lambda k: geo.haversine_m(lat, lon, *pts[k]))


@pytest.fixture
def fake_world(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "out_dir", str(tmp_path / "out"))
    monkeypatch.setattr(settings, "regrid_token", "test-token")
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    calls = {"regrid": 0, "roof": 0}

    def fake_fetch(state, done_tiles=None, on_tile=None):
        if on_tile and f"{state}:t1" not in (done_tiles or set()):
            on_tile(f"{state}:t1", FAKE_ARRAYS)
        return FAKE_ARRAYS

    def fake_regrid(lat, lon):
        calls["regrid"] += 1
        return FAKE_REGRID.get(nearest_key(lat, lon), {})

    def fake_roof(jpeg):
        calls["roof"] += 1
        return {"panels": "yes", "building_type": "commercial", "roof_size": "large" if jpeg == b"WAREHOUSE" else "medium", "note": "rooftop rows"}

    monkeypatch.setattr(pipeline.osm, "fetch_state_arrays", fake_fetch)
    monkeypatch.setattr(places, "places_for_tile", lambda t: FAKE_PLACES)
    monkeypatch.setattr(buildings, "buildings_for_cell", lambda c: FAKE_BUILDINGS)
    monkeypatch.setattr(pipeline.parcels, "lookup", fake_regrid)
    monkeypatch.setattr(pipeline.imagery, "naip_image", lambda lat, lon, cache_key=None: nearest_key(lat, lon).encode())
    monkeypatch.setattr(pipeline.vision, "check_roof", fake_roof)
    return calls


def test_area_and_clustering():
    a = arr("w1", 39.2, -94.6, 10)
    assert 95 < a["area_m2"] < 105  # 10 m x 10 m
    groups = geo.cluster_points(FAKE_ARRAYS, 60)
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 3, 4, 4, 17]


def calls_only(sites):
    return [s for s in sites if not s["tier"].startswith("X")]


def test_full_run_produces_tiered_call_list(fake_world):
    store = Store()
    sites = pipeline.run_state("MO", store=store)
    calls = calls_only(sites)
    by_name = {s.get("biz_name") or s.get("owner"): s for s in calls}
    assert len(calls) == 4 and len(sites) == 5  # the house is excluded for free
    house = [s for s in sites if s["tier"].startswith("X")][0]
    assert house["tier"] == "X - Home, excluded"
    aldi = by_name["ALDI"]
    assert aldi["category"] == "Retail" and aldi["phone"] == "+18555552534"
    assert by_name["Oakhill Day School"]["category"] == "School / college"
    assert by_name["PIP INDUSTRIAL LLC"]["category"] == "Industrial / warehouse"
    assert all(s["tier"] == "1 - Call first" for s in calls)  # all have 3+ arrays and confirmed panels
    assert fake_world["regrid"] == 4 and fake_world["roof"] == 4

    res = export.write_outputs("MO", sites, settings.out_dir)
    assert res["leads"] == 4 and res["sites"] == 5 and os.path.exists(res["xlsx"]) and os.path.exists(res["csv"])

    # Second run: everything cached, nothing paid for again, nothing flagged as new
    again = pipeline.run_state("MO", store=store)
    assert fake_world["regrid"] == 4 and fake_world["roof"] == 4
    assert not any(s.get("new_this_run") for s in again)


def test_business_inside_building_beats_closer_business_outside(fake_world):
    sites = calls_only(pipeline.run_state("MO", store=Store()))
    aldi = next(s for s in sites if s.get("bldg_id") == "b-aldi")
    assert aldi["biz_name"] == "ALDI"            # not the closer QuikTrip across the street
    assert aldi["building_match"] == "business inside building"
    assert aldi["on_building"] and aldi["roof_m2"] > 5000


def test_first_pass_alone_would_pick_wrong_business(fake_world, monkeypatch):
    monkeypatch.setattr(settings, "use_buildings", False)
    sites = calls_only(pipeline.run_state("MO", store=Store()))
    names = {s.get("biz_name") for s in sites}
    assert "QuikTrip" in names and "ALDI" not in names  # the problem the building step fixes


def test_multi_tenant_building(fake_world):
    sites = calls_only(pipeline.run_state("MO", store=Store()))
    strip = next(s for s in sites if s.get("bldg_id") == "b-strip")
    assert strip["multi_tenant"] and strip["tenants"] == 4
    assert strip["category"] == "Multi-tenant building (call property manager)"
    row = export._prep(strip)
    assert row["display_name"] == "Embassy Plaza (4 businesses)"
    assert "Great Clips" in row["tenant_names"]


def test_house_filtered_by_footprint_even_when_array_is_big(fake_world, monkeypatch):
    big_house = [arr("w901", HOUSE[0], HOUSE[1], 12)]  # 144 m2: big enough to pass the size filter
    monkeypatch.setattr(pipeline.osm, "fetch_state_arrays",
                        lambda state, done_tiles=None, on_tile=None: on_tile and on_tile("MO:t2", big_house))
    sites = pipeline.run_state("MO", store=Store())
    house = next(s for s in sites if s.get("bldg_id") == "b-house")
    assert house["tier"] == "X - Home, excluded"
    assert fake_world["roof"] == 0  # no money spent on it


def test_spending_caps(fake_world, monkeypatch):
    monkeypatch.setattr(settings, "max_roof_checks", 1)
    monkeypatch.setattr(settings, "max_regrid_lookups", 2)
    sites = calls_only(pipeline.run_state("MO", store=Store()))
    assert fake_world["roof"] == 1 and fake_world["regrid"] == 2
    unchecked = [s for s in sites if s["panels"] == "not_checked"]
    assert unchecked and all(s["tier"] == "4 - Verify first" for s in unchecked)
    # the biggest site gets the paid checks first
    checked = [s for s in sites if s["panels"] == "yes"]
    assert checked[0]["arrays"] == 17


def test_land_use_rules():
    assert parcels.land_use_class({"use_desc": "Single Family Residential"}) == "home"
    assert parcels.land_use_class({"lbcs_activity": "1100"}) == "home"
    assert parcels.land_use_class({"use_desc": "Apartment Complex"}) == "multifamily"
    assert parcels.land_use_class({"use_desc": "Retail Store"}) == "commercial"
    assert parcels.land_use_class(None) == "unknown"


def test_regrid_response_parsing():
    js = {"parcels": {"type": "FeatureCollection", "features": [{"properties": {"fields": {"owner": "X LLC", "mailadd": "1 Main"}}}]}}
    assert parcels._fields_from_response(js)["owner"] == "X LLC"
    assert parcels._fields_from_response({"features": []}) is None


def test_tiers_and_categories():
    base = {"panels": "yes", "building_type": "commercial", "land_use": "commercial", "area_m2": 100, "arrays": 1}
    assert score.tier(dict(base)) == "2 - Good fit"
    assert score.tier(dict(base, arrays=3)) == "1 - Call first"
    assert score.tier(dict(base, panels="no")) == "X - No panels seen"
    assert score.tier(dict(base, building_type="house", land_use="unknown")) == "X - Home, excluded"
    assert score.tier(dict(base, panels="unclear")) == "4 - Verify first"
    gov = dict(base, owner="KANSAS CITY CITY OF")
    gov["category"] = score.categorize(gov)
    assert gov["category"] == "Government facility" and score.tier(gov) == "3 - Government (bid/procurement)"
    assert score.categorize({"owner": "SCHOOL DISTRICT 74"}) == "School / college"
    assert score.categorize({"biz_category": "innovation_center"}) != "Hotel"  # 'inn' must not match 'innovation'
    assert score.categorize({"biz_category": "hotel"}) == "Hotel"


def test_overture_record_flattening():
    row = {"names": {"primary": "Joe's Tires"}, "categories": {"primary": "tire_shop"}, "phones": ["+18165550100"],
           "websites": None, "addresses": [{"freeform": "1 Main St", "locality": "Liberty"}], "confidence": 0.8,
           "bbox": {"xmin": -94.41, "xmax": -94.41, "ymin": 39.24, "ymax": 39.24}}
    p = places._flatten(row)
    assert p["name"] == "Joe's Tires" and p["phone"] == "+18165550100" and p["city"] == "Liberty"
    assert abs(p["lat"] - 39.24) < 1e-9
    assert places._flatten({"names": None}) is None


def test_overpass_element_parsing():
    from solarleads import osm

    way = {"type": "way", "id": 5, "tags": {"generator:source": "solar", "location": "roof", "generator:solar:modules": "40"},
           "geometry": square(39.2, -94.6, 20)[:-1]}
    a = osm._element_to_array(way)
    assert a["osm_id"] == "w5" and a["modules"] == 40 and 380 < a["area_m2"] < 420
    node = {"type": "node", "id": 7, "lat": 39.1, "lon": -94.5, "tags": {}}
    assert osm._element_to_array(node)["area_m2"] == 0
    rel = {"type": "relation", "id": 9, "tags": {"power": "plant"},
           "members": [{"role": "outer", "geometry": square(39.0, -94.0, 200)[:-1]}]}
    r = osm._element_to_array(rel)
    assert r["plant"] and r["area_m2"] > 39000


def test_vision_reads_tool_answer(monkeypatch):
    from types import SimpleNamespace

    from solarleads import vision

    answer = {"panels": "yes", "building_type": "commercial", "roof_size": "large", "note": "rows on flat roof"}
    fake = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok"), SimpleNamespace(type="tool_use", name="report_roof", input=answer)])))
    monkeypatch.setattr(settings, "anthropic_api_key", "k")
    monkeypatch.setattr(vision, "_get_client", lambda: fake)
    assert vision.check_roof(b"jpg") == answer

    def boom(**kw):
        raise RuntimeError("rate limited")
    monkeypatch.setattr(vision, "_get_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=boom)))
    assert vision.check_roof(b"jpg")["panels"] == "error"  # never crashes the run


def test_combine_states(fake_world, tmp_path):
    sites = pipeline.run_state("MO", store=Store())
    res = export.write_outputs("MO", sites, str(tmp_path / "o"))
    import shutil
    shutil.copy(res["csv"], str(tmp_path / "o" / "solar-leads-KS-2026-10-29.csv"))
    n = export.combine([res["csv"], str(tmp_path / "o" / "solar-leads-KS-2026-10-29.csv")], str(tmp_path / "national"))
    assert n == 8 and os.path.exists(str(tmp_path / "national.xlsx"))


def test_wkb_parsing():
    import struct

    ring = [(-94.6, 39.2), (-94.5, 39.2), (-94.5, 39.3), (-94.6, 39.3), (-94.6, 39.2)]  # (x=lon, y=lat)
    poly = struct.pack("<BII", 1, 3, 1) + struct.pack("<I", len(ring)) + b"".join(struct.pack("<dd", *p) for p in ring)
    out = buildings.parse_wkb(poly)
    assert out[0][0][0] == (39.2, -94.6)
    polyz = struct.pack("<BII", 1, 1003, 1) + struct.pack("<I", len(ring)) + b"".join(struct.pack("<ddd", *p, 10.0) for p in ring)
    assert buildings.parse_wkb(polyz)[0][0][2] == (39.3, -94.5)
    multi = struct.pack("<BI", 1, 6) + struct.pack("<I", 2) + poly + polyz
    assert len(buildings.parse_wkb(multi)) == 2
    assert buildings.point_in_rings(39.25, -94.55, out[0]) and not buildings.point_in_rings(39.35, -94.55, out[0])
    assert 0 < buildings.distance_to_rings_m(39.31, -94.55, out[0]) < 1200


def test_scorecard(fake_world, tmp_path):
    import csv

    key = tmp_path / "key.csv"
    with open(key, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["answer_id", "county", "status", "arrays_mapped", "lat", "lon", "parcel_id",
                                          "site_name", "county_owner", "city"])
        w.writeheader()
        w.writerow(dict(answer_id="A1", county="Clay", status="panels_confirmed", arrays_mapped=4, lat=ALDI[0], lon=ALDI[1],
                        parcel_id="", site_name="ALDI", county_owner="ALDI INC", city="Gladstone"))
        w.writerow(dict(answer_id="A2", county="Clay", status="panels_confirmed", arrays_mapped=4, lat=OAKHILL[0], lon=OAKHILL[1],
                        parcel_id="", site_name="Oakhill Day School", county_owner="OAKHILL DAY SCHOOL", city="Gladstone"))
        w.writerow(dict(answer_id="A3", county="Clay", status="panels_confirmed", arrays_mapped=2, lat=39.30, lon=-94.60,
                        parcel_id="", site_name="Missing Co", county_owner="MISSING LLC", city=""))
        w.writerow(dict(answer_id="A4", county="Platte", status="panels_confirmed", arrays_mapped=1, lat=HOUSE[0], lon=HOUSE[1],
                        parcel_id="", site_name="", county_owner="", city=""))
    rows = export.sort_rows(pipeline.run_state("MO", store=Store()))
    res = scorecard.evaluate(rows, str(key))
    assert res["confirmed_sites"] == 4 and res["found"] == 2 and res["found_rate"] == 50.0
    assert len(res["never_seen"]) == 1 and len(res["excluded_by_tool"]) == 1  # the 'house' was hand-marked commercial
    assert res["owner_rate"] == 100.0 and res["name_rate"] == 100.0
    # the warehouse is inside the hand-checked outline but not in this small key; the strip mall sits
    # outside the outline (a never-checked area), so it no longer counts against the clean rate
    assert res["clean"] == 2 and len(res["unknown_rows"]) == 1
    md = scorecard.to_markdown(res)
    assert "Found rate" in md and "Missing Co" in md


def test_real_answer_key_loads():
    paths = scorecard.benchmarks_for("MO")
    assert paths
    res = scorecard.evaluate([], paths[0])
    assert res["confirmed_sites"] == 180 and res["found"] == 0 and len(res["never_seen"]) == 180


def test_business_just_outside_a_big_building_is_found():
    big = building("b", 39.2, -94.6, 100, 100, "commercial")  # 200 m x 200 m
    idx = places.build_index([{"name": "Edge Cafe", "category": "cafe", "phone": "", "website": "", "address": "",
                               "city": "", "confidence": 0.9, "lat": 39.2 + 110 * M_LAT, "lon": -94.6}])
    assert buildings.business_next_to(big, idx, 25)["name"] == "Edge Cafe"  # 10 m past the north wall
    assert buildings.business_next_to(big, idx, 5) is None


# ---------------------------------------------------------------- lead packs
from solarleads import deliver  # noqa: E402


def _pack_rows(fake_world):
    return export.sort_rows(pipeline.run_state("MO", store=Store()))


def test_cleanup_helpers():
    assert deliver.clean_phone("+18165550199") == "(816) 555-0199"
    assert deliver.clean_phone("816.555.0199") == "(816) 555-0199"
    assert deliver.clean_phone("555-0199") == "" and deliver.clean_phone("0165550199") == ""
    assert deliver.clean_website("https://www.ALDI.us/") == "aldi.us"
    assert deliver.title_case("PIP INDUSTRIAL LLC") == "Pip Industrial LLC"
    assert deliver.title_case("Joe's Tires") == "Joe's Tires"
    assert deliver.job_value(200, 12, 350) == 2400 and deliver.job_value(10, 12, 350) == 350
    a = deliver.score({"tier": "1 - Call first", "est_panels": 500, "phone_clean": "x", "website_clean": "y", "owner": "z"})
    b = deliver.score({"tier": "2 - Good fit", "est_panels": 20})
    assert 80 <= a <= 100 and b < a


def test_package_end_to_end(fake_world, tmp_path, monkeypatch):
    import openpyxl

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "d"))
    rows = _pack_rows(fake_world)
    terr = deliver.resolve_territory("Kansas City", None, 40)
    assert terr["states"] == ["MO", "KS"]
    m = deliver.package(rows, terr, "Acme Exterior", str(tmp_path / "o"))
    assert m["leads"] == 4 and m["with_phone"] >= 2 and m["est_value_per_round"] > 0
    xlsx, crm, teaser = m["files"][:3]
    wb = openpyxl.load_workbook(xlsx)
    assert wb.sheetnames == ["Cover", "Leads", "How to use", "Data dictionary", "Sources & license"]
    leads = wb["Leads"]
    hdr = [c.value for c in leads[1]]
    assert hdr[:4] == ["Lead ID", "Priority", "Score (0-100)", "Business / site"]
    first = {h: c.value for h, c in zip(hdr, leads[2])}
    assert first["Priority"].startswith("A") and first["Phone"] in ("", None) or first["Phone"].startswith("(")
    assert str(first["Est. cleaning job ($)"]).startswith("=ROUND(MAX(Cover!$B$14")
    assert "OpenStreetMap" in wb["Sources & license"]["A2"].value
    import csv as _csv
    with open(crm) as f:
        crm_rows = list(_csv.DictReader(f))
    assert set(["Company", "Phone", "Street", "City", "State", "Zip", "Lead Source"]) <= set(crm_rows[0])
    aldi = next(r for r in crm_rows if r["Company"] == "ALDI")
    assert aldi["Phone"] == "(855) 555-2534" and aldi["Website"] == "aldi.us"
    tw = openpyxl.load_workbook(teaser)["Preview"]
    cells = " ".join(str(c.value) for row in tw.iter_rows() for c in row if c.value)
    assert "ALDI" not in cells and "555-2534" not in cells and "•••" in cells  # names and numbers hidden
    # Lead IDs are stable between months
    m2 = deliver.package(rows, terr, "Acme Exterior", str(tmp_path / "o2"))
    ids = lambda p: [r["Lead ID"] for r in _csv.DictReader(open(p))]  # noqa: E731
    assert ids(crm) == ids(m2["files"][1])


def test_territory_radius_and_exclusivity(fake_world, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "d"))
    rows = _pack_rows(fake_world)
    small = deliver.resolve_territory(None, f"{ALDI[0]},{ALDI[1]}", 2)  # 2 miles around ALDI Gladstone
    m = deliver.package(rows, small, "Acme Exterior", str(tmp_path / "o"))
    assert m["leads"] == 2  # ALDI + Oakhill; the Platte warehouse and strip center are farther out
    with pytest.raises(PermissionError):
        deliver.package(rows, deliver.resolve_territory("Kansas City", None, 40), "Rival Wash Co", str(tmp_path / "o"))
    # same customer can refresh; a demo pack is never recorded; explicit override works
    deliver.package(rows, small, "Acme Exterior", str(tmp_path / "o"))
    deliver.package(rows, small, "Sample", str(tmp_path / "o"))
    deliver.package(rows, small, "Rival Wash Co", str(tmp_path / "o"), allow_overlap=True)
    far = deliver.resolve_territory("Denver", None, 40)
    assert deliver.package(rows, far, "Rival Wash Co", str(tmp_path / "o"))["leads"] == 0


def test_dedupe_and_chain_phone():
    terr = {"name": "t", "lat": 39.2, "lon": -94.6, "radius_mi": 50, "states": []}
    base = {"tier": "2 - Good fit", "est_panels": "40", "arrays": "1", "lat": "39.2", "lon": "-94.6", "state": "MO"}
    rows = [dict(base, site_id="a", address="1 Main St", city="Gladstone", phone="+18165550100", arrays="1"),
            dict(base, site_id="b", address="1 MAIN ST.", city="Gladstone", phone="+18165550100", arrays="3"),
            ] + [dict(base, site_id=f"c{i}", address=f"{i} Oak St", city="KC", phone="+18005550000") for i in range(3)]
    leads = deliver.build_leads(rows, terr)
    assert len(leads) == 4  # the two "1 Main St" rows merged into one lead
    merged = next(r for r in leads if r["address"].startswith("1 M"))
    assert merged["arrays"] == 4 and merged["est_panels"] == 80 and merged["merged_sites"] == 2
    chains = [r for r in leads if r["chain_phone"]]
    assert len(chains) == 3 and "Head-office" in chains[0]["notes_out"]


def test_package_cli(fake_world, tmp_path, monkeypatch):
    from solarleads.__main__ import main

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "d"))
    monkeypatch.setattr(settings, "out_dir", str(tmp_path / "o"))
    sites = pipeline.run_state("MO", store=Store())
    export.write_outputs("MO", sites, str(tmp_path / "o"))
    assert main(["package", str(tmp_path / "o"), "--metro", "kansas city", "--radius", "40"]) == 0
    assert main(["package", str(tmp_path / "o"), "--metro", "Atlantis"]) == 2


def test_title_case_addresses():
    assert deliver.title_case("7604 N OAK TRFY") == "7604 N Oak Trfy"
    assert deliver.title_case("PO Box 460049, Houston TX 77056") == "PO Box 460049, Houston TX 77056"  # mixed case untouched
    assert deliver.title_case("PO BOX 460049 DEPT 501, HOUSTON TX 77056") == "PO Box 460049 Dept 501, Houston, TX 77056".replace(", TX", " TX")


def test_overture_2026_schema():
    row = {"names": {"primary": "Joe's Tires"}, "taxonomy": {"primary": "tire_shop", "hierarchy": ["auto", "tire_shop"]},
           "basic_category": "auto_service", "operating_status": "open", "phones": ["+18165550100"],
           "addresses": [{"freeform": "1 Main St", "locality": "Liberty", "postcode": "64068"}], "confidence": 0.8,
           "bbox": {"xmin": -94.41, "xmax": -94.41, "ymin": 39.24, "ymax": 39.24}}
    p = places._flatten(row)
    assert p["category"] == "tire_shop" and p["zip"] == "64068"
    assert places._flatten(dict(row, operating_status="permanently_closed")) is None


def _pl(name, cat, lat, lon, phone="+18165550100", conf=0.9):
    return {"name": name, "category": cat, "phone": phone, "website": "", "address": "", "city": "", "zip": "",
            "confidence": conf, "lat": lat, "lon": lon}


def test_anchor_rules_from_live_kc_data():
    """Cases seen in the Oct 30 live KC Northland pull."""
    b = building("ace", 39.377, -94.585, 30, 30, "commercial", "retail", "Porters Ace of Smithville")
    inside = [_pl("Redbox", "rental_kiosk", 39.377, -94.585, conf=0.99),
              _pl("Blue Rhino Propane Exchange", "propane", 39.3771, -94.585, conf=0.95),
              _pl("U-Haul Neighborhood Dealer", "truck_rental", 39.3772, -94.585, conf=0.7)]
    # the store's own listing pin sits in the parking lot, 15 m outside the wall
    outside = _pl("Porter's Ace Hardware Smithville", "hardware_store", 39.377 + 45 * M_LAT, -94.585, "+18168737800")
    idx = places.build_index(inside + [outside])
    p, how = pipeline.pick_anchor(b, inside, idx)
    assert p["name"].startswith("Porter's Ace") and p["phone"] == "+18168737800" and "building name" in how

    # Commerce Bank building whose tenants are an insurance agent and a law firm: show the bank, no wrong phone
    bank = building("cb", 39.247, -94.582, 15, 15, "", "", "Commerce Bank")
    tenants = [_pl("Michael Faeth American Family Insurance", "insurance_agency", 39.247, -94.582),
               _pl("Ford & Cooper Attorneys", "attorney", 39.2471, -94.582)]
    p, how = pipeline.pick_anchor(bank, tenants, places.build_index(tenants))
    assert p["name"] == "Commerce Bank" and p["phone"] == "" and how.startswith("building name only")

    # No building name: the most confident real tenant, kiosks skipped
    plain = building("x", 39.2, -94.6, 20, 20, "commercial")
    tenants = [_pl("DigitalMint Bitcoin ATM", "atm", 39.2, -94.6, conf=0.99), _pl("Joe's Tires", "tire_shop", 39.2001, -94.6)]
    assert pipeline.pick_anchor(plain, tenants, places.build_index(tenants))[0]["name"] == "Joe's Tires"


def test_dealership_with_many_listings_is_not_multi_tenant(fake_world, monkeypatch):
    ford = building("b-ford", STRIP[0], STRIP[1] + 0.00015, 15, 40, "commercial", "car", "Gary Crossley Ford")
    listings = [_pl(n, "auto_dealer", STRIP[0], STRIP[1] + i * 0.0001) for i, n in
                enumerate(["Gary Crossley Ford", "Quick Lane", "Ford Parts", "Auto's by Jason"])]
    monkeypatch.setattr(buildings, "buildings_for_cell", lambda c: FAKE_BUILDINGS[:-1] + [ford])
    monkeypatch.setattr(places, "places_for_tile", lambda t: FAKE_PLACES[:4] + listings)
    sites = calls_only(pipeline.run_state("MO", store=Store()))
    s = next(x for x in sites if x.get("bldg_id") == "b-ford")
    assert s["biz_name"] == "Gary Crossley Ford" and not s.get("multi_tenant") and s["tenants"] == 4


def test_building_name_matching_edge_cases():
    m = pipeline._best_name_match
    cands = lambda *ns: [{"name": n} for n in ns]  # noqa: E731
    assert m(cands("Northpoint Foundation"), "NorthPoint Development") is None
    assert m(cands("Holiday Inn Express & Suites Kansas City-Liberty", "Comfort Suites"),
             "Comfort Suites Kansas City-Liberty")["name"] == "Comfort Suites"
    assert m(cands("The Co-op at Shoal Creek"), "Shoal Creek Community Church") is None
    assert m(cands("U-Haul Neighborhood Dealer"), "U-Haul")["name"] == "U-Haul Neighborhood Dealer"
    assert m(cands("Porters Ace of Smithville", "Redbox"), "Porters Ace of Smithville")["name"] == "Porters Ace of Smithville"
    assert m(cands("MCC-Maple Woods Veterinary Technology"), "Veterinary Technology") is not None


def test_campus_merges_by_owner_but_city_buildings_do_not():
    terr = {"name": "t", "lat": 39.2, "lon": -94.6, "radius_mi": 50, "states": []}
    base = {"tier": "2 - Good fit", "est_panels": "50", "arrays": "1", "state": "MO", "address": "NE Barry Rd", "city": "Kansas City"}
    college = [dict(base, site_id=f"m{i}", lat=str(39.246 + i * 0.0005), lon="-94.548",
                    owner="JUNIOR COLLEGE DISTRICT OF METROPOLITAN") for i in range(5)]
    city = [dict(base, site_id=f"f{i}", lat=str(39.14 + i * 0.002), lon="-94.57", owner="NORTH KANSAS CITY CITY OF",
                 tier="3 - Government (bid/procurement)") for i in range(2)]
    leads = deliver.build_leads(college + city, terr)
    assert len(leads) == 3
    campus = next(r for r in leads if r.get("merged_sites"))
    assert campus["merged_sites"] == 5 and campus["est_panels"] == 250 and "5 buildings" in campus["notes_out"]


def test_categories_from_live_kc_data():
    c = score.categorize
    assert c({"owner": "BOCADA 8050 N CHURCH ROAD LLC", "biz_category": "auto_dealer", "biz_name": "Gary Crossley Ford"}) == "Auto / equipment"
    assert c({"owner": "BOCADA 8050 N CHURCH ROAD LLC"}) != "Church"
    assert c({"owner": "CATHOLIC DIOCESE OF KC-ST JOSEPH", "biz_category": "religious_school"}) == "School / college"
    assert c({"owner": "CATHOLIC DIOCESE OF KC-ST JOSEPH"}) == "Church"
    assert c({"biz_category": "food_beverage_distributor", "biz_name": "Arrowhead Specialty Meats"}) == "Industrial / warehouse"
    assert c({"biz_category": "chemical_plant"}) == "Industrial / warehouse"
    assert c({"biz_category": "glass_and_mirror_sales_service"}) == "Contractor / trades"
    assert c({"biz_category": "orthopedics"}) == "Medical / vet / dental"
    assert c({"biz_category": "discount_store", "biz_name": "Aldi"}) == "Retail"
    assert c({"owner": "SCHOOL DISTRICT 74"}) == "School / college"
    assert c({"owner": "KANSAS CITY CITY OF", "biz_category": "fire_station"}) == "Government facility"
    assert c({"biz_category": "fire_station"}) == "Government facility"


def test_vague_listing_on_industrial_parcel():
    c = score.categorize
    assert c({"biz_category": "b2b_service", "biz_name": "Atlas Tube", "use_desc": "Industrial"}) == "Industrial / warehouse"
    assert c({"biz_category": "b2b_service", "biz_name": "WarehouseQuote"}) == "Industrial / warehouse"  # name says it
    assert c({"biz_category": "b2b_service", "biz_name": "Acme"}) == "Office"
    assert c({"biz_category": "hardware_store", "use_desc": "Industrial"}) == "Retail"


def test_coating_pack(fake_world, tmp_path, monkeypatch):
    import openpyxl

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "d"))
    rows = _pack_rows(fake_world)
    terr = deliver.resolve_territory("Kansas City", None, 40)
    m = deliver.package(rows, terr, "Sample", str(tmp_path / "o"), offer="coating")
    assert m["offer"] == "coating" and m["price_per_panel"] == 6.0
    wb = openpyxl.load_workbook(m["files"][0])
    assert wb["Cover"]["A1"].value.startswith("Solar Panel Coating Lead Pack")
    labels = [wb["Cover"].cell(r, 1).value for r in range(4, 20)]
    assert labels[9:15] == ["Price per panel used for estimates", "Minimum job used for estimates", "Watts per panel",
                            "Yearly output per kW (kWh)", "Power price ($/kWh)", "Energy lost to dirt (share of output)"]
    hdr = [c.value for c in wb["Leads"][1]]
    for h in ("Est. coating job ($)", "Est. system size (kW)", "Est. energy lost to dirt ($/yr)", "Soiling risk"):
        assert h in hdr
    row2 = {h: c.value for h, c in zip(hdr, wb["Leads"][2])}
    assert row2["Est. system size (kW)"].startswith("=ROUND(") and "Cover!$B$15" in row2["Est. system size (kW)"]
    assert "Cover!$B$18" in row2["Est. energy lost to dirt ($/yr)"]
    guide = " ".join(str(c.value) for c in wb["How to use"]["A"] if c.value)
    assert "warranty" in guide.lower() and "3-5%" in guide
    teaser = " ".join(str(c.value) for row in openpyxl.load_workbook(m["files"][2])["Preview"].iter_rows() for c in row if c.value)
    assert "coating value" in teaser and "lose to dirty panels" in teaser
    # the industrial warehouse ranks as High soiling risk and carries the coating tip
    import csv as _csv
    crm = list(_csv.DictReader(open(m["files"][1])))
    wh = next(r for r in crm if r["Industry"] == "Industrial / warehouse")
    assert "soiling risk High" in wh["Description"]


def test_cleaning_pack_unchanged_by_coating_option(fake_world, tmp_path, monkeypatch):
    import openpyxl

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "d"))
    m = deliver.package(_pack_rows(fake_world), deliver.resolve_territory("Kansas City", None, 40), "Sample", str(tmp_path / "o"))
    hdr = [c.value for c in openpyxl.load_workbook(m["files"][0])["Leads"][1]]
    assert "Est. cleaning job ($)" in hdr and "Soiling risk" not in hdr and m["price_per_panel"] == 12.0


def test_campus_weak_and_multitenant_names():
    # individual practitioners / associations rank below the real business in a building
    assert pipeline._is_weak({"name": "Dr. Dustin Woyski DO"}) and pipeline._is_weak({"name": "North American Corriente Association"})
    assert not pipeline._is_weak({"name": "Starbucks"}) and not pipeline._is_weak({"name": "Dover Animal Hospital"})
    # a multi-tenant building with no name or owner is labelled by the tenants nearest the panels
    r = export._prep({"multi_tenant": True, "tenants": 5, "tenant_names": "Gladstone Animal Clinic, Subway, Nail Bar",
                      "lat": 39.2384, "lon": -94.5754})
    assert r["display_name"] == "Gladstone Animal Clinic / Subway +3 more (multi-tenant)"
    # campus buildings sharing the college's phone become one lead named for the college
    terr = {"name": "t", "lat": 39.2, "lon": -94.6, "radius_mi": 50, "states": []}
    base = {"tier": "4 - Verify first", "est_panels": "40", "arrays": "2", "lat": "39.2", "lon": "-94.6", "state": "MO",
            "phone": "+18166044000"}
    rows = [dict(base, site_id=f"m{i}", display_name=f"MCC-Maple Woods – {b}", lon=str(-94.6 + i * 0.001))
            for i, b in enumerate(["Student Center", "Learning Resources", "Facilities"])]
    leads = deliver.build_leads(rows, terr)
    assert len(leads) == 1 and leads[0]["merged_sites"] == 3
    assert leads[0]["display_name"] == "MCC-Maple Woods"


def test_campus_name_is_the_shared_institution(monkeypatch):
    near = [{"name": "Maple Woods Language-Arts Building", "category": "college_university", "phone": "", "lat": 0, "lon": 0},
            {"name": "Maple Valley School", "category": "elementary_school", "phone": "+18165550101", "lat": 0, "lon": 0},
            {"name": "MCC-Maple Woods | Veterinary Technology", "category": "veterinarian", "phone": "", "lat": 0, "lon": 0},
            {"name": "MCC-Maple Woods | Library", "category": "college_university", "phone": "+18166044000", "lat": 0, "lon": 0}]
    monkeypatch.setattr(pipeline.buildings, "businesses_near", lambda b, idx, m: near)
    inst = pipeline._institution_near({"name": "Student Center"}, object(), "Student Center")
    assert inst["name"] == "MCC-Maple Woods"
    assert pipeline._is_weak({"name": "Randy Beale", "category": "real_estate_agent"})
