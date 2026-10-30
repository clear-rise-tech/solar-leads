"""Accuracy scorecard: compare a run's output to an answer key of hand-checked sites.

The KC Northland answer key (benchmarks/kc_northland_2026-10-29.csv) holds 196 sites in Clay and Platte
County, MO, each checked on satellite imagery on Oct 29, 2026 and matched to the county owner record.

Metrics
- Found rate:   share of confirmed answer-key sites that made the call list.
- Clean rate:   share of call-list rows in the answer-key area that match a confirmed site (a lower bound:
                rows not in the key may still be good, they just were not hand-checked).
- Owner match:  share of matched rows whose owner (Regrid) agrees with the county record.
- Name match:   share of matched rows whose business name agrees with the hand-checked name.
"""
import csv
import os
import re

from .geo import GridIndex

MATCH_M = 100  # a call-list row this close to an answer-key site counts as the same site
BENCH_DIR = os.path.join(os.path.dirname(__file__), "benchmarks")
STOP = {"LLC", "INC", "CO", "CORP", "THE", "OF", "AND", "LP", "LTD", "TRUST", "COMPANY", "C/O", "KC", "MO", "KANSAS", "CITY"}


def benchmarks_for(state: str) -> list[str]:
    if not os.path.isdir(BENCH_DIR):
        return []
    if state.upper() == "MO":
        return [os.path.join(BENCH_DIR, f) for f in sorted(os.listdir(BENCH_DIR)) if f.startswith("kc_northland")]
    return []


def _words(s: str) -> set[str]:
    return {w for w in re.split(r"[^A-Z0-9]+", (s or "").upper()) if len(w) > 2 and w not in STOP}


