"""Turn raw state output into a sellable, customer-ready lead pack for one territory.

    python -m solarleads package output/ --metro "Kansas City" --radius 40 --customer "Acme Exterior Cleaning"

Produces, in output/packs/<customer>-<territory>/:
  - <name>.xlsx       customer workbook: Cover, Leads, How to use, Data dictionary, Sources & license
  - <name>-crm.csv    CRM-ready import (standard column names: Company, Phone, Street, City, State, Zip...)
  - <name>-teaser.xlsx  free preview for sales: territory totals + top 10 leads with names/phones hidden
  - manifest.json     what was delivered, for your records

Territories sold to a customer are recorded in data/territories.json; packaging an overlapping territory
for a different customer is refused unless --allow-overlap is given (exclusivity protection).
"""
import csv
import hashlib
import json
import math
import os
import re
from datetime import date

from .config import settings
from .geo import haversine_m

M_PER_MI = 1609.344

# Metro centers (downtown) and the states the default radius reaches into.
METROS = {
    "New York": (40.7128, -74.0060, "NY NJ CT"), "Los Angeles": (34.0522, -118.2437, "CA"),
    "Chicago": (41.8781, -87.6298, "IL IN WI"), "Dallas": (32.7767, -96.7970, "TX"),
    "Houston": (29.7604, -95.3698, "TX"), "Washington DC": (38.9072, -77.0369, "DC MD VA"),
    "Philadelphia": (39.9526, -75.1652, "PA NJ DE"), "Miami": (25.7617, -80.1918, "FL"),
    "Atlanta": (33.7490, -84.3880, "GA"), "Boston": (42.3601, -71.0589, "MA NH RI"),
    "Phoenix": (33.4484, -112.0740, "AZ"), "San Francisco": (37.7749, -122.4194, "CA"),
    "Riverside": (33.9806, -117.3755, "CA"), "Detroit": (42.3314, -83.0458, "MI"),
    "Seattle": (47.6062, -122.3321, "WA"), "Minneapolis": (44.9778, -93.2650, "MN WI"),
    "San Diego": (32.7157, -117.1611, "CA"), "Tampa": (27.9506, -82.4572, "FL"),
    "Denver": (39.7392, -104.9903, "CO"), "St. Louis": (38.6270, -90.1994, "MO IL"),
    "Baltimore": (39.2904, -76.6122, "MD"), "Orlando": (28.5383, -81.3792, "FL"),
    "Charlotte": (35.2271, -80.8431, "NC SC"), "San Antonio": (29.4241, -98.4936, "TX"),
    "Portland": (45.5152, -122.6784, "OR WA"), "Sacramento": (38.5816, -121.4944, "CA"),
    "Pittsburgh": (40.4406, -79.9959, "PA"), "Austin": (30.2672, -97.7431, "TX"),
    "Las Vegas": (36.1699, -115.1398, "NV"), "Cincinnati": (39.1031, -84.5120, "OH KY IN"),
    "Kansas City": (39.0997, -94.5786, "MO KS"), "Columbus": (39.9612, -82.9988, "OH"),
    "Indianapolis": (39.7684, -86.1581, "IN"), "Cleveland": (41.4993, -81.6944, "OH"),
    "San Jose": (37.3382, -121.8863, "CA"), "Nashville": (36.1627, -86.7816, "TN"),
    "Raleigh": (35.7796, -78.6382, "NC"), "Salt Lake City": (40.7608, -111.8910, "UT"),
    "Albuquerque": (35.0844, -106.6504, "NM"), "Tucson": (32.2226, -110.9747, "AZ"),
    "Honolulu": (21.3069, -157.8583, "HI"), "Omaha": (41.2565, -95.9345, "NE IA"),
    "Oklahoma City": (35.4676, -97.5164, "OK"), "Jacksonville": (30.3322, -81.6557, "FL"),
    "Milwaukee": (43.0389, -87.9065, "WI"), "Louisville": (38.2527, -85.7585, "KY IN"),
    "Memphis": (35.1495, -90.0490, "TN MS AR"), "New Orleans": (29.9511, -90.0715, "LA"),
}

TIER_BASE = {"1": 60, "2": 42, "3": 25, "4": 15}

# What the pack's buyer sells. Changes wording, default prices, extra columns and the call guide.
OFFERS = {
    "cleaning": {
        "job_label": "Est. cleaning job ($)",
        "cover_value": "Estimated cleaning value per round",
        "teaser_value": "estimated cleaning value per round (recurring)",
        "price": 12.0, "minimum": 350.0, "energy": False,
        "title": "Commercial Solar Lead Pack",
    },
    "coating": {
        "job_label": "Est. coating job ($)",
        "cover_value": "Estimated coating value (one-time jobs)",
        "teaser_value": "estimated coating value (one-time jobs)",
        "price": 6.0, "minimum": 500.0, "energy": True,
        "title": "Solar Panel Coating Lead Pack",
    },
}
# Energy assumptions for the "lost to dirt" estimate (blue inputs on the Cover sheet, buyer can change them).
ENERGY_DEFAULTS = {"watts_per_panel": 400, "kwh_per_kw_year": 1300, "price_per_kwh": 0.12, "soiling_loss": 0.03}
HIGH_DUST = ("Industrial / warehouse", "Auto / equipment", "Farm / agricultural", "Contractor / trades")


