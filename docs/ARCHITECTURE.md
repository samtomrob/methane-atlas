# Methane Atlas — System Architecture

_Working name: **Methane Atlas** (AU + PNG). Status: design draft, 2026-08-14. Companion docs: [BUILD_PLAN.md](BUILD_PLAN.md), [DATA_SOURCES.md](DATA_SOURCES.md)._

## 1. Design goals & constraints

1. **Free/open data only.** Every layer must come from a source that is free to access and legal to republish with attribution (CC BY or equivalent). No commercial satellite tasking.
2. **Near-zero hosting cost.** Static-first architecture: the map must work with no application server. Target ≤ $5/month, with a $0 configuration possible.
3. **Reproducible pipeline.** All data artifacts are produced by scripted, scheduled pipeline runs from raw sources — nothing hand-edited.
4. **AU + PNG region of interest.** Working bbox: `[108°E, -45°S] → [160°E, 1°N]` (covers all of Australia incl. Tasmania, PNG incl. Bougainville, and offshore basins: Bonaparte, Browse, Carnarvon, Gippsland, Gulf of Papua).
5. **Honest about physics.** TROPOMI (~5.5×7 km pixels) shows regional enhancement, not facility-level flux; point-source imagers (EMIT, Carbon Mapper, MethaneSAT archive) detect large plumes intermittently. The UI must not imply attribution precision the data doesn't support (confidence tags on every facility↔plume association).

## 2. High-level shape

Three planes, connected only by object storage:

```
┌────────────────────────────────────────────────────────────┐
│ PIPELINE (Python, scheduled via GitHub Actions cron)       │
│  fetch → filter/grid → composite → tile → publish          │
└──────────────────────────┬─────────────────────────────────┘
                           ▼
┌────────────────────────────────────────────────────────────┐
│ STATIC DATA STORE (Cloudflare R2 or Backblaze B2 + CDN)    │
│  *.pmtiles (raster + vector) · *.cog.tif · *.geojson       │
│  *.json (time series, plume index, status manifest)        │
└──────────────────────────┬─────────────────────────────────┘
                           ▼ HTTP range requests only
┌────────────────────────────────────────────────────────────┐
│ WEB APP (Next.js on Vercel, static/SSG)                    │
│  MapLibre GL + deck.gl · time slider · layer control       │
│  facility panel · charts · permalinks                      │
└────────────────────────────────────────────────────────────┘
```

No database, no tile server, no API server for the MVP. Everything the browser needs is a static file fetched by HTTP range request (PMTiles, COG) or plain GET (GeoJSON/JSON). Optional serverless functions (Vercel) come later only for search and share-link metadata.

## 3. Data plane

### 3.1 Concentration rasters (TROPOMI XCH4)

