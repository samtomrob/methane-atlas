# Credentials setup

_Verified 2026-08-14. Every account here is free. **You fill in `.env` at the repo root** — that file is gitignored and never committed. Nothing needs to be pasted into a chat: the pipeline reads `.env` directly._

Verify at any point with:

```bash
cd pipeline && uv run matlas auth-check
```

It tests each credential against the real service and prints `ok` / `FAIL` / `skip` per line — never the values themselves, so the output is safe to share.

| Service | Needed for | Phase | Billing? |
|---|---|---|---|
| **Copernicus Data Space** | **all Sentinel-5P methane, backfill + ongoing** | **1 — required** | no |
| NASA Earthdata | EMIT plume detections | 2 | no |
| Carbon Mapper | Tanager/aircraft plumes | 2 | no |
| Global Forest Watch | deforestation + carbon | 4 | no |
| Cloudflare R2 | hosting raster tiles | 1 (late) | card on file |
| ~~Google Earth Engine~~ | dropped — see below | — | — |

---

## CDSE

Two separate credentials, because CDSE's own documentation recommends **searching over the API and downloading over S3** — the catalogue tells us which granules exist, S3 moves the bytes efficiently.

### OAuth client → `CDSE_CLIENT_ID`, `CDSE_CLIENT_SECRET`

1. Open <https://shapps.dataspace.copernicus.eu/dashboard>
2. **User Settings** tab → **OAuth clients** → **Create**
3. Name it (e.g. `methane-atlas`), pick an expiry or "Never expire"
4. Copy both values — **the secret is shown only once**

Token endpoint used by the pipeline: `https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token` (grant type `client_credentials`).

### S3 keys → `CDSE_S3_ACCESS_KEY`, `CDSE_S3_SECRET_KEY`

1. Open <https://eodata-s3keysmanager.dataspace.copernicus.eu/>
2. **Add Credentials** → set an expiry → confirm
3. Copy both — **the secret key is shown only once** (gone after you click Close)

Endpoint `https://eodata.dataspace.copernicus.eu`, region `default`, bucket `eodata`.

### Password fallback → `CDSE_USERNAME`, `CDSE_PASSWORD` (optional)

CDSE's docs demonstrate granule downloads using the account-password grant, and it is **not confirmed** that an OAuth-client token is accepted by the download endpoint. `matlas auth-check` settles this empirically: it mints a token and then tries to authorize a real granule download. If the OAuth route fails, fill these two as a fallback — but **skip it if you have 2FA enabled**, since that grant then requires a live one-time code and can't be scripted. Prefer the OAuth client for CI regardless: a rotatable secret is a much smaller blast radius than an account password.

### Limits the backfill must respect

| Limit | Value |
|---|---|
| Concurrent S3 connections | **4** — caps download parallelism |
| Bandwidth per connection | 20 MB/s |
| S3 requests per minute | 2,000 (each file counts separately) |
| Transfer volume | 12 TB per rolling 30 days; over-quota throttles rather than blocks |
| Access token lifetime | 10 minutes, refreshable within 60 — long jobs must refresh mid-run |

Cross-host redirects on downloads drop the auth header, so the pipeline follows redirects manually and re-attaches the token each hop.

---

## Earth Engine — dropped, nothing to configure

**You do not need an Earth Engine credential.** Leave `GEE_PROJECT_ID` and `GEE_SERVICE_ACCOUNT_JSON_PATH` blank.

Earth Engine was originally in the plan purely as a shortcut for the 2019→now backfill. Measuring the alternative removed the reason: the full backfill from CDSE is **571 GB** (25 granules/week intersecting the region × ~60 MB, over 392 weeks) — under 5% of CDSE's 12 TB rolling-30-day allowance and roughly one overnight run. Earth Engine would have cost a second account, an OAuth service-account flow, a monthly compute quota, and a noncommercial-only platform licence, to save a few hours of one-time download.

Dropping it also improves the project's position in two ways: CDSE's Copernicus terms permit commercial use where Earth Engine's free tier does not, and working from raw L2 measurements rather than Google's pre-binned L3 grid means we set the quality threshold and gridding ourselves.

### On API keys, for the record

An Earth Engine API key cannot drive the API — not via the Python library and not via REST. Google's [auth documentation](https://developers.google.com/earth-engine/guides/auth) lists user credentials, service accounts, and OAuth flows; API keys are not among the supported methods, because Earth Engine authorizes an *identity* holding IAM roles rather than a bare key string. (Note that <https://developers.google.com/earth-engine/apidocs> is the reference for the `ee.*` functions — the operations catalog — not an authentication or REST endpoint guide.)

If you ever want the shortcut anyway, `matlas gee-login` does an interactive browser sign-in and needs only `GEE_PROJECT_ID` set to the Cloud project your access is attached to (allowances belong to the project, not the key or the person). A service-account JSON at `secrets/gee-service-account.json` also works, needing both `roles/earthengine.writer` and `roles/serviceusage.serviceUsageConsumer`. Neither is used by default.

### Why we avoid batch exports entirely

Batch `Export` tasks would force an awkward choice — `toCloudStorage` requires enabling billing, and `toDrive` under a service account writes into the *service account's* Drive namespace where you can't see the files unless you explicitly share a folder with its email address.

Our composites are small (the whole Australia+PNG region at 0.05° is about 1040×920 pixels ≈ 4 MB per band), so the pipeline pulls them **synchronously via `getDownloadURL`** instead. No billing, no Drive sharing, no export-task queue. Batch export stays available as a fallback if a product ever exceeds the request-size limit.

---

## Earthdata

<https://urs.earthdata.nasa.gov/> → register → profile → **Generate Token** → set `EARTHDATA_TOKEN`. Tokens expire (typically 60 days), so `auth-check` failing here usually just means it needs regenerating.

## Carbon Mapper

Register at <https://data.carbonmapper.org/>, then the pipeline exchanges credentials for a JWT at `/api/v1/token/pair`. Set `CARBON_MAPPER_TOKEN`.

## GFW

<https://www.globalforestwatch.org/my-gfw/> → create account → request an API key (valid one year) → set `GFW_API_KEY`. Sent as the `x-api-key` header.

## R2

Cloudflare dashboard → **R2** → create bucket named `methane-atlas` → **Manage R2 API Tokens** → create token with object read/write. Set `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`. Only needed once TROPOMI rasters exist — the current vector layers are small enough to ship inside the repo.

---

## CI

The same names go into GitHub Actions secrets (Settings → Secrets and variables → Actions). The Earth Engine key is the exception: store the **file contents** as a secret named `GEE_SERVICE_ACCOUNT_JSON`, and the workflow writes it to disk before running. Local `.env` always loses to real environment variables, so CI can't be polluted by a stale local file.