def energy_lost(panels: float) -> int:
    """$ per year lost to dirt, rounded the same way as the workbook formulas so every total matches."""
    e = ENERGY_DEFAULTS
    kw = round(panels * e["watts_per_panel"] / 1000, 1)
    return int(round(kw * e["kwh_per_kw_year"] * e["soiling_loss"] * e["price_per_kwh"]))


def soiling_risk(row: dict) -> str:
    """Rough guide to how fast the panels get dirty, from what the building is used for and its size."""
    cat = str(row.get("category", ""))
    btype = str(row.get("bldg_type", "") or row.get("building_type", "")).lower()
    if cat in HIGH_DUST or "industrial" in btype or "warehouse" in btype or "farm" in btype:
        return "High"
    if _num(row.get("est_panels")) < 50:
        return "Low"
    return "Medium"
PRIORITY = {"1": "A - Call first", "2": "B - Good fit", "3": "C - Government / bid", "4": "D - Verify first"}


# --- cleanup --------------------------------------------------------------------------------
def clean_phone(raw: str) -> str:
    d = re.sub(r"\D", "", raw or "")
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    if len(d) != 10 or d[0] in "01":
        return ""
    return f"({d[:3]}) {d[3:6]}-{d[6:]}"


def clean_website(raw: str) -> str:
    w = (raw or "").strip()
    if not w:
        return ""
    w = re.sub(r"^https?://", "", w, flags=re.I).rstrip("/")
    w = re.sub(r"^www\.", "", w, flags=re.I)
    return w.lower() if " " not in w else ""


def title_case(s: str) -> str:
    keep = {"LLC", "LP", "INC", "II", "III", "IV", "PC", "PLLC", "LLP", "USA", "US", "YMCA", "UPS", "KC", "NE", "NW",
            "SE", "SW", "N", "S", "E", "W", "PO", "ALDI", "CVS", "QT", "BP",
            "MO", "KS", "TX", "CA", "FL", "IL", "NY", "GA", "AZ", "CO", "OH", "TN", "NC", "SC", "MN", "WA", "OR", "RI",
            "NJ", "PA", "MI", "VA", "MD", "MA", "WI", "IN", "KY", "LA", "OK", "NV", "UT", "NM", "NE", "IA", "AR", "MS",
            "AL", "DE", "DC", "CT", "NH", "VT", "ME", "WV", "ID", "MT", "WY", "ND", "SD", "HI", "AK"}
    if not s or s != s.upper() or not any(ch.isalpha() for ch in s):
        return s or ""
    return " ".join(w if w in keep else w.capitalize() for w in s.split())


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _norm_addr(a: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (a or "").lower())


def lead_id(row: dict) -> str:
    """Stable id for a site across monthly refreshes (so customers can update their CRM)."""
    key = row.get("site_id") or f"{row.get('lat')},{row.get('lon')}"
    return "SL-" + hashlib.sha1(key.encode()).hexdigest()[:8].upper()


# --- scoring --------------------------------------------------------------------------------
def score(row: dict, offer: str = "cleaning") -> int:
    """0-100: confirmed panels and size first, then how reachable the lead is (coating: dusty sites rank higher)."""
    base = TIER_BASE.get(str(row.get("tier", "4"))[0], 10)
    panels = _num(row.get("est_panels"))
    size = min(20, int(round(6 * math.log10(1 + panels))))  # 10 panels≈6, 100≈12, 1000≈18
    reach = (10 if row.get("phone_clean") else 0) + (4 if row.get("website_clean") else 0) + (4 if row.get("owner") else 0)
    if row.get("chain_phone"):
        reach -= 4
    fresh = 2 if row.get("new_this_run") else 0
    dust = {"High": 5, "Medium": 2}.get(row.get("soil_risk", ""), 0) if offer == "coating" else 0
    return max(0, min(100, base + size + reach + fresh + dust))


def job_value(panels: float, price_per_panel: float, minimum: float) -> int:
    return int(round(max(minimum, panels * price_per_panel) / 50) * 50) if panels else int(minimum)


# --- territory ------------------------------------------------------------------------------
def resolve_territory(metro: str | None, center: str | None, radius_mi: float) -> dict:
    if metro:
        match = next((k for k in METROS if k.lower() == metro.strip().lower()), None)
        if not match:
            raise ValueError(f"Unknown metro '{metro}'. Known: {', '.join(sorted(METROS))}. Or use --center LAT,LON")
        lat, lon, states = METROS[match]
        return {"name": match, "lat": lat, "lon": lon, "radius_mi": radius_mi, "states": states.split()}
    if center:
        lat, lon = (float(x) for x in center.split(","))
        return {"name": f"{lat:.3f},{lon:.3f}", "lat": lat, "lon": lon, "radius_mi": radius_mi, "states": []}
    raise ValueError("Give --metro or --center")


def _registry_path() -> str:
    return os.path.join(settings.data_dir, "territories.json")