- **Input**: Sentinel-5P L2 `CH4` (bias-corrected XCH4, qa filtering ≥ 0.5), from **CDSE only** — one source for both backfill and ongoing updates (D-3, revised).
- **Measured volume** (OData query, 2026-08-14): **25 granules/week** intersect the ROI at **~60 MB each** = 1.5 GB/week; **571 GB** for the full 2019-02→now backfill. That is <5% of the 12 TB rolling-30-day allowance, so the one-time backfill is a single overnight run. Granules are processed and discarded — only composites persist (~2 GB).
- **Access pattern — search over OData, download over S3** (CDSE's own recommendation): OData results carry an `S3Path` per product; bytes come from the `eodata` bucket at `https://eodata.dataspace.copernicus.eu` (region `default`).
- **Hard limits to respect**: **4 concurrent S3 connections** (the parallelism cap), 2000 requests/min, 12 TB per rolling 30 days, and a **10-minute access token** — long runs refresh mid-flight. Cross-host download redirects drop the auth header, so redirects are followed manually with the token re-attached each hop.
- **Gridding**: xarray, 0.05° target grid, qa_value ≥ 0.5 mask, area-weighted binning of L2 pixels. Working from L2 rather than a pre-binned L3 is what lets us set the quality threshold, compute per-cell observation counts, and derive uncertainty honestly.
- **Backfill execution**: chunked by year, resumable, idempotent per period (`status.json` records the last completed period), so an interrupted run resumes rather than restarts.
- **Earth Engine: dropped.** It would have saved a few hours of one-time download at the cost of a second account, an OAuth service-account flow, a monthly compute quota, and a noncommercial-only platform licence on an otherwise commercially-usable data path. `matlas gee-login` and the EE code path remain in the tree as an optional shortcut, unused by default.
- **Products** (per period: ISO week and calendar month):
  - `xch4_mean` — mean column XCH4 (ppb)
  - `xch4_anom` — anomaly vs. rolling 90-day regional median (ppb) — this is the "where is it elevated" layer
  - `valid_obs` — count of valid retrievals (honesty layer; PNG will be sparse due to cloud)
- **Storage format**: one COG per product-period (`rasters/xch4/weekly/2026-W32_mean.tif`), plus rendered **raster PMTiles** per product (z3–z8, 256px, fixed colormap) for cheap map display. COGs stay available for client-side point queries (geotiff.js) so clicking the map shows the numeric value.
- **Size estimate**: ROI at 0.05° ≈ 1040×920 px. ~390 weekly + ~90 monthly periods since 2019-02 ≈ **< 2 GB total** including tiles. Trivial for free-tier object storage.

### 3.2 Infrastructure vectors

- **Compilation**: quarterly pipeline job merges sources (GEM trackers, Geoscience Australia, OGIM, OSM for PNG gaps, Open Electricity) into one GeoPackage, deduplicates (name+distance heuristics, source priority order), and emits a **single `infrastructure.pmtiles`** (tippecanoe) with layers:
  - `pipelines` (lines), `lng_facilities`, `gas_plants`, `coal_mines`, `oil_gas_fields` (polygons where available), `wells` (z10+ only), `other_facilities` (landfills etc., later)
- **Canonical attribute schema** (every feature):
  `id, layer, name, subtype, status, operator, owner, basin, fuel, capacity, capacity_unit, country, source, source_id, source_url, gem_ch4_estimate (coal mines), nger_facility_id (AU join), reported_scope1_tco2e, reported_ch4_tco2e (Safeguard "GHG Methane" column), fugitive_method (NGER Method 1–4), first_seen, last_verified`
- Geometry notes: gas/oil **fields are centroid points** (GOGET has no polygons); GA shows WA/NT onshore pipelines as **corridor polygons** (render distinctly); GEM route GeoJSON may have null geometries for unmapped projects (fall back to GA lines or point-pair placeholder).
- **NGER join** (AU only): match Clean Energy Regulator facility-level reported emissions to map features by name/operator fuzzy match, manually reviewed once (the list is only a few hundred facilities). This powers the **reported-vs-observed** view.
- License note: OSM features (ODbL) are kept in a **separate layer/file** from CC BY layers rather than merged into derived geometries, to keep the share-alike boundary clean.

### 3.3 Point-source plumes

- **Inputs** (each a small fetcher): Carbon Mapper API, EMIT plume complexes (CMR/LP DAAC), IMEO/MARS downloads, MethaneSAT archive (static, one-time ingest), SRON weekly list if still published.
- **Normalized schema**:
  `plume_id, provider, satellite/instrument, datetime_utc, lat, lon, emission_rate_kghr, uncertainty_kghr, sector_hint, scene_id, provider_url, geometry (point + optional plume polygon)`
- **Facility association**: nearest `infrastructure` feature within 2 km → `associated_facility_id` + `association_confidence` (high <500 m single candidate / medium / low). Never presented as attribution, always "nearest infrastructure".
- **Storage**: per-year GeoJSON for the map (clustered client-side), one Parquet for analytics. Refreshed daily where the provider updates that often, else weekly.

### 3.4 Analytics artifacts (Phase 3)

Precomputed JSON, no server:
- Per-facility and per-basin XCH4 anomaly time series (mean over a 0.3° box minus regional background, weekly).
- Plume event history per facility.
- Reported-vs-observed table (NGER reported scope-1 vs. plume counts/anomaly percentile) — CSV + JSON.
- `status.json` manifest: per-layer last-update timestamp + coverage stats, rendered on an "data freshness" page and used by CI checks.

### 3.5 Deforestation & carbon module (Phase 4)

- Overlay layers via provider tile services where licensing allows (GFW tree-cover-loss & alerts for PNG; DEA services for Australia), rather than re-hosting global rasters.
- "Carbon lost" estimates for a drawn/clicked polygon via GFW API zonal statistics (emissions from the Harris carbon-flux layers) with clearly stated uncertainty; Australia fallback: area × ESA CCI biomass × IPCC defaults computed in a serverless function or precomputed per region.
- Exact endpoints/licensing per DATA_SOURCES.md (verified 2026-08).

## 4. Web app

- **Stack**: Next.js (static export where possible) + **MapLibre GL JS** + `pmtiles` protocol; deck.gl only if/when we need heavy point rendering (plume clusters may be fine in MapLibre alone). Charts: uPlot or ECharts. TypeScript throughout.
- **Basemap**: no-key options in order of preference — OpenFreeMap hosted vector tiles; else self-hosted Protomaps `pmtiles` basemap extract for the ROI (~1–2 GB, fits R2 free tier); Carto free tier as fallback. Decision at build time based on availability.
- **Core UI**:
  - Layer control grouped: *Methane* (mean / anomaly / observation count), *Point sources* (by provider), *Infrastructure* (by type, filter by status/operator/basin), *Context* (basins, admin boundaries).
  - **Time slider** (weekly/monthly) with play button; plume layer filters to slider window.
  - Click facility → side panel: attributes, reported emissions (AU), nearby plume history, XCH4 anomaly sparkline, links to sources.
  - Click anywhere → XCH4 value + obs count for current period (COG point query).
  - Permalink encoding view + layers + date (URL params, like the MethaneSAT portal the user referenced).
  - Methodology & attribution pages (required by CC BY sources; also the honesty layer for what the data can/can't say).
- **Design language**: dark map UI, colorblind-safe scientific colormaps (viridis/magma family for XCH4; distinct hues per provider for plumes). Follow `dataviz` skill guidance when building charts.

## 5. Pipeline & operations

- **Language/tooling**: Python 3.12, `uv`-managed; xarray + netCDF4, rasterio + rio-cogeo, geopandas + shapely, tippecanoe & pmtiles CLI (run in CI via Docker), pyarrow. Each stage is a CLI (`matlas fetch-tropomi`, `matlas composite`, `matlas tile`, `matlas plumes`, `matlas infra`) so agents/CI/humans run identical entry points.
- **Scheduling**: GitHub Actions cron —
  - daily: plume fetchers (~2 min)
  - weekly (Mon 04:00 UTC): TROPOMI weekly composite + anomaly + tiles (~10–20 min)
  - monthly: monthly composite; quarterly: infrastructure recompile
  - Backfill 2019→now: one supervised batch run (chunked by year), executed once at build time.
- **Secrets** (GitHub Actions secrets, user-provided): CDSE token, Earthdata login, Carbon Mapper token, GFW API key, EE service-account JSON (if Route A), R2/B2 access keys.
- **Publishing**: `rclone`/S3 API sync to bucket; artifacts are immutable per period (idempotent re-runs safe); `status.json` written last as the commit marker.
- **QA gates in CI**: schema validation (pydantic models for plume/infra records), min-valid-pixel thresholds before publishing a composite, geometry validity checks, license/attribution manifest check (every layer must map to an entry in DATA_SOURCES.md), Playwright smoke test on the deployed preview (map loads, layers toggle, no console errors).

## 6. Hosting & cost

| Component | $0 option | Small-budget option |
|---|---|---|
| Web app | Vercel Hobby | Vercel Pro (only if traffic demands) |
| Data store/CDN | Backblaze B2 (10 GB free) + Cloudflare proxy | Cloudflare R2 (~$0.10/mo at our sizes, no egress fees; needs card on file) |
| Compute | GitHub Actions free minutes (public repo = unlimited) | same |
| Basemap | OpenFreeMap / self-hosted PMTiles | same |
| Earth Engine (Route A) | Free noncommercial tier | — |

Total steady-state data volume estimate: **3–6 GB** (rasters + tiles + basemap extract + plumes + infra). Well inside free tiers.

## 7. Repository layout (monorepo)

```
methane-atlas/
├── pipeline/                 # Python package `matlas`
│   ├── src/matlas/{tropomi,plumes,infra,tiling,publish}/
│   └── tests/                # golden-file tests per fetcher/gridder
├── web/                      # Next.js app
├── data-catalog/             # one YAML per source: urls, license, cadence, schema map
├── .github/workflows/        # cron + CI + deploy
└── docs/                     # these documents + methodology page source
```

## 8. Key risks

| Risk | Mitigation |
|---|---|
| Source drift (APIs/URLs change, e.g. MethaneSAT archive relocation) | `data-catalog/` YAML is the single place URLs live; daily CI probe job fails loudly; status page shows staleness |
| PNG cloud cover → sparse TROPOMI retrievals | Ship `valid_obs` layer; lean on point-source imagers + monthly (not weekly) composites for PNG messaging |
| Offshore facilities invisible to TROPOMI (no glint retrievals in standard product) | State it in methodology; offshore coverage comes from plume imagers only |
| Facility attribution overclaim | Confidence-tagged "nearest infrastructure" language everywhere; no automatic "X is emitting" statements |
| EE noncommercial terms (Route A) | Route B (CDSE) is the ongoing path; EE used only for backfill |
| EE 150 EECU-hr/month quota stalls the backfill | Chunked, resumable, immutable per-period outputs; upgrade path is the Contributor tier (needs billing) if it ever binds |
| GEM/OSM license mixing | Layer-level source separation; attribution page generated from data-catalog |
