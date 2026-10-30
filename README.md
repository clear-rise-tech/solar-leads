# Solar Leads — commercial solar panel call-list builder

Finds businesses with solar panels in any U.S. state and turns them into a tiered call list
(Excel + CSV) for panel-cleaning and exterior-maintenance outreach. Runs on GitHub for free:
pick a state, click **Run**, download the list. It also re-runs itself monthly to catch new installs.

## What it does, step by step

| # | Step | Source | Cost |
|---|---|---|---|
| 1 | Find every mapped solar array in the state | OpenStreetMap (Overpass API) | Free |
| 2 | Group arrays on the same property into one site | built in | Free |
| 3 | Match each site to the business there: name, category, phone, website | Overture Maps places | Free |
| 4 | Drop homes: small arrays with no business nearby | built in | Free |
| 4b | Put the panels on their exact building (Overture footprints): real roof size and building type, drop houses, pick the business *inside* that building, flag multi-tenant buildings | Overture Maps buildings | Free |
| 5 | Property owner, mailing address, land use; drops homes the free filter missed | Regrid parcel API | Paid, optional |
| 6 | Pull a public-domain aerial photo of the roof | USDA NAIP via USGS National Map | Free |
| 7 | AI checks the photo: panels visible? what kind of building? | Claude API (`claude-haiku-5-5`) | Paid, pennies per site |
| 8 | Score into tiers and export | built in | Free |

The paid steps (5 and 7) only run on sites that already look commercial, **largest systems first**.
Both have a per-run cap and every result is saved, so you never pay for the same site twice.

**Tiers**
- **1 – Call first:** panels confirmed, with 3+ arrays or a large system.
- **2 – Good fit:** panels confirmed.
- **3 – Government:** city, county, state or federal buildings, which usually buy through bids.
- **4 – Verify first:** panels not confirmed on the photo, or the roof check was skipped because of the cap.
- Homes and sites where no panels were seen are left out of the call list. They stay in the `-all-sites.csv` file.

## Accuracy scorecard

Every Missouri run is graded against an answer key of 196 Kansas City Northland sites. They were checked by hand on satellite imagery on Oct 29, 2026, and matched to Clay County owner records (`solarleads/benchmarks/`). The grade appears on the GitHub run page, and as `scorecard-MO.md` in the downloads.

| Measure | What it means | Target |
|---|---|---|
| Found rate | Hand-confirmed sites that made the call list | 90% |
| Clean rate | Call-list rows in the Northland area that are confirmed sites (a floor; unlisted rows may still be good) | 95% |
| Owner match | Regrid owner agrees with the county record | 95% |
| Name match | Business name agrees with the hand check | 85% |

The scorecard also lists the specific misses, each with a map link:
- Confirmed sites the tool excluded, and why.
- Sites missing from the map data entirely.
- Call-list rows that aren't in the answer key.

Check those links, adjust the settings, and re-run. To re-grade an earlier run without re-running it:
`python -m solarleads score output/solar-leads-MO-<date>-all-sites.csv`.

## One-time setup (about 15 minutes)

1. **Create a private GitHub repository.** On github.com, click **New repository**, name it `solar-leads`, choose **Private**, and click **Create**.
2. **Upload this folder.** On the new repo page, click **uploading an existing file** and drag in everything from this folder, including the hidden `.github` folder. Click **Commit changes**.
   - If `.github` won't drag in on your computer, use GitHub Desktop or ask Claude to push it for you.
3. **Add your Claude API key** (needed for the roof check).
   - Get a key at console.anthropic.com under **API Keys**.
   - In the Console, set a monthly spend limit under **Limits**.
   - In the repo, go to **Settings → Secrets and variables → Actions → New repository secret**. Name it `ANTHROPIC_API_KEY` and paste the key.
4. **Optional: add a Regrid token** for owner names. Add another secret named `REGRID_TOKEN`. Without it, the tool still works but the owner columns stay blank.
5. **Choose the monthly states.** On the same page, open the **Variables** tab and click **New repository variable**. Name it `MONTHLY_STATES` and set the value to something like `MO KS` (or `ALL`).

## Running it

1. In the repo, open the **Actions** tab and select **Build solar call lists**.
2. Click **Run workflow** and fill in:
   - **states:** for example `MO KS`, or `ALL` for nationwide.
   - **max_roof_checks** and **max_regrid_lookups:** your spending caps per state.
3. When the run finishes (a green check), scroll to **Artifacts** at the bottom and download:
   - `solar-leads-XX` for each state: an Excel call list, a CSV for importing into a CRM or outreach tool, and an all-sites CSV.
   - `solar-leads-ALL-STATES-COMBINED`: one national Excel file and CSV.

**Run times**
- A single state takes from a few minutes to about an hour. Big states like CA and TX take the longest.
- If a run stops partway through, run it again. It picks up where it left off and re-uses everything already paid for.

**Monthly runs**
- On the 2nd of each month, the tool re-scans the `MONTHLY_STATES`.
- New sites are marked **NEW** in the "New this run" column.

## Selling it: customer lead packs

**Two kinds of buyer.** Set **offer** when you build a pack.

`cleaning` (the default) is for panel-cleaning and exterior-cleaning companies:
- Job estimates are per cleaning round, and the work is recurring.
- The default price is $12 per panel.

