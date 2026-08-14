# Methane Atlas (working name)

An open web map of methane emissions and their likely sources across **Australia and Papua New Guinea**, built entirely on free satellite data and open infrastructure datasets — with a later module for deforestation and carbon estimation.

**Status: Phase 0–1 in build** (started 2026-08-14; decisions D-1..D-7 resolved — see BUILD_PLAN §5). Layers that need owner credentials (TROPOMI, EMIT, Carbon Mapper, GEM spreadsheets) activate as tokens arrive.

| Doc | What it is |
|---|---|
| [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) | Vision, phases, agent task breakdown, acceptance criteria, open decisions |
| [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) | Verified catalog of every data source (access, license, cadence) |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design: pipeline → static data store → web app |

In one line: **TROPOMI daily methane as the always-on background, point-source plumes from EMIT / Carbon Mapper / IMEO / SRON on top, and a rich, filterable overlay of every pipeline, LNG plant, gas field, gas generator and coal mine — with reported-vs-observed methane comparison for Australian facilities.**

This is a noncommercial, public-good project. Several layers (Carbon Mapper, UNEP IMEO, Open Electricity) are used under noncommercial licenses — see `data-catalog/` for per-source terms and attribution.

## Quickstart

```bash
# data pipeline (Python 3.12+, uv)
cd pipeline
uv sync
uv run matlas probe          # check every data-catalog URL is alive
uv run matlas infra          # compile v0 infrastructure layers -> web/public/data/

# web app (Node 22+)
cd ../web
npm install
npm run dev                  # http://localhost:3000
```

Secrets: copy `.env.example` to `.env` and fill tokens as accounts are created; CI reads the same names from GitHub Actions secrets.

## Repository layout

```
pipeline/       Python package `matlas` — fetch, composite, tile, publish stages
web/            Next.js + MapLibre app (static export)
data-catalog/   one YAML per data source: URLs, license, cadence, probe rules
docs/           BUILD_PLAN, DATA_SOURCES, ARCHITECTURE
```