def check_exclusive(territory: dict, customer: str) -> list[dict]:
    """Other customers whose sold territory overlaps this one."""
    try:
        with open(_registry_path()) as f:
            sold = json.load(f)
    except (OSError, ValueError):
        return []
    clashes = []
    for t in sold:
        if t["customer"].lower() == customer.lower():
            continue
        d = haversine_m(t["lat"], t["lon"], territory["lat"], territory["lon"]) / M_PER_MI
        if d < t["radius_mi"] + territory["radius_mi"]:
            clashes.append(t)
    return clashes


def record_sale(territory: dict, customer: str) -> None:
    path = _registry_path()
    try:
        with open(path) as f:
            sold = json.load(f)
    except (OSError, ValueError):
        sold = []
    sold = [t for t in sold if not (t["customer"].lower() == customer.lower() and t["name"] == territory["name"])]
    sold.append({"customer": customer, "name": territory["name"], "lat": territory["lat"], "lon": territory["lon"],
                 "radius_mi": territory["radius_mi"], "since": date.today().isoformat()})
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(sold, f, indent=2)


# --- build the pack -------------------------------------------------------------------------
def build_leads(rows: list[dict], territory: dict, price_per_panel: float = 12.0, minimum_job: float = 350.0,
                include_verify: bool = True, offer: str = "cleaning") -> list[dict]:
    keep = []
    for r in rows:
        tier = str(r.get("tier", "X"))
        if tier.startswith("X") or (tier.startswith("4") and not include_verify):
            continue
        lat, lon = _num(r.get("lat"), None), _num(r.get("lon"), None)
        if lat is None or lon is None:
            continue
        dist = haversine_m(lat, lon, territory["lat"], territory["lon"]) / M_PER_MI
        if dist > territory["radius_mi"]:
            continue
        r = dict(r)
        r["distance_mi"] = round(dist, 1)
        r["address"] = title_case(r.get("address", ""))
        r["city"] = title_case(r.get("city", ""))
        r["owner_mail"] = ", ".join(title_case(part) for part in str(r.get("owner_mail", "")).split(", "))
        r["phone_clean"] = clean_phone(r.get("phone", ""))
        r["website_clean"] = clean_website(r.get("website", ""))
        keep.append(r)

    # De-duplicate: one row per property. Rows merge when they share a numbered street address, or the same
    # (non-government) owner within 500 m (a campus or a business park with one landlord). Merged rows add up
    # their arrays and panels and keep the best name / phone.
    GOV = ("CITY OF", "COUNTY", "STATE OF", "UNITED STATES", "TOWN OF", "VILLAGE OF")
    groups: list[list[dict]] = []
    by_addr: dict[str, list[dict]] = {}
    for r in keep:
        addr = (r.get("address") or "").strip()
        k = _norm_addr(addr) + "|" + _norm_addr(r.get("city", "")) if addr[:1].isdigit() else None
        owner = (r.get("owner") or "").upper().strip()
        target = by_addr.get(k) if k else None
        if target is None and owner and not any(g in owner for g in GOV):
            target = next((g for g in groups if (g[0].get("owner") or "").upper().strip() == owner and
                           haversine_m(_num(g[0]["lat"]), _num(g[0]["lon"]), _num(r["lat"]), _num(r["lon"])) <= 500), None)
        # Campus buildings ("Institution – Building") with the same phone within 500 m: one customer.
        # (Limited to campus rows so nearby chain stores sharing a head-office number stay separate.)
        if target is None and r["phone_clean"] and " – " in str(r.get("display_name", "")):
            target = next((g for g in groups if any(x["phone_clean"] == r["phone_clean"] for x in g) and
                           haversine_m(_num(g[0]["lat"]), _num(g[0]["lon"]), _num(r["lat"]), _num(r["lon"])) <= 500), None)
        if target is None:
            target = []
            groups.append(target)
        target.append(r)
        if k:
            by_addr[k] = target
    leads = []
    for g in groups:
        g.sort(key=lambda r: (bool(r["phone_clean"]), str(r.get("tier", "9"))[0] == "1", _num(r.get("arrays")),
                              _num(r.get("est_panels"))), reverse=True)
        top = dict(g[0])
        if len(g) > 1:
            top["arrays"] = sum(int(_num(r.get("arrays"))) for r in g)
            top["est_panels"] = sum(int(_num(r.get("est_panels"))) for r in g)
            top["tier"] = min((str(r.get("tier", "4")) for r in g), key=lambda t: t[0])
            top["merged_sites"] = len(g)
            # campus rows are named "Institution – Building": the merged lead is the institution
            heads = {str(r.get("display_name", "")).split(" – ")[0] for r in g if " – " in str(r.get("display_name", ""))}
            if len(heads) == 1 and " – " in str(top.get("display_name", "")):
                top["display_name"] = heads.pop()
        leads.append(top)
    # Chain / corporate numbers: the same phone on 3+ sites is a head-office line, not the site.
    counts: dict[str, int] = {}
    for r in leads:
        if r["phone_clean"]:
            counts[r["phone_clean"]] = counts.get(r["phone_clean"], 0) + 1
    for r in leads:
        r["chain_phone"] = counts.get(r["phone_clean"], 0) >= 3
        panels = _num(r.get("est_panels"))
        r["job_value"] = job_value(panels, price_per_panel, minimum_job)
        r["soil_risk"] = soiling_risk(r)
        r["score"] = score(r, offer)
        r["priority"] = PRIORITY.get(str(r.get("tier", "4"))[0], "D - Verify first")
        r["lead_id"] = lead_id(r)
        r["name_out"] = title_case(r.get("display_name", "")) or f"{r.get('category', 'Commercial site')} (name unknown)"
        r["owner_out"] = title_case(r.get("owner", ""))
        notes = []
        if r["chain_phone"]:
            notes.append("Head-office number: ask for the facilities manager for this location")
        if not r["phone_clean"]:
            notes.append("No phone listed: use owner mailing address or visit")
        if "Multi-tenant" in str(r.get("category", "")):
            tenant = r.get("biz_name") or ""
            if r["phone_clean"] and tenant:
                notes.append(f"Several businesses share this roof; the phone is for {tenant}, a tenant: ask who "
                             "manages the property. The landlord / property manager is the buyer")
            else:
                notes.append("Several businesses share this roof: the landlord / property manager is the buyer")
        if str(r.get("tier", "")).startswith("4"):
            notes.append("Panels not confirmed on aerial photo: check the satellite link before calling")
        if str(r.get("tier", "")).startswith("3"):
            notes.append("Government building: usually bought through bids or an approved-vendor list")
        if r.get("merged_sites"):
            notes.append(f"{r['merged_sites']} buildings on one property (campus / park): one decision-maker")
        if offer == "coating" and r["soil_risk"] == "High":
            notes.append("Dusty use (industrial / vehicles): panels soil fast, strong coating fit")
        r["notes_out"] = "; ".join(notes)
    leads.sort(key=lambda r: (-r["score"], r["distance_mi"]))
    return leads


