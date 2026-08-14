# Methane Atlas — Data Source Catalog

_All entries verified 2026-08-14 via primary sources. Each entry: what it is, access route, auth, license, AU/PNG applicability, role in the app. This file is the source of truth mirrored by `data-catalog/*.yaml` at build time._

**Licensing legend** — ⚠️NC = noncommercial-use license: fine for this project as a public-good tool, but these layers must be dropped/replaced if the app is ever commercialized. See BUILD_PLAN decision D-7.

---

## 1. Methane observations (satellite)

### 1.1 Sentinel-5P TROPOMI L2 CH4 — the always-on backbone

- **What**: column-averaged dry-air methane (XCH4, ppb), product `L2__CH4___` (OFFL + RPRO reprocessed streams; no near-real-time for CH4). ~5.5×7 km pixels, daily ~13:30 LT overpass, land + sun-glint only (no standard open-water retrievals). NetCDF4. Latency ~1–5 days.
- **Access** (Copernicus Data Space Ecosystem, CDSE): STAC `https://stac.dataspace.copernicus.eu/v1/` · OData `https://catalogue.dataspace.copernicus.eu/odata/v1/Products` · openEO `https://openeo.dataspace.copernicus.eu/` · Sentinel Hub `https://sh.dataspace.copernicus.eu/` (one band/request limit) · S3 `eodata.dataspace.copernicus.eu`. Docs: `https://documentation.dataspace.copernicus.eu/APIs.html`. Search anonymous; **download needs free CDSE account + OAuth2 token**.
- **Note**: S5P-PAL (`https://data-portal.s5p-pal.com/`) no longer lists CH4 (as of Mar 2026) — use CDSE RPRO for reprocessed history.
- **License**: Copernicus Sentinel Data Terms — free incl. commercial use; attribute "Contains modified Copernicus Sentinel data (2026)".
- **AU/PNG**: excellent yield over arid/semi-arid Australia (Bowen Basin & Hunter Valley enhancements documented in peer-reviewed work); PNG Highlands sparse (cloud, terrain, dark surfaces) → monthly composites + `valid_obs` honesty layer.
- **Role**: weekly/monthly mean + anomaly + observation-count rasters (ARCHITECTURE §3.1). Pipeline route B.

### 1.2 Sentinel-5P L3_CH4 on Google Earth Engine — cheap backfill route

- **What**: `COPERNICUS/S5P/OFFL/L3_CH4` image collection, qa-filtered gridded XCH4 (band `CH4_column_volume_mixing_ratio_dry_air_bias_corrected`), 2019-02-08 → present (~3-day lag). Verified current through 2026-08-11 at check time.
- **Access**: Earth Engine Python API; export ROI weekly/monthly means to GeoTIFF. Catalog: `https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S5P_OFFL_L3_CH4`.
- **License**: Copernicus data terms; **EE platform itself is ⚠️NC tier** (free for individuals/nonprofits/research; commercial use is paid). `https://earthengine.google.com/noncommercial/`
- **Role**: pipeline route A — 2019→now backfill in one batch job; CDSE route B remains the platform-independent ongoing path.

### 1.3 MethaneSAT archive — historical high-res layer (constrained)

- **What**: satellite **lost 2025-06-20** (power failure, unrecoverable; ~400 contact attempts). Archive of ~Mar 2024–Jun 2025 targets remains **live and still growing** as backlog scenes are processed. Products: L3 XCH4 (~30 m grid), L4 area flux (4 km, kg/h + CI), L4 point sources.
- **Access**: portal `https://portal.methanesat.org/` (open); Google Earth Engine (e.g. `projects/edf-methanesat-ee/assets/public-preview/L4area_v2`) + Google Cloud Storage — **GEE access via request form**.
- **License**: **MethaneSAT Content License (custom, not CC)** — free to use, but **raw L3/L4 redistribution prohibited**; derivative products allowed.
- **AU/PNG**: ⚠️ unverified — GEE public-preview covers US/Turkmenistan/Venezuela regions only; Australian scenes (e.g. Hunter Valley, visible on their portal UI) need interactive confirmation on the portal.
- **Role**: **link-out + derived layer only** (e.g. our own summary of scene footprints/statistics), not raster re-hosting. Owner action: submit GEE access request form; confirm AU scenes exist. If AU coverage is thin, this layer is a nice-to-have, not a pillar.
- **Successor**: none announced; MethaneAIR aircraft flights resumed 2025 (US-focused).

### 1.4 NASA EMIT methane products — point-source plumes with rates

