# Methane Atlas — Build Plan

_Design draft 2026-08-14. This document is the build contract: phases, tasks, acceptance criteria, and the decisions still owned by the project owner. Data source details live in [DATA_SOURCES.md](DATA_SOURCES.md); system design in [ARCHITECTURE.md](ARCHITECTURE.md)._

## 1. Vision

A single public web map answering, for Australia and PNG: **"Where is methane elevated, what infrastructure is there, and does what we observe match what's reported?"** — extended later with deforestation/forestry layers and carbon estimates.

### Why this beats the MethaneSAT portal (the reference the owner likes)

The MethaneSAT portal is excellent but is a **single-instrument archive** — the satellite was lost in June 2025, so its map ends there, and it only ever covered pre-selected targets. Methane Atlas is different by design:

1. **Multi-source fusion** — TROPOMI's daily, ongoing, wall-to-wall coverage as the base; point-source plume detections from EMIT, Carbon Mapper/Tanager, IMEO/MARS, and the frozen MethaneSAT archive layered on top. One map, all public instruments.
2. **Infrastructure-first** — every gas pipeline, LNG facility, oil/gas field, gas generator and coal mine as first-class, filterable features with attributes — not just background context.
3. **Reported vs. observed (Australia)** — join NGER/Safeguard facility-reported emissions to the map so users can compare official numbers with satellite observation. No public tool does this for Australia today.
4. **Regional focus** — AU+PNG cropping makes weekly composites, per-basin analytics and full history (2019→now) cheap and fast, where global portals are slow and shallow.
5. **Time as a first-class axis** — scrub weekly/monthly history, watch hotspots evolve, permalink any view.
6. **Carbon module** — deforestation alerts + carbon-loss estimates in the same place (Phase 4), which no methane portal offers.

### Non-goals (v1)

- No custom L2 retrievals from radiances (we consume published products only).
- No sub-daily/"live" monitoring — best latency is ~1–3 days (TROPOMI offline stream) and plume providers update on their own schedules.
- No automated public accusations: the app shows observations + nearest infrastructure with confidence tags, never "facility X is leaking".
- No agriculture/livestock methane in v1 (diffuse area source; revisit later).

## 2. Ground truth that shapes the plan

_(verified via research agents 2026-08-14; details & links in DATA_SOURCES.md)_

- **MethaneSAT is dead; its archive is not — but it's license-constrained.** Satellite lost June 2025; archive (Mar 2024–Jun 2025) is live and still growing as backlog scenes are processed. Its custom license **prohibits redistributing raw L3/L4** (derivatives allowed), and Earth Engine access is behind a request form. So it becomes a **derived/link-out layer**, not a re-hosted one — and Australian scene coverage still needs interactive confirmation on their portal.
- **The 2026 surprise wins**: UNEP's MARS expanded to **coal and waste** this year (exactly Australia's sector mix), its MARS-S2L feed gives us Sentinel-2/Landsat plume detections without running our own retrievals, and GFW's integrated deforestation alerts went **global in Jan 2026** — Australia now has near-real-time clearing alerts, not just the tropics.
- **Several key sources are noncommercial-licensed** (Carbon Mapper, UNEP IMEO CC BY-NC-SA, Open Methane CC BY-NC, Earth Engine free tier). Fine for a public-good tool; blocks future commercialization unless those layers are dropped or relicensed → decision D-7.
- **TROPOMI is the only always-on methane map** (daily, ~5.5×7 km, 2018/19→now, free). It shows regional enhancement, not facilities. Offshore: standard product has essentially no open-water retrievals (sun-glint only) — offshore LNG/platforms will only ever appear via plume imagers.
- **Point sources come from tasked imagers** (EMIT ~60 m, Carbon Mapper/Tanager ~30 m, historical MethaneSAT): sporadic revisit, but they quantify kg/hr per plume and both have publicly documented Australian detections (e.g. Bowen Basin coal mines).
- **PNG will be cloud-limited.** Tropical cloud cover slashes valid TROPOMI retrievals; monthly composites + an honest "observation count" layer are the answer, with plume imagers as the sharp instrument.
- **Australia's biggest observable sources are coal mines** (esp. Bowen Basin underground mines and Hunter Valley open cuts) — hence coal-mine attributes (incl. per-mine methane estimates where GEM publishes them) get first-class treatment.
- **Infrastructure data is a solved problem — better than hoped.** GEM publishes pipeline **route GeoJSON on GitHub (no form), including the PNG LNG Hides→Port Moresby route** that OSM lacks; GCMT has **per-mine methane estimates** for every Australian coal mine (Aug 2026 release); Geoscience Australia's pipeline REST service exports GeoJSON directly; OGIM v2.7 (CC BY, no registration) fills wells/processing. Gas fields come as centroids only (no polygons) — the map shows labeled field markers, not acreage.
- **Australia publishes facility-level reported methane.** The CER Safeguard table has per-facility "GHG Methane" (tCO2-e) columns, and NGER "methods data" discloses which fugitive-estimation method (1–4) each coal/O&G facility uses. Reported-vs-observed can therefore be **methane-vs-methane**, with the estimation-method caveat displayed — sharper than any comparison MethaneSAT's portal offered.