`coating` is for companies that sell hydrophobic or anti-soiling panel coatings:
- Job estimates are one-time jobs; the default is $6 per panel, which you should replace with the seller's own price.
- Each lead also gets an estimated **system size**, an **energy lost to dirt ($/yr)** figure and a **soiling risk** rating. Industrial, vehicle, trades and farm sites rate High and rank higher.
- The Cover sheet has editable inputs for watts per panel, yearly output per kW, power price and soiling loss (default 3%).
- The call guide leads with the owner's energy loss.
- The guide reminds callers to get the panel maker's or installer's OK in writing before coating, since some module warranties don't cover aftermarket coatings.

A lead pack is the product you sell: one customer's territory, cleaned up and ready to call.

**Make one on GitHub**
1. In the **Actions** tab, open **Build solar call lists** and click **Run workflow**.
2. Fill in the inputs:
   - **states:** `AUTO`. It picks the right states for the metro; Kansas City runs MO + KS.
   - **client_metro:** for example `Kansas City`. Run `python -m solarleads metros` for the list, or enter `LAT,LON`.
   - **client_radius:** the territory size in miles.
   - **client_name:** the customer. Use `Sample` for a demo; demos don't reserve the territory.
   - **price_per_panel:** your customer's cleaning price, used for job estimates.
3. When the run finishes, download **solar-leads-ALL-STATES-COMBINED**. The pack is in `packs/<customer>-<metro>/`.

**What's in a pack**

| File | For | What it is |
|---|---|---|
| `….xlsx` | The customer | **Cover** (lead counts, total panels, estimated cleaning value), **Leads** (scored and sorted, call tips, status drop-down), How to use, Data dictionary, Sources & license |
| `…-crm.csv` | The customer's CRM | Standard columns (Company, Phone, Street, City, State, Zip, Lead Source, Estimated Value…). Imports into Housecall Pro, Jobber, HubSpot or GoHighLevel. |
| `…-teaser.xlsx` | Your sales pitch | Territory totals plus the top 10 leads with names and phone numbers hidden. Send it to prospects free. |
| `manifest.json` | Your records | What was delivered and when |

**How the data is cleaned up**
- **Phones and websites:** phones are formatted as (816) 555-0199. Bad numbers are dropped, and websites are trimmed to the plain domain.
- **Duplicates:** two map entries for the same address are merged.
- **Head-office numbers:** a number used at 3 or more sites is flagged so the caller asks for that location's facilities manager.
- **Score (0–100):** confirmed panels and system size count most, then whether there's a phone, website and owner.
- **Estimated job value:** panels × your price per panel, with a minimum job. Edit the blue cells on the Cover sheet and every value updates.
- **Lead IDs:** each lead keeps the same ID every month, so monthly refreshes update the customer's CRM instead of duplicating it.
- **Formatting:** addresses and owner names are converted out of all-caps.

**Exclusivity**
- Each customer's territory is recorded when its pack is built.
- Building an overlapping territory for a different customer is refused, so you can't accidentally sell the same area twice. Use `--allow-overlap` to override on purpose.

From the command line:
`python -m solarleads package output/ --metro "Kansas City" --radius 40 --customer "Acme Exterior Cleaning" --price-per-panel 12`

**Before charging customers:** the license notice in each pack is a starting point. Have a lawyer confirm the terms for OpenStreetMap (ODbL) and Regrid redistribution.

## Running it on your own computer (optional)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-...        # optional, but needed for the roof check
export REGRID_TOKEN=...                # optional
python -m solarleads run MO            # results go to ./output
python -m solarleads combine output/   # merge states into one national file
```

## Settings (environment variables)

| Variable | Default | What it does |
|---|---|---|
| `MAX_ROOF_CHECKS` | 2000 (500 on GitHub) | Cap on AI roof checks per state per run |
| `MAX_REGRID_LOOKUPS` | 5000 (1000 on GitHub) | Cap on Regrid lookups per state per run |
| `MIN_CLUSTER_M2` | 120 | Arrays smaller than this with no business nearby are treated as homes |
| `PLACE_MATCH_METERS` | 60 | First-pass business search radius (before building footprints) |
| `USE_BUILDINGS` | 1 | Snap panels to building footprints (set 0 to turn off) |
| `OUTSIDE_MATCH_M` | 25 | If no business is inside the building, how far outside to look |
| `BUILDING_NEAR_M` | 20 | Carport and ground arrays: link to a building within this distance |
| `VISION_MODEL` | claude-haiku-5-5 | Model used for the roof check |
| `OVERPASS_URL` | overpass-api.de | Switch to another Overpass server if the main one is busy |
| `CACHE_IMAGES` | 0 | Set to 1 to keep the aerial photos (local runs) |

## Good to know

- **Coverage:** OpenStreetMap solar mapping is volunteer work. It's strong in some metros and thin in others, so the tool only finds sites someone has mapped. A later version can scan aerial photos directly to find unmapped arrays.
- **Regrid field names:** the tool follows Regrid's standard schema. Once you have a token, run `python -m solarleads regrid-test 39.22 -94.57` to see a real record and confirm the owner fields come through.
- **Owners vs. decision-makers:** the owner is often a holding company or an out-of-state tax address. To reach the facilities or property manager, call the business at the site.
- **Calling rules:** phone numbers come from public business listings. Before any automated dialing or texting, scrub them against the Do Not Call list and check for cell numbers (TCPA rules).
- **Fair use:** the tool waits between map requests and uses at most 3 parallel jobs to stay polite to the free public servers. Keep it that way.

## Tests

```bash
python -m pytest -q
```

The tests run offline using real Kansas City Northland sites that were checked by hand.
