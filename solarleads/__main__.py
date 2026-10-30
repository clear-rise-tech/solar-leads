"""Command line:

    python -m solarleads run MO                 # one state
    python -m solarleads run MO KS --refresh    # several states, re-read the map for new installs
    python -m solarleads run ALL                # every state (long; best split across GitHub jobs)
    python -m solarleads combine output/        # merge state lists into one national file
    python -m solarleads score output/solar-leads-MO-2026-11-01-all-sites.csv   # accuracy vs answer key
    python -m solarleads package output/ --metro "Kansas City" --radius 40 --customer "Acme Cleaning"
    python -m solarleads metros                 # list built-in metros
    python -m solarleads regrid-test 39.2 -94.6 # show a raw Regrid record to confirm field names
"""
import argparse
import json
import logging
import os
import sys

from .config import settings
from .osm import STATES


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="solarleads", description="Commercial solar panel call-list builder")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="build call lists for one or more states")
    r.add_argument("states", nargs="+", help="two-letter state codes, or ALL")
    r.add_argument("--refresh", action="store_true", help="re-scan the map for new installs (monthly)")
    c = sub.add_parser("combine", help="merge per-state call lists into one national file")
    c.add_argument("folder", help="folder containing solar-leads-XX-DATE.csv files")
    sc = sub.add_parser("score", help="score an -all-sites.csv against the answer key")
    sc.add_argument("csv_path")
    pk = sub.add_parser("package", help="build a customer-ready lead pack for one territory")
    pk.add_argument("folder", help="folder with solar-leads-XX-DATE-all-sites.csv files")
    pk.add_argument("--metro", help="metro name, e.g. 'Kansas City'")
    pk.add_argument("--center", help="LAT,LON instead of a metro")
    pk.add_argument("--radius", type=float, default=35, help="miles from the center (default 35)")
    pk.add_argument("--customer", default="Sample", help="customer name ('Sample' = demo, not recorded)")
    pk.add_argument("--offer", choices=["cleaning", "coating"], default="cleaning",
                    help="what the buyer sells: panel cleaning (recurring) or hydrophobic / anti-soiling coating")
    pk.add_argument("--price-per-panel", type=float, default=None, help="default: $12 cleaning, $6 coating")
    pk.add_argument("--minimum-job", type=float, default=None)
    pk.add_argument("--brand", default="ClearRise Technologies")
    pk.add_argument("--confirmed-only", action="store_true", help="leave out D - Verify first leads")
    pk.add_argument("--allow-overlap", action="store_true", help="ignore territory exclusivity check")
    sub.add_parser("metros", help="list built-in metro names")
    t = sub.add_parser("regrid-test", help="print a raw Regrid parcel record")
    t.add_argument("lat", type=float)
    t.add_argument("lon", type=float)
    args = ap.parse_args(argv)
    handlers = [logging.StreamHandler()]
    if args.cmd == "run":
        os.makedirs(settings.out_dir, exist_ok=True)
        handlers.append(logging.FileHandler(os.path.join(settings.out_dir, "run-log.txt")))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)

    if args.cmd == "regrid-test":
        import requests

        if not settings.regrid_token:
            print("Set REGRID_TOKEN first.")
            return 1
        resp = requests.get(settings.regrid_url, params={"lat": args.lat, "lon": args.lon, "limit": 1,
                                                          "token": settings.regrid_token}, timeout=30)
        print(resp.status_code)
        print(json.dumps(resp.json(), indent=2)[:6000])
        return 0

    if args.cmd == "metros":
        from .deliver import METROS

        for k, (lat, lon, st) in sorted(METROS.items()):
            print(f"{k:16} {lat:9.4f} {lon:10.4f}  states: {st}")
        return 0

    if args.cmd == "package":
        from .deliver import load_rows, package, resolve_territory

        try:
            terr = resolve_territory(args.metro, args.center, args.radius)
        except ValueError as e:
            print(e)
            return 2
        rows = load_rows(args.folder, terr["states"] or None)
        if not rows:
            print(f"No -all-sites.csv files found in {args.folder}" + (f" for {' '.join(terr['states'])}" if terr["states"] else ""))
            return 1
        try:
            m = package(rows, terr, args.customer, settings.out_dir, args.price_per_panel, args.minimum_job, args.brand,
                        allow_overlap=args.allow_overlap, include_verify=not args.confirmed_only, offer=args.offer)
        except PermissionError as e:
            print(e)
            return 3
        print(json.dumps({k: m[k] for k in ("customer", "leads", "by_priority", "with_phone", "est_value_per_round", "files")}, indent=2))
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as f:
                f.write(f"## Lead pack: {m['customer']} — {terr['name']} ({terr['radius_mi']:g} mi)\n\n"
                        f"- Leads: **{m['leads']}** ({m['with_phone']} with phone)\n"
                        f"- By priority: {m['by_priority']}\n- Est. {m['offer']} value: **${m['est_value_per_round']:,}**\n")
        return 0

    if args.cmd == "score":
        import re

        from .scorecard import rows_from_csv, run_scorecard

        m = re.search(r"solar-leads-([A-Z]{2})-", os.path.basename(args.csv_path))
        if not m:
            print("File name should look like solar-leads-MO-2026-11-01-all-sites.csv")
            return 2
        path = run_scorecard(m.group(1), rows_from_csv(args.csv_path), settings.out_dir)
        print(open(path).read() if path else f"No answer key for {m.group(1)} yet.")
        return 0

    if args.cmd == "combine":
        import glob

        from .export import combine

        paths = sorted(p for p in glob.glob(os.path.join(args.folder, "**", "solar-leads-*.csv"), recursive=True)
                       if not p.endswith("-all-sites.csv") and "nationwide" not in p)
        if not paths:
            print("No state call lists found.")
            return 1
        os.makedirs(settings.out_dir, exist_ok=True)
        n = combine(paths, os.path.join(settings.out_dir, "solar-leads-nationwide"))
        print(f"Combined {len(paths)} state lists into {n} leads.")
        return 0

    from .export import sort_rows, write_outputs
    from .pipeline import run_state
    from .scorecard import run_scorecard
    from .store import Store

    states = list(STATES) if [s.upper() for s in args.states] == ["ALL"] else [s.upper() for s in args.states]
    bad = [s for s in states if s not in STATES]
    if bad:
        print(f"Unknown state code(s): {', '.join(bad)}")
        return 2
    store = Store()
    failures = []

    def notice(title, text):  # summary lines on the GitHub run page (readable through the API)
        if os.environ.get("GITHUB_ACTIONS"):
            text = text[:4000].replace("%", "%25").replace("\r", "").replace("\n", "%0A")
            print(f"::notice title={title}::{text}", flush=True)

    for st in states:
        try:
            sites = run_state(st, refresh_map=args.refresh, store=store)
            res = write_outputs(st, sites, settings.out_dir)
            logging.info("%s: %d leads written to %s", st, res["leads"], res["xlsx"])
            from collections import Counter

            tiers = Counter(s.get("tier", "") for s in sites)
            cats = Counter(s.get("category", "") for s in sites if not s.get("tier", "X").startswith("X"))
            notice(f"{st} results", f"{res['sites']} sites, {res['leads']} leads\nTiers: "
                   + ", ".join(f"{k}: {v}" for k, v in sorted(tiers.items()))
                   + "\nCategories: " + ", ".join(f"{k}: {v}" for k, v in cats.most_common(12))
                   + f"\nWith phone: {sum(1 for s in sites if s.get('phone') and not s.get('tier', 'X').startswith('X'))}")
            card = run_scorecard(st, sort_rows(sites), settings.out_dir)
            if card:
                logging.info("%s: accuracy scorecard written to %s", st, card)
                notice(f"{st} scorecard", open(card, encoding="utf-8").read())
        except Exception as e:  # one state failing should not stop the others
            logging.exception("%s failed: %s", st, e)
            if os.environ.get("GITHUB_ACTIONS"):  # show the error on the run page
                import traceback

                tb = traceback.format_exc()[-3000:].replace("%", "%25").replace("\r", "").replace("\n", "%0A")
                print(f"::error title={st} failed::{tb}", flush=True)
            failures.append(st)
    if failures:
        print(f"Finished with errors in: {', '.join(failures)} (re-run to resume where they stopped)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
