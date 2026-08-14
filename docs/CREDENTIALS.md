# Credentials setup

_Verified 2026-08-14. Every account here is free. **You fill in `.env` at the repo root** — that file is gitignored and never committed. Nothing needs to be pasted into a chat: the pipeline reads `.env` directly._

Verify at any point with:

```bash
cd pipeline && uv run matlas auth-check
```

It tests each credential against the real service and prints `ok` / `FAIL` / `skip` per line — never the values themselves, so the output is safe to share.

| Service | Needed for | Phase | Billing? |
|---|---|---|---|
| Copernicus Data Space | Sentinel-5P TROPOMI methane | 1 | no |
| Google Earth Engine | 2019→now methane backfill | 1 | no |
| NASA Earthdata | EMIT plume detections | 2 | no |
| Carbon Mapper | Tanager/aircraft plumes | 2 | no |
| Global Forest Watch | deforestation + carbon | 4 | no |
| Cloudflare R2 | hosting raster tiles | 1 (late) | card on file |

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

## Earth Engine

An "Earth Engine API key" from the Cloud console is **not** sufficient for automated use — an API key carries no identity, and Earth Engine authorizes a *principal* with IAM roles. Non-interactive use needs a **service account with a JSON key**. No billing required.

1. **Register a project** — <https://console.cloud.google.com/earth-engine>
2. **Enable the API** — <https://console.cloud.google.com/apis/library/earthengine.googleapis.com> → **ENABLE**
3. **Register as noncommercial** — `https://code.earthengine.google.com/register?project=YOUR-PROJECT-ID`
4. **Complete the eligibility questionnaire** — <https://console.cloud.google.com/earth-engine/configuration> — mandatory for all noncommercial projects since September 2025; Earth Engine calls fail without it
5. **Create a service account** — <https://console.cloud.google.com/iam-admin/serviceaccounts/create>, then grant it **both** roles:
   - **Earth Engine Resource Writer** (`roles/earthengine.writer`) — Viewer is not enough; Writer is what permits computations and exports
   - **Service Usage Consumer** (`roles/serviceusage.serviceUsageConsumer`) — without this, `ee.Initialize(project=…)` fails outright
6. **Download the key** — open the service account → **Keys** → **Add key** → **Create new key** → **JSON**. Save it as `secrets/gee-service-account.json` (that folder is gitignored).

Then set `GEE_PROJECT_ID` to your project ID. Earth Engine registration is **per Cloud project**, not per service account — there's no separate per-account signup any more; every service account in a registered project with the right roles inherits access.

### Compute quota — the real constraint

Since April 2026, noncommercial projects carry a monthly compute budget:

| Tier | Monthly quota | Requirement |
|---|---|---|
| **Community** (default) | **150 EECU-hours** | verified noncommercial project |
| Contributor | 1,000 EECU-hours | needs an active billing account |
| Partner | 100,000 EECU-hours | separate application |

Check or change tier at <https://console.cloud.google.com/earth-engine/configuration/manage-tier>. Also roughly **2 concurrent batch tasks** and 250 GB of asset storage.

150 EECU-hours is the ceiling that shapes the backfill design: the job is built to be **resumable and chunked by year**, so if a month's budget runs out it picks up where it stopped rather than restarting.

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