def names_agree(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    return bool(wa and wb and len(wa & wb) / min(len(wa), len(wb)) >= 0.5)


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def evaluate(rows: list[dict], key_path: str) -> dict:
    """`rows`: every site from a run (the -all-sites CSV, or pipeline output), using export field names."""
    with open(key_path, newline="", encoding="utf-8") as f:
        key = list(csv.DictReader(f))
    for k in key:
        k["lat"], k["lon"] = float(k["lat"]), float(k["lon"])
    lats = [k["lat"] for k in key]
    lons = [k["lon"] for k in key]
    box = (min(lats) - 0.01, min(lons) - 0.01, max(lats) + 0.01, max(lons) + 0.01)
    hull = _convex_hull([(k["lon"], k["lat"]) for k in key])
    near_key = GridIndex(0.002)
    for k in key:
        near_key.add(k["lat"], k["lon"], k)

    def in_box(r):
        """In the area the answer key covers: inside the outline of the hand-checked sites (so rows from
        neighbouring counties that were never hand-checked don't count against the clean rate)."""
        lat, lon = _num(r.get("lat")), _num(r.get("lon"))
        if not (box[0] <= lat <= box[2] and box[1] <= lon <= box[3]):
            return False
        return _in_hull(lon, lat, hull) or bool(near_key.near(lat, lon, MATCH_M))

    area_rows = [r for r in rows if in_box(r)]
    call = [r for r in area_rows if not str(r.get("tier", "X")).startswith("X")]
    excluded = [r for r in area_rows if str(r.get("tier", "X")).startswith("X")]
    idx_call, idx_excl = GridIndex(0.002), GridIndex(0.002)
    for r in call:
        idx_call.add(_num(r["lat"]), _num(r["lon"]), r)
    for r in excluded:
        idx_excl.add(_num(r["lat"]), _num(r["lon"]), r)

    confirmed = [k for k in key if k["status"].startswith("panels_confirmed")]
    found, excluded_by_tool, never_seen = [], [], []
    owner_checks = owner_ok = name_checks = name_ok = 0
    name_misses = []
    matched_rows = set()
    for k in confirmed:
        hit = idx_call.near(k["lat"], k["lon"], MATCH_M)
        if hit:
            r = hit[0][1]
            found.append((k, r))
            matched_rows.add(id(r))
            if r.get("owner") and k.get("county_owner"):
                owner_checks += 1
                owner_ok += names_agree(r["owner"], k["county_owner"])
            if k.get("site_name") and r.get("display_name"):
                name_checks += 1
                if names_agree(r["display_name"], k["site_name"]):
                    name_ok += 1
                else:
                    name_misses.append((k, r))
        elif idx_excl.near(k["lat"], k["lon"], MATCH_M):
            excluded_by_tool.append((k, idx_excl.near(k["lat"], k["lon"], MATCH_M)[0][1]))
        else:
            never_seen.append(k)

    # call-list rows: do they match a confirmed key site, an 'unclear' one, or nothing in the key?
    key_idx = GridIndex(0.002)
    for k in key:
        key_idx.add(k["lat"], k["lon"], k)
    clean = unclear = unknown_rows = 0
    unknown = []
    for r in call:
        near = key_idx.near(_num(r["lat"]), _num(r["lon"]), MATCH_M)
        if any(k["status"].startswith("panels_confirmed") for _, k in near):
            clean += 1
        elif near:
            unclear += 1
        else:
            unknown_rows += 1
            unknown.append(r)

    pct = lambda a, b: round(100 * a / b, 1) if b else None  # noqa: E731
    return {
        "key": os.path.basename(key_path),
        "confirmed_sites": len(confirmed),
        "found": len(found),
        "found_rate": pct(len(found), len(confirmed)),
        "excluded_by_tool": excluded_by_tool,
        "never_seen": never_seen,
        "call_rows_in_area": len(call),
        "clean": clean,
        "clean_rate": pct(clean, len(call)),
        "unclear_rows": unclear,
        "unknown_rows": unknown,
        "owner_checks": owner_checks,
        "owner_rate": pct(owner_ok, owner_checks),
        "name_checks": name_checks,
        "name_rate": pct(name_ok, name_checks),
        "name_misses": name_misses,
    }


def _convex_hull(pts):
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _in_hull(x, y, hull) -> bool:
    if len(hull) < 3:
        return False
    n = len(hull)
    for i in range(n):
        (x1, y1), (x2, y2) = hull[i], hull[(i + 1) % n]
        if (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1) < 0:
            return False
    return True


def _map(lat, lon) -> str:
    return f"[map](https://www.google.com/maps/@{lat},{lon},90m/data=!3m1!1e3)"


def to_markdown(res: dict, targets=(90.0, 95.0)) -> str:
    def mark(v, t):
        return "—" if v is None else (f"**{v}%** ✅" if v >= t else f"**{v}%** ⚠️")

    lines = [
        f"## Accuracy scorecard — {res['key']}",
        "",
        "| Measure | Result | Target |",
        "|---|---|---|",
        f"| Found rate (hand-checked sites that made the call list) | {mark(res['found_rate'], targets[0])} "
        f"({res['found']} of {res['confirmed_sites']}) | {targets[0]:.0f}% |",
        f"| Clean rate (call-list rows in the area that are confirmed sites) | {mark(res['clean_rate'], targets[1])} "
        f"({res['clean']} of {res['call_rows_in_area']}) | {targets[1]:.0f}% |",
        f"| Owner matches county record | {res['owner_rate'] if res['owner_rate'] is not None else '— (no Regrid token)'}"
        f"{'%' if res['owner_rate'] is not None else ''} ({res['owner_checks']} checked) | 95% |",
        f"| Business name matches hand check | {res['name_rate'] if res['name_rate'] is not None else '—'}"
        f"{'%' if res['name_rate'] is not None else ''} ({res['name_checks']} checked) | 85% |",
        "",
        f"- Confirmed sites the tool saw but **excluded**: {len(res['excluded_by_tool'])}",
        f"- Confirmed sites **not in the run at all** (map data gap): {len(res['never_seen'])}",
        f"- Call-list rows matching an *unclear* answer-key site: {res['unclear_rows']}",
        f"- Call-list rows **not in the answer key** (spot-check these): {len(res['unknown_rows'])}",
    ]
    if res["unknown_rows"]:
        from collections import Counter

        cats = Counter(r.get("category") or "?" for r in res["unknown_rows"])
        lines.append("  - by kind: " + ", ".join(f"{k} {v}" for k, v in cats.most_common()))
    lines.append("")
    if res.get("name_misses"):
        lines += ["### Business name differs from the hand check", "", "| Answer key | Tool's name | Map |", "|---|---|---|"]
        for k, r in res["name_misses"][:20]:
            lines.append(f"| {k['site_name']} | {r.get('display_name', '')} | {_map(k['lat'], k['lon'])} |")
        lines.append("")
    if res["excluded_by_tool"]:
        lines += ["### Excluded by the tool but confirmed by hand", "", "| Answer key | Why excluded | Map |", "|---|---|---|"]
        for k, r in res["excluded_by_tool"][:30]:
            lines.append(f"| {k['site_name'] or k['county_owner']} ({k['answer_id']}) | {r.get('tier', '')} | {_map(k['lat'], k['lon'])} |")
        lines.append("")
    if res["never_seen"]:
        lines += ["### Not found at all", "", "| Answer key | Arrays mapped Oct 29 | Map |", "|---|---|---|"]
        for k in res["never_seen"][:30]:
            lines.append(f"| {k['site_name'] or k['county_owner']} ({k['answer_id']}) | {k['arrays_mapped']} | {_map(k['lat'], k['lon'])} |")
        lines.append("")
    if res["unknown_rows"]:
        lines += ["### On the call list but not in the answer key", "",
                  "| Site | Kind | Arrays | Panel area m² | Map |", "|---|---|---|---|---|"]
        for r in res["unknown_rows"][:40]:
            lines.append(f"| {r.get('display_name', '')} | {r.get('category', '')} | {r.get('arrays', '')} | "
                         f"{round(_num(r.get('area_m2')))} | {_map(r['lat'], r['lon'])} |")
        lines.append("")
    return "\n".join(lines)


def run_scorecard(state: str, rows: list[dict], out_dir: str) -> str | None:
    """Score a state's rows against any answer keys for that state. Writes scorecard-XX.md; returns its path."""
    keys = benchmarks_for(state)
    if not keys:
        return None
    md = "\n\n".join(to_markdown(evaluate(rows, k)) for k in keys)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"scorecard-{state.upper()}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:  # show it right on the GitHub run page
        with open(summary, "a", encoding="utf-8") as f:
            f.write(md + "\n")
    return path


def rows_from_csv(path: str) -> list[dict]:
    from .export import COLUMNS

    label_to_key = {label: key for label, key in COLUMNS}
    with open(path, newline="", encoding="utf-8") as f:
        return [{label_to_key.get(k, k): v for k, v in r.items()} for r in csv.DictReader(f)]