- **What**: imaging spectrometer on ISS (extended mission, operating). **V002** (current since 2026-02; V001 decommissioned 2026-03-26): `EMITL2BCH4PLM` 60 m plume complexes (COG + GeoJSON) **now including emission-rate estimates + uncertainties, manually reviewed**; `EMITL2BCH4ENH` enhancement rasters. Coverage 2022-08 → present, ±51.6° lat.
- **Access**: free NASA Earthdata Login; CMR-STAC `https://cmr.earthdata.nasa.gov/stac/LPCLOUD` (concept `C3242707413-LPCLOUD`); no-login browse portal (VISIONS): `https://earth.jpl.nasa.gov/emit/data/data-portal/Greenhouse-Gases/`. DOI `10.5067/EMIT/EMITL2BCH4PLM.002`.
- **License**: NASA open, no restrictions; cite DOI.
- **AU/PNG**: full coverage of both; sporadic revisit; arid AU favorable, PNG cloud-limited.
- **Role**: plume fetcher #1 (Phase 2), normalized into the plume schema.

### 1.5 Carbon Mapper (Tanager-1 + EMIT + aircraft) — richest plume catalog

- **What**: published CH4/CO2 point-source plumes with rates: Tanager-1 (~30 m, launched Aug 2024, publishing plumes ~30 days post-observation), EMIT, AVIRIS-3/GAO aircraft. **~271 Australian plumes since Jan 2023 reported, mostly coal, ~51 in the Bowen Basin.**
- **Access**: portal `https://data.carbonmapper.org/`; API `https://api.carbonmapper.org/api/v1/docs` — REST (`/catalog/plumes/annotated`, `/catalog/plume-csv`) + STAC (`/api/v1/stac/`). **Free registration; JWT via `/api/v1/token/pair`.** Rate-limited.
- **License**: ⚠️NC — free for noncommercial (research, journalism, government, nonprofit) with attribution; read portal Terms before launch (exact instrument unverified).
- **Role**: plume fetcher #2 — likely the single most valuable point-source feed for AU coal.

### 1.6 UNEP IMEO "Eye on Methane" / MARS — validated multi-instrument detections

- **What**: Methane Alert and Response System detections, published 30 days post-event; **expanded in 2026 from oil/gas to coal and waste** (directly relevant to AU). Includes **MARS-S2L**: operational Sentinel-2/Landsat large-plume detection, validated, published bi-weekly — this is the practical route to S2/Landsat methane, no need to run our own.
- **Access**: `https://methanedata.unep.org/` → `/download-dataset`: CSV/GeoJSON/XLS/JSON, **no login**. No formal API documented. (Site 403s scripted fetches — pipeline needs a polite UA / manual-refresh fallback.)
- **License**: ⚠️NC — CC BY-NC-SA 4.0, credit UNEP IMEO.
- **Role**: plume fetcher #3; SA (share-alike) noted in attribution page.

### 1.7 SRON weekly TROPOMI plume list

- **What**: ML-detected TROPOMI super-emitter plumes, weekly CSV, current (week 32, 2026-08-10 verified). Feeds CAMS Methane Hotspot Explorer.
- **Access**: `https://www.sron.nl/methane-emissions/` — per-week CSV, no login.
- **License**: CC BY 4.0; credit SRON, cite Schuit et al. (2023).
- **Role**: plume fetcher #4 (coarse, 7 km localization — flagged lower precision in UI).

### 1.8 Open Methane (Superpower Institute) — AU modeled emissions layer

- **What**: inverse-model **emissions** (not concentrations) on a 10×10 km grid, Australia only, TROPOMI-driven. Open code (`https://github.com/openmethane`).
- **Access**: `https://openmethane.org/` + `/data`; batch model outputs; no documented REST API.
- **License**: ⚠️NC — CC BY-NC (free relicensing offered for public-good use — worth emailing them).
- **Role**: optional AU overlay ("modeled emissions" vs our "observed concentrations"); also the closest prior art — our differentiators: PNG, infrastructure depth, point-source fusion, reported-vs-observed.

### 1.9 Watchlist (not usable yet / niche)

- **GOSAT-GW (TANSO-3)**: launched 2025-06-29; 1–3 km CH4 mapping; L1B public "after July 2026 (TBD)", L2 later — **revisit each quarter**, potential major upgrade.
- **GOSAT/GOSAT-2**: sparse ~10 km point footprints; long-term background only. Free registration (`https://data2.gosat.nies.go.jp/`).
- **GHGSat via ESA TPM**: 25 m, but research-proposal-gated; not an open bulk feed.

