# Whose data is this, and what do we actually contribute?

_Written 2026-08-15 in response to a direct question: if we're building a Carbon-Mapper-like site ourselves, how are we using their data? The honest answer matters both legally and strategically._

## 1. Right now, we detect nothing

Of the 797 plumes on the map, we found **zero**. Every one was detected and quantified by someone else:

| Provider | Plumes | What they did | What it costs us |
|---|---|---|---|
| UNEP IMEO / MARS | 491 | detection + quantification | **CC BY-NC-SA 4.0** |
| Carbon Mapper | 367 | Tanager-1 + aircraft, measured rates | non-commercial, attribution |
| SRON | 66 | TROPOMI ML detection | CC BY 4.0 |
| NASA EMIT | 11 | imaging spectrometer | open, cite DOI |

_(935 raw, 797 after removing 138 events reported by more than one provider.)_

What this project genuinely adds today is real but thin:

- **Cross-provider fusion.** 138 duplicate events matched across providers. None of them do this — each publishes its own catalogue in isolation.
- **Infrastructure association.** 585 of 797 plumes tied to a specific mapped mine or gas plant, using a pipeline/facility distinction none of the providers offer.
- **Regional depth.** AU+PNG cropping, a 20-month TROPOMI baseline, and 2,036 infrastructure features that no global portal carries at this fidelity.

That is a legitimate contribution — an aggregator and cross-referencer. But it is **not** what Carbon Mapper does. They fly instruments and run retrievals. We re-serve their conclusions with context attached.

## 2. The licence bill is the real constraint

Two of the four sources are non-commercial, and one is share-alike:

- **UNEP IMEO is CC BY-NC-SA 4.0.** Share-Alike is the sharp edge: a derivative *database* must be released under the same licence. Our merged `plumes.geojson` blends IMEO records with the rest, so the combined product is realistically bound by BY-NC-SA. Anyone may insist we license it that way.
- **Carbon Mapper is non-commercial with attribution**, on terms they set and can revise.

> **Owner decision, 2026-08-15:** this project is non-commercial today, and if that ever changes the licences get renegotiated or paid for then. An undefined future is not allowed to constrain present work. The consequences below are recorded so the decision stays informed — they are **not** a reason to avoid a source now.

**Consequences, stated plainly:**

1. This project **cannot become commercial** while the plume layer includes IMEO or Carbon Mapper data. Not "would need a conversation" — the licences forbid it. Both offer paid or negotiated commercial terms, so this is a bill, not a wall.
2. The merged plume layer **cannot be released more openly** than BY-NC-SA.
3. If either provider changes terms, restricts access, or shuts down, our headline layer disappears. We have no independent capability.
4. Attribution is a live obligation, not a footnote. It was missing from the UI until this was written; that is now fixed.

The rest of the stack is clean: Copernicus permits commercial use, as do Geoscience Australia, GEM and NASA. **The plume layer alone is what encumbers the project.**

## 3. What "building it off the core data" would actually mean

Detecting plumes ourselves means running a retrieval over raw imagery instead of consuming someone's catalogue. The route is well established and — importantly — **we already have every credential and access path it needs.**

**Verified 2026-08-15:** Sentinel-2 L2A sits in the same CDSE `eodata` bucket, reachable with the S3 keys already driving the TROPOMI pipeline. Scenes available in the last 60 days:

| Site | Scenes |
|---|---|
| Grosvenor mine, Bowen Basin | 16 |
| Appin colliery, Sydney Basin | 32 |
| **Hides gas field, PNG** | **17** |
| **PNG LNG plant, Caution Bay** | **14** |

The method is published and reproducible: methane absorbs in SWIR, so comparing Sentinel-2 bands B12 and B11 against a clear reference pass (the multi-band multi-pass approach) isolates a plume. UNEP's own MARS-S2L pipeline works exactly this way — the 491 IMEO plumes we currently borrow are substantially Sentinel-2 detections. **We can run the same instrument they do.**

Sensitivity is the honest limit. Sentinel-2 detects roughly 1–3 t/hr and up. Our observed plume rates have a median around 2.1 t/hr and a maximum near 81 t/hr, so this reaches the larger half of events — the half that matters most for emissions.

### Why this is worth doing, in one number

**All four providers report zero plumes over Papua New Guinea.** TROPOMI cannot see PNG either (0.2% usable coverage). Yet Sentinel-2 imaged the Hides field 17 times in 60 days. PNG is not unobservable — it is simply unexamined, because global providers prioritise elsewhere.

Running our own detection over the PNG LNG chain would produce **genuinely new data that does not currently exist anywhere**, under Copernicus terms we own outright. That is the difference between an aggregator and an observatory.

## 4. Recommendation

Keep both, in this order:

1. **Keep the aggregated layer** — it is 797 real plumes today, it is free, and building our own will not match that coverage for months. Attribute it properly and take the non-commercial terms; they are revisitable if the project's status ever changes.
2. **Build our own Sentinel-2 detection as Phase 2b**, starting with the highest-value gap rather than trying to replicate anyone globally:
   - **PNG LNG chain** (Hides, Kutubu, Caution Bay) — nobody is looking, and we have the imagery
   - **Bowen Basin and Hunter Valley underground mines** — where the 585 associated plumes already cluster, giving us ready-made validation targets
3. **Validate against the borrowed data.** We have 797 detections with locations, times and rates. Any detector we build can be scored against them directly — an unusually good position to start from.
4. **Track provenance per plume** so the map can always show what is ours versus borrowed, and so an independent, commercially-usable subset can be split out later if the project's status changes.

The end state worth aiming at: **our own detections over the region nobody covers, with the global catalogues layered in as corroboration** — rather than a regional mirror of other people's work.
