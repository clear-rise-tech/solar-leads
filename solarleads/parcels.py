"""Step 2 + 3: property owner, mailing address and land use from Regrid's nationwide parcel API.

Optional. With no REGRID_TOKEN set, the pipeline skips this step and still produces a list
(business name/phone from Overture, roof check from the aerial photo), just without owner names.

Field names follow Regrid's standard parcel schema. Run `python -m solarleads regrid-test LAT LON`
once you have a token to see a real response and confirm them.
"""
import logging
import time

import requests

from .config import settings

log = logging.getLogger(__name__)

# Regrid schema fields we read (properties.fields.<name>)
FIELDS = {
    "owner": "owner",
    "owner2": "owner2",
    "mail_addr": "mailadd",
    "mail_city": "mail_city",
    "mail_state": "mail_state2",
    "mail_zip": "mail_zip",
    "site_addr": "address",
    "site_city": "scity",
    "site_zip": "szip",
    "parcel_id": "parcelnumb",
    "use_code": "usecode",
    "use_desc": "usedesc",
    "lbcs_activity": "lbcs_activity",
    "lbcs_activity_desc": "lbcs_activity_desc",
    "lbcs_function_desc": "lbcs_function_desc",
    "lbcs_structure_desc": "lbcs_structure_desc",
}


def _fields_from_response(js: dict) -> dict | None:
    """Find the first parcel's attribute dict in a GeoJSON-style response, wherever it is nested."""
    feats = None
    if isinstance(js.get("parcels"), dict):
        feats = js["parcels"].get("features")
    feats = feats or js.get("features") or []
    if not feats:
        return None
    props = feats[0].get("properties", {})
    return props.get("fields", props)


def lookup(lat: float, lon: float) -> dict | None:
    if not settings.regrid_token:
        return None
    params = {"lat": lat, "lon": lon, "limit": 1, "token": settings.regrid_token}
    for attempt in range(4):
        try:
            r = requests.get(settings.regrid_url, params=params, timeout=30,
                             headers={"User-Agent": settings.user_agent})
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code in (401, 403):
                raise RuntimeError("Regrid rejected the token (check REGRID_TOKEN and your plan's API access)")
            r.raise_for_status()
            f = _fields_from_response(r.json())
            if not f:
                return {}
            return {k: (f.get(v) or "") for k, v in FIELDS.items()}
        except requests.RequestException as e:
            log.warning("Regrid error at %s,%s: %s", lat, lon, e)
            time.sleep(2 * (attempt + 1))
    return None


RES_WORDS = ("single family", "single-family", "residential", "res ", "mobile home", "condo", "duplex", "townhouse", "dwelling")
MULTI_WORDS = ("multi", "apartment", "multifamily", "multi-family")


def land_use_class(p: dict | None) -> str:
    """'home', 'multifamily', 'commercial', or 'unknown' from the parcel's use fields."""
    if not p:
        return "unknown"
    text = " ".join(str(p.get(k, "")) for k in ("use_desc", "lbcs_activity_desc", "lbcs_function_desc", "lbcs_structure_desc")).lower()
    act = str(p.get("lbcs_activity", ""))
    if any(w in text for w in MULTI_WORDS):
        return "multifamily"
    if act.startswith("1") and len(act) == 4:  # LBCS activity 1xxx = residential
        return "home"
    if any(w in text for w in RES_WORDS):
        return "home"
    if text.strip():
        return "commercial"
    return "unknown"
