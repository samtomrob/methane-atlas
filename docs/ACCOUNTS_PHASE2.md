# Phase 2 accounts — step by step

Two free accounts unlock the plume detections. Both take about five minutes. Neither needs a payment method.

Everything you copy goes into **`.env`** in the project root (already gitignored — it never gets committed or pushed). You don't need to paste any value into chat.

Check your work at any point with:

```bash
cd "C:\Users\samro\Claude Projects\methane-atlas\pipeline" && uv run matlas auth-check
```

It tests each credential against the live service and prints only pass/fail — never the values.

---

## 1. NASA Earthdata → `EARTHDATA_TOKEN`

Unlocks **EMIT** methane plumes: 60 m resolution, individual plumes with emission rates and uncertainty, covering both Australia and PNG.

1. Go to **<https://urs.earthdata.nasa.gov/users/new>**
2. Fill in the registration form. The fields that matter:
   - **Username** and **password** — you'll need these again in step 5
   - **Email** — must be real, you'll verify it
   - **Country**: Australia
   - **Study Area / Primary Use**: choose *Earth Science* (any option is fine; this is just for NASA's statistics)
   - **User Type**: *Public / Other* is fine
3. Accept the terms and submit, then **check your email and click the verification link**. The account will not work until you do.
4. Log in at **<https://urs.earthdata.nasa.gov/>**
5. Click your **username in the top-right → Generate Token** (or go straight to <https://urs.earthdata.nasa.gov/generate_token>)
   - Click **Generate Token**
   - You may be asked for your password again — that's expected
6. Copy the long string it shows you. It looks like `eyJ0eXAiOiJKV1Qi...` and is several hundred characters.
7. Paste it into `.env`:

   ```
   EARTHDATA_TOKEN=eyJ0eXAiOiJKV1Qi...
   ```

**Note:** these tokens **expire after 60 days**. When `auth-check` eventually reports the Earthdata token as rejected, just repeat steps 5–7. Nothing else breaks.

---

## 2. Carbon Mapper → `CARBON_MAPPER_TOKEN`

Unlocks the richest plume catalogue for Australia — roughly **271 Australian plumes since 2023, about 51 of them in the Bowen Basin**, each with a measured emission rate in kg/hr. This is the layer that can actually point at a facility, which the TROPOMI layer cannot.

1. Go to **<https://data.carbonmapper.org/>**
2. Click **Sign up** (top right). Register with your email and set a password.
   - When asked about intended use, *research / public interest / non-commercial* is the accurate answer for this project — it's the basis on which the data is free.
3. Verify your email if prompted, then sign in.
4. The pipeline exchanges your login for a short-lived API token automatically, so you have a choice of what to put in `.env`:

   **Option A (simplest)** — store your login and let the pipeline fetch tokens itself:

   ```
   CARBON_MAPPER_EMAIL=you@example.com
   CARBON_MAPPER_PASSWORD=your-password
   ```

   **Option B** — if the portal offers a personal API key under account settings, use that instead and leave the two above blank:

   ```
   CARBON_MAPPER_TOKEN=...
   ```

   Option A is what their API documents (`/api/v1/token/pair` exchanges email + password for a token). If you'd rather not store a password, use Option B and tell me — I'll wire whichever the portal gives you.

---

## Already working — nothing to do

| Source | Status |
|---|---|
| Copernicus / Sentinel-5P | ✅ configured, 20-month baseline built |
| UNEP IMEO (MARS detections) | no account needed |
| SRON weekly TROPOMI plumes | no account needed |
| Infrastructure (Geoscience Australia, GEM, Open Electricity) | no account needed |
| Earth Engine | not used — deliberately dropped |

## Not needed yet

| Source | When |
|---|---|
| Global Forest Watch API key | Phase 4 (deforestation & carbon) |
| Cloudflare R2 | only if the raster archive outgrows the repo — currently 21 MB, fine as is |

---

## After you've added them

```bash
cd "C:\Users\samro\Claude Projects\methane-atlas\pipeline"
uv run matlas auth-check     # confirm both are green
uv run matlas plumes         # fetch every provider
```

`matlas plumes` writes `web/public/data/plumes.geojson` and refreshes the map layer. Providers you haven't configured are skipped with a note rather than failing the run, so it's safe to run with only some credentials in place.