---

## 2. Infrastructure (pipelines, LNG, fields, generators, coal mines)

### 2.1 Global Energy Monitor (GEM) — the backbone for both countries

All trackers **CC BY 4.0** (`https://globalenergymonitor.org/creative-commons-public-license/`). Downloads via per-tracker form (name/email, xlsx delivered); hub `https://globalenergymonitor.org/download-data`.

- **Pipelines — GGIT (gas) + GOIT (oil/NGL)**: attributes in xlsx; **route geometries published as per-project GeoJSON linestrings on GitHub, no form needed**: `https://github.com/GlobalEnergyMonitor/GOIT-GGIT-pipeline-routes` (WGS84, ProjectID keys matching spreadsheets; null geometries where unmapped; repo lacks explicit LICENSE file — confirm CC BY before redistribution). **Includes the PNG LNG pipeline route (292 km onshore + 407 km subsea Hides→Port Moresby)** — critical, since OSM doesn't have it. Releases ~quarterly (pipelines Nov 2025; LNG terminals Sep 2025).
- **LNG terminals** (inside GGIT): points + capacity/status/owner incl. FLNG; PNG covered (PNG LNG terminal, Papua LNG, PAWA PNG FSRU).
- **Global Coal Mine Tracker (GCMT)**: point lat/lon per mine + production, type (UG/open-cut), ownership, **and per-mine methane emission estimates** (production × depth-based gas content; m³ and CO2-e). Latest release Aug 2026, annual. Mine **boundary polygons available on request**. PNG: no coal mines. This layer is the heart of the AU coal story.
- **GOGET (oil & gas extraction)**: fields/basins as **centroid points only** (no polygons), with status/ownership/production/reserves; Mar 2026 release; covers PNG fields (Hides etc.).
- **GOGPT (oil & gas power plants)** + **GCPT (coal plants)**: unit-level points; GOGPT Jan 2026, GCPT Jul 2026 (biannual). AU complete; PNG's small gas plants (NiuPower, Dirio ~45–58 MW) threshold-dependent — verify in xlsx, else OSM/manual supplement.

### 2.2 Geoscience Australia / Digital Atlas (AU authoritative geometry)

- **Oil & Gas Pipelines**: ArcGIS REST verified: `https://services.ga.gov.au/gis/rest/services/Oil_Gas_Pipelines/MapServer` (layers: Oil_Pipelines, Gas_Pipelines; polylines; `query?where=1=1&outFields=*&f=geojson`, paginate at 2000 records). Hub one-click GeoJSON: `https://digital.atlas.gov.au/datasets/digitalatlas::gas-pipelines/about`. CC BY 4.0. Quirks: WA/NT onshore shown as **corridor polygons** not lines; vintage 2022, irregular updates. AU only.
- **Mines**: `https://services.ga.gov.au/gis/rest/services/AustralianOperatingMines/MapServer` (points, updated weekly); OZMIN deposits `https://data.gov.au/data/dataset/34247a24-d3cf-4a98-bb9d-81671ddb99de` (CC BY 4.0).
- **Offshore**: AMSIS hub `https://amsis-geoscience-au.hub.arcgis.com/` (offshore petroleum wells + infrastructure/titles, GeoJSON downloads, CC BY 4.0); NOPTA spatial (NEATS titles) `https://www.nopta.gov.au/maps-and-public-data/spatial-data.html`; NOPIMS for well reports (not a facility layer).
- Note: `services.ga.gov.au` 403s non-browser directory listings; layer query URLs work — set a browser-ish User-Agent in fetchers.

### 2.3 Clean Energy Regulator (AU reported emissions — the join tables)

**No coordinates in any CER file** — join by facility name/operator to GEM/GA/OSM geometries (one-time manual review; a few hundred facilities).