## 3. Phases

Each phase ships something usable. Tasks are written to be dispatched to build agents; each has acceptance criteria (AC).

### Phase 0 — Foundations (needs owner inputs first — see §5)

| # | Task | AC |
|---|---|---|
| 0.1 | Repo scaffold (monorepo per ARCHITECTURE §7), CI skeleton, lint/test wiring | CI green on empty packages |
| 0.2 | `data-catalog/` YAML for every source in DATA_SOURCES.md + probe script | `matlas probe` hits every URL, reports auth OK/missing, exits nonzero on dead links |
| 0.3 | Secrets wiring (local `.env` + GH Actions secrets doc) | probe passes with owner-supplied tokens |
| 0.4 | Bucket + Vercel project + deploy hello-map (basemap only, ROI framed) | public URL loads basemap over AU/PNG |

### Phase 1 — MVP: methane background + infrastructure (the "better map" moment)

| # | Task | AC |
|---|---|---|
| 1.1 | TROPOMI fetch+grid+composite (weekly/monthly mean, anomaly, valid-obs) for a 3-month pilot window | COGs match reference stats (spot-check vs Copernicus browser values ±2 ppb) |
| 1.2 | Raster tiling to PMTiles + colormap/legend spec | tiles render in MapLibre, values legible AU-wide at z4–z8 |
| 1.3 | Infrastructure compile v1: GEM (pipeline routes repo, LNG, GOGPT gas plants, GCMT coal mines + CH4 estimates, GOGET fields) + Geoscience Australia pipelines/mines + OGIM v2.7 wells/processing + OSM PNG facility supplement (license-separated) | `infrastructure.pmtiles` with canonical schema; ≥95% of AU LNG trains/coal mines present vs. checklist; PNG LNG chain present end-to-end (GEM route) |
| 1.4 | Web app v1: layer control, time slider, facility click-panel, XCH4 point query, permalinks, attribution page | Lighthouse ≥90 perf on mid-tier laptop; all layers toggle; deep link reproduces view |
| 1.5 | Backfill 2019→now weekly+monthly after pilot validated | full history scrubbable; total bucket ≤6 GB |
| 1.6 | Scheduled weekly update job | two consecutive unattended weekly runs publish + status.json updates |

### Phase 2 — Point-source plumes

| # | Task | AC |
|---|---|---|
| 2.1 | Fetchers: Carbon Mapper API (JWT), EMIT v002 plumes (CMR-STAC), IMEO/MARS CSV/GeoJSON, SRON weekly CSV → normalized plume schema | each fetcher has golden-file test; dedup across providers (same event <1 km & <2 h) |
| 2.1b | MethaneSAT archive: request GEE access (owner form), confirm AU scenes; if present, build **derived** summary layer (scene footprints + our stats) + portal link-outs — no raw re-hosting (license) | AU coverage confirmed yes/no; derived layer or documented skip |
| 2.2 | Facility association (nearest-infra ≤2 km, confidence tiers) | manual audit of 30 random AU plumes: ≥90% association judged sensible; language never implies proof |
| 2.3 | Map layer: provider-colored points, cluster, date-window filter, plume detail popup (rate ± uncertainty, provider link) | all providers visible; popups show provenance |
| 2.4 | Daily plume cron | unattended for 7 days, status page shows freshness |

### Phase 3 — Analytics & the "reported vs observed" flagship

| # | Task | AC |
|---|---|---|
| 3.1 | Safeguard/NGER facility join to map features (fuzzy match + one-time manual review file), carrying **per-facility reported methane (tCO2-e)** and **fugitive-estimation Method 1–4** attributes | ≥90% of safeguard oil/gas/coal facilities in ROI matched; mismatches listed; CH4 + method fields populated |
| 3.2 | Per-facility & per-basin XCH4 anomaly time series + plume history JSON | facility panel shows sparkline + events; basin pages for Bowen, Surat, Hunter, Cooper, Carnarvon, Gulf of Papua |
| 3.3 | Reported-vs-observed table view + CSV export | sortable table; downloads reproduce UI numbers |
| 3.4 | Hotspot detector: persistent weekly anomaly > threshold flags region | flags Bowen Basin in backtest; false-positive review doc |

### Phase 4 — Deforestation & carbon module