CUSTOMER_COLS = [
    ("Lead ID", "lead_id", 12), ("Priority", "priority", 18), ("Score (0-100)", "score", 9),
    ("Business / site", "name_out", 34), ("Type", "category", 22), ("Phone", "phone_clean", 15),
    ("Website", "website_clean", 24), ("Street", "address", 28), ("City", "city", 16), ("State", "state", 6),
    ("Zip", "zip", 7), ("Miles from center", "distance_mi", 9), ("Solar arrays", "arrays", 8),
    ("Est. panels", "est_panels", 9), ("Est. cleaning job ($)", "job_value", 11), ("Panels confirmed", "panels", 11),
    ("Businesses in building", "tenant_names", 30), ("Property owner", "owner_out", 30),
    ("Owner mailing address", "owner_mail", 34), ("Satellite view", "map_url", 11), ("Tips", "notes_out", 44),
    ("New this month", "new_this_run", 8), ("Status", "_status", 16), ("Notes", "_notes", 30),
]

def customer_cols(offer: str = "cleaning") -> list[tuple[str, str, int]]:
    cols = [(OFFERS[offer]["job_label"] if k == "job_value" else c, k, w) for c, k, w in CUSTOMER_COLS]
    if OFFERS[offer]["energy"]:
        i = [k for _, k, _ in cols].index("job_value") + 1
        cols[i:i] = [("Est. system size (kW)", "_kw", 9), ("Est. energy lost to dirt ($/yr)", "_loss", 12),
                     ("Soiling risk", "soil_risk", 9)]
    return cols


CRM_COLS = [
    ("Lead ID", "lead_id"), ("Company", "name_out"), ("Phone", "phone_clean"), ("Website", "website_clean"),
    ("Street", "address"), ("City", "city"), ("State", "state"), ("Zip", "zip"), ("Lead Source", "_source"),
    ("Lead Status", "_crm_status"), ("Industry", "category"), ("Priority", "priority"), ("Lead Score", "score"),
    ("Estimated Value", "job_value"), ("Property Owner", "owner_out"), ("Owner Mailing Address", "owner_mail"),
    ("Latitude", "lat"), ("Longitude", "lon"), ("Description", "_desc"),
]

DICTIONARY = [
    ("Lead ID", "Stays the same every month, so updates can be matched to leads already in your CRM."),
    ("Priority", "A = solar panels confirmed and a large system; B = panels confirmed; C = government building "
                 "(usually bid work); D = panels mapped but not confirmed on the aerial photo yet."),
    ("Score (0-100)", "Overall ranking: confirmed panels and system size count most, then whether there is a phone, "
                      "website and owner on file. Work the list from the top down."),
    ("Est. panels", "From the size of the mapped arrays (about 1.9 m² per panel) or the panel count when mapped."),
    ("Est. cleaning job ($)", "Est. panels × the price per panel on the Cover sheet, with a minimum job. A rough "
                              "starting point for quoting, not a quote."),
    ("Panels confirmed", "yes = panels seen on a USDA aerial photo; unclear / not_checked = look at the satellite link."),
    ("Businesses in building", "Every business listed inside the same building, for shopping centers and offices."),
    ("Property owner", "Owner of record from county parcel data. Often a holding company: the decision-maker is "
                       "usually the facilities or property manager."),
    ("Satellite view", "Opens Google Maps satellite view on the panels."),
    ("Tips", "What to watch for when calling this lead."),
]