- **Safeguard Mechanism baselines & emissions** (`https://cer.gov.au/markets/reports-and-data/safeguard-data/2024-25-baselines-and-emissions-data`, CSV/XLSX, annual): covered emissions, baseline, net position, **and per-facility per-gas breakdown: "GHG Methane" and "GHG Nitrous oxide" columns (tCO2-e) — verified.** This makes reported-vs-observed methane-specific, not just total scope 1.
- **NGER publications** (`https://cer.gov.au/markets/reports-and-data/nger-reporting-data-and-registers`): corporate emissions; **designated generation facility** CSV (per-power-station scope 1 — the gas generator join); facility-level scope 1&2 for safeguard facilities (2023-24→); **NGER methods data** disclosing which fugitive-CH4 estimation Method (1–4) each coal/O&G facility uses — display this: Method 1 default factors vs measurement is a live policy issue.
- **ACCU project register** (monthly, no coords) + **area-based project polygons**: `https://data.gov.au/data/dataset/erf_project_mapping` (a.k.a. `accu_project_mapping` — same dataset family; SHP/GeoJSON/WFS/WMS; savanna/vegetation methods only; landfill-gas & mine-gas projects are point-type, no boundaries). License CC BY (3.0 AU vs 4.0 — confirm per file).

### 2.4 EDF OGIM v2.7 (gap-filler, esp. wells/processing)

- `https://zenodo.org/records/15103476` — 3.1 GB GeoPackage, **CC BY 4.0, direct download, no registration** (also in GEE as `EDF/OGIM/current`). Wells (4.5M points), pipelines, compressor stations, gas processing, LNG, refineries, offshore platforms. AU well covered (compiled from state/GA sources); **PNG sparse**. Source data to Feb 2025.

### 2.5 OpenStreetMap (PNG facility gap-filler; license-separated)

- **ODbL 1.0** — share-alike on derivative databases: CC BY sources can be mixed *into* ODbL, not vice versa → keep OSM as a **separate overlay layer/file**, never merged into the CC BY compilation.
- Verified today via Overpass (`https://overpass-api.de/api/interpreter`): PNG LNG **plant** at Caution Bay is mapped (industrial=natural_gas), Kutubu/Hegigio segments exist, but **no contiguous Hides→POM pipeline way and no Papua LNG features** → GEM covers routes; OSM contributes plant footprints/local detail. AU coverage decent but uneven. Keep Overpass queries simple (timeouts on complex PNG queries).

### 2.6 Gas power stations (coordinates)

- **Primary (fully open)**: GEM GOGPT (CC BY 4.0, unit-level points).
- **Enrichment**: **Open Electricity** facility GeoJSON — verified, no auth: `https://data.openelectricity.org.au/v3/geo/au_facilities.json` (~300+ stations: fuel_tech, capacity, status, DUIDs). ⚠️NC — **CC BY-NC 4.0**. Time-series API needs free key (`platform.openelectricity.org.au`).
- **AEMO NEM Registration & Exemption List**: CC BY 4.0, DUID/capacity, **no lat/lon** (join table only). **WRI GPPD**: unmaintained since v1.3.0 (2021) — fallback only.

### 2.7 PNG-specific & landfills

- PNG: GEM is the authoritative open source (routes + terminals + fields). MRA mining cadastre is view-only without registration (minerals, not petroleum); no open DPE petroleum geodata found. Some layers on PNG Environment Data Portal (`https://png-data.sprep.org`).
- Landfills (optional later layer): no national open geodataset; candidates = NGER waste facilities (no coords), state EPA registers, OSM `landuse=landfill`, IMEO/Carbon Mapper waste-sector plumes as discovery.

---

## 3. Deforestation & carbon (Phase 4)

_Platform note: Global Forest Watch is rebranding to **Global Nature Watch** (globalnaturewatch.org); the API/tile hosts below remain live — expect URL churn, keep them in `data-catalog/` only._

### 3.1 Tree cover loss — Hansen GFC v1.13 (2001–2025)

- **What**: 30 m annual tree-cover loss, latest release covers **through 2025** (`GFC-2025-v1.13`). GFW Data API dataset `umd_tree_cover_loss` v1.13.
- **Access**: bulk GeoTIFF `https://storage.googleapis.com/earthenginepartners-hansen/GFC-2025-v1.13/download.html`; GEE `UMD/hansen/global_forest_change_2025_v1_13`; **MapLibre-ready raster tiles** `https://tiles.globalforestwatch.org/umd_tree_cover_loss/latest/dynamic/{z}/{x}/{y}.png?start_year=2001&end_year=2025&tree_cover_density_threshold=30` (no key required per the tile-cache OpenAPI; verify rate limits).
- **License**: CC BY 4.0. **AU+PNG**: both fully covered.
- **Role**: the historical loss layer for both countries.

### 3.2 Near-real-time disturbance alerts

