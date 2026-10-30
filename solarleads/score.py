"""Turn everything we learned about a site into a category and a call tier."""
import re

GOV_WORDS = ("CITY OF", "COUNTY OF", " COUNTY", "STATE OF", "UNITED STATES", "USA ", "TOWN OF", "VILLAGE OF", "TOWNSHIP")
SCHOOL_RE = re.compile(r"\b(SCHOOL|COLLEGE|UNIVERSITY|ACADEMY)\b")
# 'CHURCH' only as a word, and not as a street name ("BOCADA 8050 N CHURCH ROAD LLC" is a car dealer)
CHURCH_RE = re.compile(r"\b(CHURCH(?!\s+(RD|ROAD|ST|STREET|AVE|AVENUE|DR|DRIVE|LN|LANE)\b)|DIOCESE|MINISTR\w*|TEMPLE|"
                       r"SYNAGOGUE|MOSQUE|PARISH)\b")

# Order matters: the first rule whose words appear wins. Specific trades before broad words like "food" or "store".
CATEGORY_RULES = [
    (("school", "college", "university", "academy", "campus", "elementary", "middle_school", "high_school",
      "preschool", "education"), "School / college"),
    (("church", "place_of_worship", "worship", "temple", "religious", "parish", "synagogue", "mosque"), "Church"),
    (("hotel", "motel", "lodging", "inn"), "Hotel"),
    (("apartment", "multifamily", "senior_living", "retirement"), "Multifamily / senior housing"),
    (("storage",), "Self-storage"),
    (("car_dealer", "auto_dealer", "auto", "automotive", "car_wash", "tire", "truck", "towing", "auto_body"),
     "Auto / equipment"),
    (("hospital", "clinic", "dentist", "dental", "endodont", "orthodont", "doctor", "medical", "veterinar", "health",
      "surgery", "surgeon", "orthop", "pediatr", "chiropract", "optomet", "eye_care", "vision", "pharmac",
      "physical_therap"), "Medical / vet / dental"),
    (("bank", "credit_union"), "Bank"),
    (("warehouse", "industrial", "manufactur", "factory", "distribut", "logistics", "plant", "chemical", "machin",
      "fabricat", "spring_supplier", "wholesale"), "Industrial / warehouse"),
    (("contractor", "plumbing", "hvac", "heating", "roofing", "electrical", "electrician", "glass", "siding",
      "garage_door", "construction", "remodel", "foundation_repair"), "Contractor / trades"),
    (("restaurant", "cafe", "coffee", "bar", "fast_food", "bakery", "pizza"), "Restaurant / bar"),
    (("grocery", "supermarket", "discount_store", "store", "shop", "retail", "hardware", "convenience",
      "gas_station"), "Retail"),
    (("fitness", "gym", "sports", "recreation", "rink", "skating", "athletic", "dance", "community_center"),
     "Fitness / recreation"),
    (("office", "professional", "insurance", "law", "attorney", "legal", "accountant", "tax_service", "real_estate",
      "consult", "b2b", "engineering", "software", "agency", "financial"), "Office"),
    (("social", "community_service", "charity", "nonprofit", "foundation"), "Nonprofit"),
    (("farm", "agricultur"), "Farm / agricultural"),
    (("civic", "government", "fire_station", "police"), "Government facility"),
    (("commercial",), "Commercial building"),
]


def _hit(words, tokens, text) -> bool:
    """Whole-word match for short words (so 'inn' doesn't hit 'innovation'), prefix match for stems."""
    for w in words:
        if "_" in w:
            if w in text:
                return True
        elif len(w) <= 4:
            if w in tokens:
                return True
        elif any(t.startswith(w) for t in tokens):
            return True
    return False


def _rules(text: str) -> str | None:
    tokens = [t for t in re.split(r"[^a-z0-9]+", text) if t]
    for words, label in CATEGORY_RULES:
        if _hit(words, tokens, text):
            return label
    return None


def categorize(site: dict) -> str:
    """Owner records decide government; the business at the site decides everything else; then the owner's
    name (school district, diocese), then the building type from the map."""
    owner = (site.get("owner") or "").upper()
    if site.get("land_use") == "multifamily" or site.get("building_type") == "multifamily":
        return "Multifamily / senior housing"
    if any(w in owner for w in GOV_WORDS) and not SCHOOL_RE.search(owner):
        return "Government facility"
    if site.get("multi_tenant"):
        return "Multi-tenant building (call property manager)"
    biz = " ".join(str(site.get(k) or "") for k in ("biz_category", "biz_name")).lower()
    label = _rules(biz) if biz.strip() else None
    land = _rules(str(site.get("use_desc") or "").lower())
    # A vague listing ("b2b service", "corporate office") on an industrial parcel is a plant or warehouse.
    if land == "Industrial / warehouse" and label in (None, "Office", "Commercial building"):
        return land
    if label:
        return label
    if SCHOOL_RE.search(owner):
        return "School / college"
    if CHURCH_RE.search(owner):
        return "Church"
    label = _rules(" ".join(str(site.get(k) or "") for k in ("use_desc", "bldg_class", "bldg_subtype", "building_type")).lower())
    if label:
        return label
    if site.get("is_solar_farm"):
        return "Solar farm (O&M)"
    bt = site.get("building_type")
    if bt == "industrial_warehouse":
        return "Industrial / warehouse"
    if bt == "institutional":
        return "Institution"
    return "Commercial building"


def tier(site: dict) -> str:
    """1 Call first · 2 Good fit · 3 Government (bid/procurement) · 4 Verify first · X Exclude."""
    panels = site.get("panels", "not_checked")
    bt = site.get("building_type", "unknown")
    land = site.get("land_use", "unknown")
    if land == "home" or (bt == "house" and land not in ("commercial", "multifamily")):
        return "X - Home, excluded"
    if panels == "no":
        return "X - No panels seen"
    if site.get("category") == "Government facility":
        return "3 - Government (bid/procurement)" if panels == "yes" else "4 - Verify first"
    if site.get("category") == "Solar farm (O&M)":
        return "4 - Verify first"
    if panels != "yes":
        return "4 - Verify first"
    big = (site.get("area_m2", 0) >= 400 or site.get("roof_size") == "large" or site.get("arrays", 1) >= 3
           or site.get("roof_m2", 0) >= 2000)
    return "1 - Call first" if big else "2 - Good fit"


def est_panels(area_m2: float) -> int:
    """Rough panel count from mapped array area (~1.9 m² per commercial module)."""
    return int(round(area_m2 / 1.9)) if area_m2 else 0