COATING_DICTIONARY = [
    ("Est. coating job ($)", "Est. panels × your coating price per panel on the Cover sheet, with a minimum job. "
                             "A rough starting point for quoting, not a quote."),
    ("Est. system size (kW)", "Est. panels × watts per panel on the Cover sheet."),
    ("Est. energy lost to dirt ($/yr)", "System size × yearly output per kW × soiling loss × power price, all from the "
                                        "Cover sheet. Use it as the opener: what dirty panels cost this owner each year."),
    ("Soiling risk", "High = dusty use (industrial, vehicles, trades, farm); Medium = other large commercial roofs; "
                     "Low = small systems. Check the satellite view for gravel lots, rail, highways, trees and birds."),
]


def _write_customer_xlsx(leads, territory, customer, path, price_per_panel, minimum_job, brand, offer="cleaning"):
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    F = "Arial"
    navy, light = "1F3A5F", "EEF3F8"
    wb = openpyxl.Workbook()
    cover = wb.active
    cover.title = "Cover"
    n = len(leads) + 1
    prof = OFFERS[offer]
    cols = customer_cols(offer)
    keys = [k for _, k, _ in cols]
    L = lambda k: get_column_letter(keys.index(k) + 1)  # noqa: E731
    cover["A1"] = f"{prof['title']} — {territory['name']}"
    cover["A1"].font = Font(name=F, bold=True, size=18, color=navy)
    demo = customer.lower() in ("sample", "demo", "teaser")
    who = f"Sample pack from {brand}" if demo else f"Prepared for {customer} by {brand}"
    cover["A2"] = f"{who} · {date.today().strftime('%B %d, %Y')}"
    cover["A2"].font = Font(name=F, size=11, color="555555")
    rows = [
        ("Territory", f"{territory['radius_mi']:g} miles around {territory['name']}"),
        ("Total leads", "=COUNTA(Leads!A2:A{})".format(n)),
        ("A - Call first", f'=COUNTIF(Leads!B2:B{n},"A - Call first")'),
        ("B - Good fit", f'=COUNTIF(Leads!B2:B{n},"B - Good fit")'),
        ("C - Government / bid", f'=COUNTIF(Leads!B2:B{n},"C - Government / bid")'),
        ("D - Verify first", f'=COUNTIF(Leads!B2:B{n},"D - Verify first")'),
        ("Leads with a phone number", f'=COUNTIF(Leads!{L("phone_clean")}2:{L("phone_clean")}{n},"?*")'),
        ("Estimated panels in territory", f"=SUM(Leads!{L('est_panels')}2:{L('est_panels')}{n})"),
        (prof["cover_value"], f"=SUM(Leads!{L('job_value')}2:{L('job_value')}{n})"),
        ("Price per panel used for estimates", price_per_panel),
        ("Minimum job used for estimates", minimum_job),
    ]
    if prof["energy"]:
        e = ENERGY_DEFAULTS
        rows += [
            ("Watts per panel", e["watts_per_panel"]),
            ("Yearly output per kW (kWh)", e["kwh_per_kw_year"]),
            ("Power price ($/kWh)", e["price_per_kwh"]),
            ("Energy lost to dirt (share of output)", e["soiling_loss"]),
            ("Est. energy lost to dirt in territory ($/yr)", f"=SUM(Leads!{L('_loss')}2:{L('_loss')}{n})"),
        ]
    INPUTS = ("Price", "Minimum", "Watts", "Yearly output", "Power price", "Energy lost to dirt (")
    for i, (k, v) in enumerate(rows, start=4):
        cover.cell(i, 1, k).font = Font(name=F, bold=True)
        c = cover.cell(i, 2, v)
        c.font = Font(name=F, color="0000FF" if k.startswith(INPUTS) else "000000")
        if k == "Power price ($/kWh)":
            c.number_format = "$0.00"
        elif k.startswith("Energy lost to dirt ("):
            c.number_format = "0%"
        elif k.startswith(("Estimated cleaning", "Estimated coating", "Est. energy", "Price", "Minimum")):
            c.number_format = "$#,##0"
        elif k != "Territory":
            c.number_format = "#,##0"
        cover.cell(i, 1).fill = PatternFill("solid", fgColor=light)
    note_row = 4 + len(rows) + 1
    note = ("Blue cells are inputs: change them and every job value"
            + (", system size and energy-loss estimate" if prof["energy"] else "")
            + " on the Leads sheet and the totals above update.")
    cover.cell(note_row, 1, note).font = Font(name=F, italic=True, color="555555")
    cover.merge_cells(f"A{note_row}:B{note_row + 1}")
    cover.cell(note_row, 1).alignment = Alignment(wrap_text=True, vertical="top")
    if prof["energy"]:
        src = ("Energy-loss defaults: soiling typically costs 3-5% of output a year (IEA, via Solar Insure); "
               "1,300 kWh per kW per year and $0.12/kWh are Midwest ballpark figures. Change them for your area.")
        cover.cell(note_row + 2, 1, src).font = Font(name=F, italic=True, color="555555", size=9)
        cover.merge_cells(f"A{note_row + 2}:B{note_row + 3}")
        cover.cell(note_row + 2, 1).alignment = Alignment(wrap_text=True, vertical="top")
    cover.column_dimensions["A"].width = 38
    cover.column_dimensions["B"].width = 44
    _print_fit(cover, landscape=False)

    ws = wb.create_sheet("Leads")
    ws.append([c for c, _, _ in cols])
    for c in ws[1]:
        c.font = Font(name=F, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=navy)
        c.alignment = Alignment(wrap_text=True, vertical="center")
    url_col = keys.index("map_url") + 1
    fills = {"A": "D9EAD3", "B": "FFF2CC", "C": "DDEBF7", "D": "F4CCCC"}
    for i, r in enumerate(leads, start=2):
        vals = []
        for k in keys:
            if k == "map_url":
                vals.append("Open map")
            elif k == "_status":
                vals.append("New")
            elif k in ("_notes", "_kw", "_loss"):
                vals.append("")
            else:
                v = r.get(k, "")
                if k in ("score", "job_value", "arrays", "est_panels"):
                    v = int(_num(v))
                vals.append(v)
        ws.append(vals)
        for j in range(1, len(keys) + 1):
            ws.cell(i, j).font = Font(name=F, color="0000FF" if keys[j - 1] in ("_status", "_notes") else "000000")
            ws.cell(i, j).alignment = Alignment(vertical="top", wrap_text=keys[j - 1] in ("tenant_names", "notes_out", "owner_mail"))
        ws.cell(i, 2).fill = PatternFill("solid", fgColor=fills.get(r["priority"][0], "FFFFFF"))
        jv = ws.cell(i, keys.index("job_value") + 1)
        pc = get_column_letter(keys.index("est_panels") + 1)
        jv.value = f"=ROUND(MAX(Cover!$B$14,{pc}{i}*Cover!$B$13)/50,0)*50"
        jv.number_format = "$#,##0"
        if prof["energy"]:
            kw = ws.cell(i, keys.index("_kw") + 1)
            kw.value = f"=ROUND({pc}{i}*Cover!$B$15/1000,1)"
            kw.number_format = "#,##0.0"
            loss = ws.cell(i, keys.index("_loss") + 1)
            loss.value = f"=ROUND({L('_kw')}{i}*Cover!$B$16*Cover!$B$18*Cover!$B$17,0)"
            loss.number_format = "$#,##0"
        link = ws.cell(i, url_col)
        link.hyperlink = r.get("map_url") or f"https://www.google.com/maps/@{r['lat']},{r['lon']},90m/data=!3m1!1e3"
        link.font = Font(name=F, color="0563C1", underline="single")
    for k, (_, _, w) in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(k)].width = w
    ws.row_dimensions[1].height = 34
    ws.freeze_panes = "E2"
    _print_fit(ws)
    ws.print_title_rows = "1:1"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(keys))}{max(n, 2)}"
    sc = get_column_letter(keys.index("_status") + 1)
    dv = DataValidation(type="list", allow_blank=True,
                        formula1='"New,Called - no answer,Left voicemail,Talked - follow up,Quote sent,Won,Not interested,Bad number"')
    ws.add_data_validation(dv)
    dv.add(f"{sc}2:{sc}{max(n, 2)}")

    how = wb.create_sheet("How to use")
    coating_steps = [
        "1. Start at the top of the Leads sheet: it is sorted by Score. Big confirmed systems at dusty sites come first.",
        "2. Click 'Open map' before each call. Look for what dirties panels: gravel lots, dirt roads, rail lines, highways, "
        "trees and birds. Mention it: 'Your array sits next to the rail yard, so it picks up a lot of dust...'",
        "3. Ask for the facilities, energy or property manager. Lead with money, not product: dirty panels typically lose 3-5% "
        "of their output a year. Use the 'Est. energy lost to dirt' column for this site's number.",
        "4. Ask who maintains the panels today. Newer systems may be under an installer service contract; that company can be "
        "a buyer or a partner.",
        "5. Warranty: before coating, get the panel maker's or installer's OK in writing. Some module warranties do not cover "
        "aftermarket coatings, and owners will ask.",
        "6. No phone? Use the owner mailing address for a letter, or stop by. Track every call in the Status column.",
        "7. Each month you receive an updated pack. Leads marked 'NEW' are newly mapped installs; the Lead ID stays the same.",
    ]
    steps = coating_steps + [s for s in [
        "Calling rules: these numbers come from public business listings. Before any automated dialing or texting, scrub against "
        "the National Do Not Call Registry and confirm whether the number is a cell phone (TCPA)."]] if prof["energy"] else [
        "1. Start at the top of the Leads sheet: it is sorted by Score. A-priority leads have confirmed panels and large systems.",
        "2. Click 'Open map' before each call to see the roof. Mention what you saw: 'I noticed the array on your south roof...'",
        "3. Ask for the facilities manager or property manager. If the Tips column says 'Head-office number', ask for whoever handles this location.",
        "4. No phone? Use the owner mailing address for a letter or postcard, or stop by in person.",
        "5. Track every call in the Status column (drop-down) and keep notes in the Notes column.",
        "6. Each month you receive an updated pack. Leads marked 'NEW' were added since the last pack; the Lead ID stays the same.",
        "Calling rules: these numbers come from public business listings. Before any automated dialing or texting, scrub against "
        "the National Do Not Call Registry and confirm whether the number is a cell phone (TCPA).",
    ]
    how["A1"] = "How to use this lead pack"
    how["A1"].font = Font(name=F, bold=True, size=14, color=navy)
    for i, t in enumerate(steps, start=3):
        c = how.cell(i, 1, t)
        c.font = Font(name=F, bold=t.startswith("Calling rules"))
        c.alignment = Alignment(wrap_text=True, vertical="top")
    how.column_dimensions["A"].width = 110

    dd = wb.create_sheet("Data dictionary")
    dd.append(["Column", "What it means"])
    for c in dd[1]:
        c.font = Font(name=F, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=navy)
    entries = [e for e in DICTIONARY if not (prof["energy"] and e[0].startswith("Est. cleaning job"))]
    if prof["energy"]:
        entries = entries[:5] + COATING_DICTIONARY + entries[5:]
    for k, v in entries:
        dd.append([k, v])
    for row in dd.iter_rows(min_row=2):
        for c in row:
            c.font = Font(name=F)
            c.alignment = Alignment(wrap_text=True, vertical="top")
    dd.column_dimensions["A"].width = 26
    dd.column_dimensions["B"].width = 100

    src = wb.create_sheet("Sources & license")
    lines = [
        "Sources & attribution",
        "Solar array locations: © OpenStreetMap contributors, available under the Open Database License (ODbL) — openstreetmap.org/copyright",
        "Business names, phones and websites: Overture Maps Foundation places and buildings data — overturemaps.org",
        "Property owner records (where shown): Regrid parcel data — regrid.com",
        "Aerial photos used for panel confirmation: USDA National Agriculture Imagery Program (public domain), via USGS The National Map",
        "",
        (f"This pack is licensed to {customer} for its own sales and marketing within the territory shown on the Cover sheet. "
         if customer.lower() not in ("sample", "demo", "teaser") else
         "Sample pack for evaluation only. ")
        + "Do not resell or redistribute it. Information is provided as-is from public and licensed sources; verify before relying on it.",
    ]
    for i, t in enumerate(lines, start=1):
        c = src.cell(i, 1, t)
        c.font = Font(name=F, bold=(i == 1), size=14 if i == 1 else 10)
        c.alignment = Alignment(wrap_text=True, vertical="top")
    src.column_dimensions["A"].width = 120
    wb.save(path)