| # | Task | AC |
|---|---|---|
| 4.1 | Tree-cover loss (Hansen v1.13, through 2025) + **GFW integrated alerts (global since Jan 2026 — covers AU too)** tile layers; PNG palm/logging concessions (vintage-flagged) | layers render both countries; alert dates filterable; provenance per country noted |
| 4.2 | AU validated-change layers: DEA Land Cover WMS + QLD SLATS S2 | AU woody loss visible; methodology page explains official-vs-GFW differences |
| 4.3 | Carbon estimate on click/draw via **GFW Data API zonal stats** (gross emissions 2001–2025, one POST per polygon); ESA CCI AGB v7.0 for stock context | polygon returns tCO2e with stated uncertainty band; validated against GFW dashboard for 3 test areas |
| 4.4 | ACCU area-based project boundaries overlay (data.gov.au `erf_project_mapping`, confirmed available) with register join via PROJ_ID | layer + link to register entries |

### Phase 5 — Hardening & polish

Monitoring/alerting on pipeline failures; methodology page finalized; mobile layout; OG-image share cards; performance pass; optional: email digest of new large plumes in ROI.

## 4. Build execution model (for the agent team)

- **Workstreams**: `pipeline` (Python), `web` (Next.js/MapLibre), `data` (catalog/QA), each owned by a dedicated agent per phase; an integration/QA agent verifies ACs before a phase closes. Phases 1.1–1.3 parallelize cleanly after 0.x.
- **Definition of done** everywhere: AC met + tests + docs + attribution entries + deployed preview verified in the browser (screenshots in PR).
- **Conventions**: conventional commits; every dataset touch goes through `data-catalog/`; no secrets in repo; PR previews via Vercel.

## 5. Decisions — RESOLVED 2026-08-14 (owner: "go with your recommendations")

| ID | Decision | Resolution |
|---|---|---|
| D-1 | Hosting budget | **Cloudflare R2** (~$0–1/mo; owner to create account + bucket) |
| D-2 | Public or private v1 | **Public GitHub repo from day one** (owner-approved publishing) |
| D-3 | TROPOMI route | ~~Earth Engine for backfill + CDSE ongoing~~ → **REVISED 2026-08-14: CDSE for everything; Earth Engine dropped.** Measured the full 2019→now backfill for the ROI at **571 GB** (25 granules/week × ~60 MB, 392 weeks) — under 5% of CDSE's 12 TB rolling-30-day allowance and roughly one overnight run. Earth Engine added a second account, an OAuth flow, a compute quota and a noncommercial-only platform licence to save a few hours of one-time download. Working from raw L2 also lets us control qa filtering and gridding rather than inheriting Google's pre-binned L3. |
| D-4 | MVP temporal grain | **Weekly + monthly composites** |
| D-5 | History depth | **2019 → now** |
| D-6 | Name & domain | **"Methane Atlas" as working name**; final name/domain still open (differentiate from openmethane.org) — non-blocking |
| D-7 | Commit to noncommercial | **Committed: noncommercial public-good use** → NC layers (Carbon Mapper, IMEO, Open Methane, Open Electricity) are in; attribution page must state this |

## 6. Accounts & keys needed from the owner

All free. I can't create accounts on your behalf — I'll wire the tokens into `.env`/CI secrets once you have them. Phase 0 can start with just the first two.

| Needed for | Account | Where | Phase |
|---|---|---|---|
| TROPOMI (Route B / ongoing) | Copernicus Data Space account | dataspace.copernicus.eu | 1 |
| TROPOMI backfill (Route A) | Google Earth Engine noncommercial registration | earthengine.google.com | 1 |
| EMIT plumes | NASA Earthdata login | urs.earthdata.nasa.gov | 2 |
| Carbon Mapper plumes | Portal account → JWT token | data.carbonmapper.org | 2 |
| MethaneSAT archive (optional) | GEE access request form | methanesat.org / portal | 2 |
| GEM spreadsheets | Per-tracker download form (email delivery) | globalenergymonitor.org/download-data | 1 |
| Deforestation & carbon | GFW account → API key | globalforestwatch.org/my-gfw | 4 |
| Data hosting | Cloudflare R2 **or** Backblaze B2 | per D-1 | 0 |
| Web hosting | Vercel (already connected via MCP?) | vercel.com | 0 |

**No account needed** for: UNEP IMEO downloads, SRON weekly CSVs, GEM pipeline-routes GitHub repo, OGIM (Zenodo), Geoscience Australia REST services, CER CSVs, Open Electricity facility GeoJSON, GFW/DEA map tiles, OSM Overpass.

## 7. Risks (top 5)

1. **Source drift** — mitigated by data-catalog probes + status page (ARCHITECTURE §8).
2. **PNG expectations** — cloud cover means PNG is monthly-composite + plume-imager territory; set messaging accordingly.
3. **Attribution overclaim** — confidence-tag language, methodology page, no auto-accusations.
4. **License mixing** (ODbL vs CC BY) — layer separation enforced in pipeline.
5. **Single-maintainer bus factor** — everything scripted + documented; no manual steps in the loop.
