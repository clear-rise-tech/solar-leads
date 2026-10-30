"""Write the call list: a CSV (for CRM / outreach import) and a formatted Excel file."""
import csv
import os
from datetime import date

COLUMNS = [
    ("Tier", "tier"), ("New this run", "new_this_run"), ("Business / site", "display_name"), ("Category", "category"),
    ("Phone", "phone"), ("Website", "website"), ("Site address", "address"), ("City", "city"), ("State", "state"), ("Zip", "zip"),
    ("Solar arrays", "arrays"), ("Est. panels", "est_panels"), ("Array area (m²)", "area_m2"),
    ("Building footprint (m²)", "roof_m2"), ("Building type (map)", "bldg_type"), ("Businesses in building", "tenant_names"),
    ("How business was matched", "building_match"),
    ("Roof check", "panels"), ("Building seen", "building_type"), ("Roof note", "roof_note"),
    ("Property owner", "owner"), ("Owner mailing address", "owner_mail"), ("Parcel #", "parcel_id"),
    ("Land use (county)", "use_desc"), ("Satellite view", "map_url"), ("Latitude", "lat"), ("Longitude", "lon"),
    ("Site ID", "site_id"), ("Lead status", "lead_status"),
]

TIER_ORDER = {"1": 0, "2": 1, "3": 2, "4": 3, "X": 9}
TIER_FILL = {"1": "D9EAD3", "2": "FFF2CC", "3": "DDEBF7", "4": "F4CCCC", "X": "EEEEEE"}


def _prep(s: dict) -> dict:
    r = dict(s)
    r["bldg_type"] = " / ".join(x for x in [s.get("bldg_subtype", ""), s.get("bldg_class", "")] if x)
    if s.get("multi_tenant"):
        label = s.get("bldg_name") or (s.get("owner", "").title() if s.get("owner") else "")
        if label:
            r["display_name"] = f"{label} ({s.get('tenants')} businesses)"
        else:  # name the tenants nearest the panels so the caller knows who to ask for
            names = [n.strip() for n in (s.get("tenant_names") or "").split(",") if n.strip()]
            more = max(int(s.get("tenants") or 0) - 2, 0)
            r["display_name"] = (" / ".join(names[:2]) + (f" +{more} more" if more else "") + " (multi-tenant)"
                                 if names else f"Multi-tenant building ({s.get('tenants')} businesses)")
        return _finish(r, s)
    r["display_name"] = s.get("biz_name") or s.get("osm_name") or (s.get("owner", "").title() + " (owner)" if s.get("owner") else _unnamed(s))
    return _finish(r, s)


def _unnamed(s: dict) -> str:
    where = s.get("site_addr") or s.get("biz_address") or s.get("site_city") or s.get("biz_city") or "see satellite link"
    kind = (s.get("category") or "Commercial building").split(" (")[0]
    return f"Unnamed {kind.lower()} – {where}"


def _finish(r: dict, s: dict) -> dict:
    r["address"] = s.get("site_addr") or s.get("biz_address") or ""
    r["city"] = s.get("site_city") or s.get("biz_city") or ""
    r["zip"] = s.get("site_zip") or s.get("biz_zip") or ""
    mail = ", ".join(x for x in [s.get("mail_addr", ""), s.get("mail_city", "")] if x)
    if s.get("mail_state"):
        mail += f" {s.get('mail_state')} {s.get('mail_zip', '')}".rstrip()
    r["owner_mail"] = mail
    r["map_url"] = f"https://www.google.com/maps/@{s['lat']},{s['lon']},90m/data=!3m1!1e3"
    r["new_this_run"] = "NEW" if s.get("new_this_run") else ""
    r["lead_status"] = "New Commercial Lead"
    return r


def sort_rows(sites: list[dict]) -> list[dict]:
    return sorted((_prep(s) for s in sites), key=lambda r: (TIER_ORDER.get(r["tier"][0], 5), -r.get("arrays", 0), -r.get("area_m2", 0)))