def _write_crm_csv(leads, path, brand, offer="cleaning"):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([c for c, _ in CRM_COLS])
        for r in leads:
            out = []
            for _, k in CRM_COLS:
                if k == "_source":
                    out.append(f"{brand} Solar Leads")
                elif k == "_crm_status":
                    out.append("New")
                elif k == "_desc":
                    panels = _num(r.get("est_panels"))
                    bits = [f"{int(panels)} est. panels" if panels else ""]
                    if OFFERS[offer]["energy"] and panels:
                        e = ENERGY_DEFAULTS
                        lost = energy_lost(panels)
                        bits.append(f"soiling risk {r.get('soil_risk', '')}; est. ${lost:,.0f}/yr lost to dirt")
                    bits += [r.get("notes_out", ""), r.get("map_url", "")]
                    out.append(" | ".join(b for b in bits if b))
                else:
                    out.append(r.get(k, ""))
            w.writerow(out)


def _mask_phone(p: str) -> str:
    return (p[:5] + " •••-••••") if p else ""


def _write_teaser(leads, territory, path, brand, top=10, offer="cleaning"):
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    F = "Arial"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Preview"
    total_value = sum(r["job_value"] for r in leads)
    a = sum(1 for r in leads if r["priority"].startswith("A"))
    ws["A1"] = f"{territory['name']}: commercial buildings with solar panels"
    ws["A1"].font = Font(name=F, bold=True, size=16, color="1F3A5F")
    facts = [
        (f"{len(leads):,}", "commercial solar sites in your territory"),
        (f"{a:,}", "confirmed, large systems (A-priority)"),
        (f"{sum(int(_num(r.get('est_panels'))) for r in leads):,}", "estimated panels"),
        (f"${total_value:,.0f}", OFFERS[offer]["teaser_value"]),
        (f"{sum(1 for r in leads if r['phone_clean']):,}", "with a phone number"),
    ]
    if OFFERS[offer]["energy"]:
        e = ENERGY_DEFAULTS
        lost = sum(energy_lost(_num(r.get("est_panels"))) for r in leads)
        facts.insert(4, (f"${lost:,.0f}", "a year these owners lose to dirty panels (est. at 3% soiling)"))
    for i, (v, t) in enumerate(facts, start=3):
        ws.cell(i, 1, v).font = Font(name=F, bold=True, size=14)
        ws.cell(i, 2, t).font = Font(name=F, size=11)
    hdr_row = 3 + len(facts) + 1
    ws.cell(hdr_row, 1, f"Top {min(top, len(leads))} leads (names and numbers unlocked in the full pack)").font = Font(name=F, bold=True)
    hdr = ["Priority", "Type", "City", "Est. panels", "Est. job ($)", "Phone"]
    for j, h in enumerate(hdr, 1):
        c = ws.cell(hdr_row + 1, j, h)
        c.font = Font(name=F, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
    for i, r in enumerate(leads[:top], start=hdr_row + 2):
        vals = [r["priority"], r.get("category", ""), r.get("city", ""), int(_num(r.get("est_panels"))), r["job_value"],
                _mask_phone(r["phone_clean"])]
        for j, v in enumerate(vals, 1):
            ws.cell(i, j, v).font = Font(name=F)
        ws.cell(i, 5).number_format = "$#,##0"
    end = hdr_row + 3 + top
    ws.cell(end, 1, f"Get the full territory pack, updated monthly and exclusive to one company per area — {brand}").font = Font(name=F, italic=True)
    for col, w in zip("ABCDEF", [16, 44, 19, 13, 14, 18]):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=3, max_row=2 + len(facts)):
        for c in row:
            c.alignment = Alignment(vertical="center")
    ws.merge_cells(f"A{end}:F{end + 1}")
    ws.cell(end, 1).alignment = Alignment(wrap_text=True, vertical="top")
    _print_fit(ws, landscape=False)
    wb.save(path)