- **GFW integrated alerts** (`gfw_integrated_alerts`, 10 m output, updated near-daily): combines GLAD-L + GLAD-S2 + RADD + **DIST-ALERT (added Jan 2026) → now global, including Australia** (the old "no extratropical coverage" is obsolete). Tiles `https://tiles.globalforestwatch.org/gfw_integrated_alerts/{version}/dynamic/{z}/{x}/{y}.png`; a newer `gfw_integrated_dist_alerts` route also exists — test both at build time.
- **RADD** (Sentinel-1 radar, 10 m, ~6–12 day): humid tropics incl. **PNG**; not Australia. GEE `projects/radar-wur/raddalert/v1`. CC BY 4.0.
- **DIST-ALERT** (UMD/NASA OPERA, 30 m, global, 2–4 day cadence): Australia's NRT option; standalone tiles `/umd_glad_dist_alerts/...`.
- **Role**: "recent clearing" toggle: integrated alerts everywhere, with per-country provenance note.

### 3.3 Australia official/validated products

- **DEA Land Cover 2.0** (`ga_ls_landcover_class_cyear_3`): annual 1988–2025, 30 m, CC BY 4.0. WMS/WMTS `https://ows.dea.ga.gov.au/` (drops straight into MapLibre); STAC `https://explorer.dea.ga.gov.au/stac/`; bulk S3 `https://data.dea.ga.gov.au/`. No dedicated woody-change product — use Level-4 woody classes.
- **QLD SLATS Sentinel-2 series** (2018→present, annual woody extent + attributed clearing/regrowth): `https://www.data.qld.gov.au/dataset/statewide-landcover-and-trees-study-queensland-sentinel-2-series`, GeoTIFF/FGDB, CC BY. Gold standard where it exists (QLD is also where most AU clearing happens).
- **National Forest & Sparse Woody Vegetation (NGGI) v8.0**: 1988–2023, 25 m, 3-class, `https://data.gov.au/data/dataset/national-forest-and-sparse-woody-vegetation-data-version-8-0-2023-release` (v8.0 ships southern tiles only; northern tiles transitioning to Sentinel-2, separate release; license presumed CC BY — confirm on page).
- **Role**: AU validated-change layers + methodology cross-check against GFW numbers (they will differ; explain why).

### 3.4 Carbon estimation

- **GFW carbon flux (Harris et al.), updated 2001–2025** (model v1.4.3): `gfw_forest_carbon_gross_emissions` / `gross_removals` / `net_flux`, 30 m, Mg CO2e/ha, pixel-aligned to Hansen loss. Tiles for display + **zonal statistics over custom polygons via `POST https://data-api.globalforestwatch.org/dataset/{dataset}/latest/query/json`** (SQL + GeoJSON geometry + `x-api-key`).
- **ESA CCI AGB v7.0** (May 2026): 100 m above-ground biomass, epochs 2005–2012 & 2015–2024, CEDA `https://catalogue.ceda.ac.uk/uuid/6429d1aafe1e43b9b414e4a5a7f8b903/`.
- **NASA GEDI L4B v2.1**: 1 km gridded AGBD 2019–2023 (context only — too coarse per-polygon).
- **Role / approach**: "carbon lost from this polygon" = one authenticated GFW API call (gross emissions sum) — no raster processing in our stack. CCI AGB for stock context maps. GFW API key: free — account via `https://www.globalforestwatch.org/my-gfw/`, then `POST /auth/token` → `POST /auth/apikey` (1-year expiry, `x-api-key` header).

### 3.5 PNG forestry context

- **Oil palm concessions** (GFW Open Data, includes PNG, ~2014 vintage — label as historical): `https://data.globalforestwatch.org/datasets/oil-palm-concessions`.
- **Logging concessions/FMAs**: not in GFW downloads; served by **PNG-NFMS geoportal** (`https://png-nfms.org/portal/`, PNGFA/CCDA/FAO, view without login; bulk download unconfirmed) and PNG Forest Observatory (`https://forest.pngsdf.com/`, 2002–2014 logging/clearing). REDD+ context: `https://pngreddplus.org/nfms/`.
- **Role**: context overlays; flag vintage prominently.

### 3.6 Australia ACCU (carbon credit) projects — confirmed spatial

- **What**: "Area-based ACCU Scheme projects" — official CER-published boundaries (savanna burning, vegetation/sequestration; ~10 projects suppressed).
- **Access**: `https://data.gov.au/data/dataset/accu_project_mapping` — **shapefile, GeoJSON, WFS, WMS**; `PROJ_ID` joins to the CER project register (updated monthly).
- **License**: CC BY 3.0 AU.
- **Role**: Phase 4.4 overlay — confirmed feasible.