def write_csv(rows: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([c for c, _ in COLUMNS])
        for r in rows:
            w.writerow([r.get(k, "") for _, k in COLUMNS])


def write_xlsx(rows: list[dict], path: str, title: str) -> None:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Call List"
    ws.append([c for c, _ in COLUMNS])
    for c in ws[1]:
        c.font = Font(name="Arial", bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
        c.alignment = Alignment(wrap_text=True, vertical="center")
    url_col = [k for _, k in COLUMNS].index("map_url") + 1
    for i, r in enumerate(rows, start=2):
        ws.append([("Open map" if k == "map_url" else r.get(k, "")) for _, k in COLUMNS])
        for j in range(1, len(COLUMNS) + 1):
            ws.cell(i, j).font = Font(name="Arial")
        ws.cell(i, 1).fill = PatternFill("solid", fgColor=TIER_FILL.get(r["tier"][0], "FFFFFF"))
        link = ws.cell(i, url_col)
        link.hyperlink = r["map_url"]
        link.font = Font(name="Arial", color="0563C1", underline="single")
    widths = [20, 8, 34, 22, 15, 26, 28, 16, 6, 7, 8, 9, 10, 11, 18, 34, 22, 11, 16, 30, 30, 36, 18, 22, 11, 10, 11, 18, 20]
    for k, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(k)].width = w
    ws.freeze_panes = "D2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{len(rows) + 1}"
    status_col = get_column_letter(len(COLUMNS))
    dv = DataValidation(type="list", allow_blank=True, formula1='"New Commercial Lead,Contacted,Qualified,Site Visit Needed,Estimate Needed,Estimate Sent,Follow-Up,Won,Lost,Not a fit"')
    ws.add_data_validation(dv)
    dv.add(f"{status_col}2:{status_col}{len(rows) + 1}")

    s = wb.create_sheet("Summary")
    s["A1"] = title
    s["A1"].font = Font(name="Arial", bold=True, size=14)
    s.append([])
    s.append(["Tier", "Sites"])
    for t in ["1 - Call first", "2 - Good fit", "3 - Government (bid/procurement)", "4 - Verify first"]:
        s.append([t, f"=COUNTIF('Call List'!A:A,\"{t}\")"])
    s.append(["New since last run", "=COUNTIF('Call List'!B:B,\"NEW\")"])
    s.append([])
    notes = [
        "Built from OpenStreetMap solar mapping, Overture Maps businesses, Regrid parcels (if a token was set), "
        "USDA NAIP aerial photos and an AI roof check. Confirm each lead with the satellite link before calling.",
        "Tier 1 = panels confirmed and a large system (3+ arrays or ~400 m²+). Tier 2 = panels confirmed. "
        "Tier 3 = government buildings (usually bought through bids). Tier 4 = panels not confirmed on the photo yet.",
        "Phone numbers come from public business listings. Scrub against the Do Not Call list before any "
        "automated calling or texting.",
    ]
    for n in notes:
        s.append([n])
    s.column_dimensions["A"].width = 100
    for row in s.iter_rows():
        for c in row:
            c.font = Font(name="Arial", bold=c.row in (1, 3))
            c.alignment = Alignment(wrap_text=True, vertical="top")
    wb.move_sheet("Summary", -1)
    wb.save(path)


def write_outputs(state: str, sites: list[dict], out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    rows = sort_rows(sites)
    keep = [r for r in rows if not r["tier"].startswith("X")]
    stamp = date.today().isoformat()
    base = os.path.join(out_dir, f"solar-leads-{state}-{stamp}")
    write_csv(keep, base + ".csv")
    write_xlsx(keep, base + ".xlsx", f"Commercial solar call list — {state} — {stamp}")
    write_csv(rows, base + "-all-sites.csv")
    return {"csv": base + ".csv", "xlsx": base + ".xlsx", "all": base + "-all-sites.csv", "leads": len(keep), "sites": len(rows)}


def combine(csv_paths: list[str], out_path: str) -> int:
    """Merge per-state call lists into one national CSV + Excel file."""
    label_to_key = {label: key for label, key in COLUMNS}
    rows = []
    for p in csv_paths:
        with open(p, newline="", encoding="utf-8") as f:
            for rec in csv.DictReader(f):
                r = {label_to_key.get(k, k): v for k, v in rec.items()}
                for k in ("arrays", "area_m2"):
                    try:
                        r[k] = float(r.get(k) or 0)
                    except ValueError:
                        r[k] = 0
                r["arrays"] = int(r["arrays"])
                rows.append(r)
    rows.sort(key=lambda r: (TIER_ORDER.get((r.get("tier") or "X")[0], 5), -r["arrays"], -r["area_m2"]))
    write_csv(rows, out_path + ".csv")
    write_xlsx(rows, out_path + ".xlsx", f"Commercial solar call list — {len(csv_paths)} states — {date.today().isoformat()}")
    return len(rows)