def _print_fit(ws, landscape: bool = True) -> None:
    """Print / PDF on one page wide so nothing gets cut off."""
    from openpyxl.worksheet.properties import PageSetupProperties

    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.orientation = "landscape" if landscape else "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = ws.page_margins.right = 0.4


def _to_pdf(xlsx_path: str) -> str | None:
    """PDF copy of the teaser (easier to email / open on a phone). Needs LibreOffice; skipped if missing."""
    import shutil
    import subprocess

    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        return None
    try:
        subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", os.path.dirname(xlsx_path), xlsx_path],
                       check=True, capture_output=True, timeout=120)
    except Exception:
        return None
    pdf = os.path.splitext(xlsx_path)[0] + ".pdf"
    return pdf if os.path.exists(pdf) else None


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def package(rows: list[dict], territory: dict, customer: str, out_dir: str, price_per_panel: float | None = None,
            minimum_job: float | None = None, brand: str = "ClearRise Technologies", allow_overlap: bool = False,
            include_verify: bool = True, record: bool = True, offer: str = "cleaning") -> dict:
    if offer not in OFFERS:
        raise ValueError(f"offer must be one of {', '.join(OFFERS)}")
    price_per_panel = OFFERS[offer]["price"] if price_per_panel is None else price_per_panel
    minimum_job = OFFERS[offer]["minimum"] if minimum_job is None else minimum_job
    demo = customer.lower() in ("sample", "demo", "teaser")
    clashes = [] if (allow_overlap or demo) else check_exclusive(territory, customer)
    if clashes:
        names = ", ".join(f"{c['customer']} ({c['name']}, {c['radius_mi']:g} mi)" for c in clashes)
        raise PermissionError(f"Territory overlaps one already sold to: {names}. Use --allow-overlap to override.")
    leads = build_leads(rows, territory, price_per_panel, minimum_job, include_verify, offer)
    folder = os.path.join(out_dir, "packs", f"{_slug(customer)}-{_slug(territory['name'])}")
    os.makedirs(folder, exist_ok=True)
    base = os.path.join(folder, f"solar-leads-{_slug(territory['name'])}-{date.today().isoformat()}")
    _write_customer_xlsx(leads, territory, customer, base + ".xlsx", price_per_panel, minimum_job, brand, offer)
    _write_crm_csv(leads, base + "-crm.csv", brand, offer)
    _write_teaser(leads, territory, base + "-teaser.xlsx", brand, offer=offer)
    teaser_pdf = _to_pdf(base + "-teaser.xlsx")
    manifest = {
        "customer": customer, "offer": offer, "territory": territory, "created": date.today().isoformat(),
        "leads": len(leads), "price_per_panel": price_per_panel,
        "by_priority": {p: sum(1 for r in leads if r["priority"] == p) for p in PRIORITY.values()},
        "with_phone": sum(1 for r in leads if r["phone_clean"]),
        "est_value_per_round": sum(r["job_value"] for r in leads),
        "files": [base + ".xlsx", base + "-crm.csv", base + "-teaser.xlsx"] + ([teaser_pdf] if teaser_pdf else []),
    }
    with open(os.path.join(folder, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    if record and not demo:
        record_sale(territory, customer)
    return manifest


def load_rows(folder: str, states: list[str] | None = None) -> list[dict]:
    """Newest -all-sites.csv per state from an output folder (recursively)."""
    from .scorecard import rows_from_csv

    latest: dict[str, str] = {}
    for root, _, files in os.walk(folder):
        for fn in files:
            m = re.match(r"solar-leads-([A-Z]{2})-(\d{4}-\d{2}-\d{2})-all-sites\.csv$", fn)
            if m and (not states or m.group(1) in states):
                p = os.path.join(root, fn)
                if m.group(1) not in latest or os.path.basename(latest[m.group(1)]) < fn:
                    latest[m.group(1)] = p
    rows = []
    for p in latest.values():
        rows.extend(rows_from_csv(p))
    return rows
