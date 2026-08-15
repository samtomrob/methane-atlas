# Building our own Sentinel-2 detector: what happened

_Built and validated 2026-08-15. **Verdict: this implementation does not work. Nothing from it is published.** The harness, the controls and the reasons are kept because they are what make the conclusion trustworthy, and because they define what a working version would have to beat._

## The verdict first

Detection candidates per scene, all sites through identical code:

| | Site | SWIR | noise σ | per scene |
|---|---|---|---|---|
| **target** | Grosvenor mine, Bowen Basin | 0.280 | 0.017 | **1.12** |
| **target** | Mandalong mine, NSW | 0.150 | 0.020 | 0.88 |
| **target** | Ashton mine, Hunter Valley | 0.284 | 0.033 | 0.75 |
| **target** | Hides Gas Plant, PNG | 0.166 | 0.022 | 0.75 |
| **target** | Appin colliery, Sydney Basin | 0.174 | 0.014 | 0.12 |
| control | NSW bushland | 0.184 | 0.019 | **0.75** |
| control | PNG forest ridge | 0.139 | 0.016 | 0.38 |
| control | QLD rangeland | 0.310 | 0.012 | 0.12 |

**Target-to-control ratio: 1.74× overall, 1.64× in Australia.** A working detector should be far above that. Worse, the ordering is wrong in the places it matters:

- **Ashton, an active coal mine with 37 independently measured plumes, scores exactly the same as NSW bushland (0.75 vs 0.75).**
- **Appin, with 35 known plumes, scores the same as empty QLD rangeland (0.12 vs 0.12).**

### The decisive test

We hold 797 plumes that other instruments independently measured. Across the 17 dates analysed, **20 of those plumes fell on a day we examined, near a site we examined**. Our detector reproduced **one**, at 2.9 km separation.

One match in twenty, from 29 candidates, is what chance looks like. If this were detecting methane, the overlap would be substantially higher.

**Conclusion: the candidates are surface-change noise, not plumes.** Nothing is published to the map or the site, and `s2_detections.geojson` is deliberately empty.

## What the one apparent success actually shows

Grosvenor (1.12/scene) against QLD rangeland (0.12/scene) is a clean 9× contrast — and unusually well controlled, because **both sit on the same tile T55KER and were analysed from the identical eight scenes**, so illumination, atmosphere and date are perfectly matched.

That is the single genuinely interesting result. But Grosvenor is one of Australia's largest open-cut-plus-underground complexes: pits, spoil heaps, haul roads and water ponds all move between passes. Intense surface change is the simpler explanation, and the independent-agreement test gives no support for the alternative. It is a lead, not a finding.

## Three bugs found along the way

Each was caught by measurement, and each mattered.

**1. The first run's 95 detections were all false.** At Hides, 25 of 44 distinct locations recurred on up to four separate dates — the two strongest at an identical position two days apart. A plume disperses in minutes and cannot be imaged twice. The survivors then showed **B11 brightening 5–9% while B12 darkened**, which is a surface moisture change (B11 tracks water content), not gas. Methane leaves B11 essentially untouched. The physical gate now requires both: B12 clearly down, B11 near zero.

**2. Two filters fought each other.** Tightening the physical gate *raised* the Hides count from 5 to 15. The scene-contamination check counted clusters after the gate, so tightening it pushed contaminated scenes back under the limit and readmitted them. Contamination is a property of a scene, not of how many clusters survive a later test, so it is now judged on raw counts.

**3. A tile-edge bug was inflating the apparent result.** The NSW control sits exactly on the UTM zone 55/56 boundary at 150°E. Its window overflowed the tile edge and every scene was silently rejected — it looked like missing imagery. With the control reading zero, the measured false-positive floor was too low and the target-to-control ratio looked like **7.5×**. Fixing it (choose the tile by how much of the site it covers; clip the window instead of rejecting it) brought the real floor into view and the ratio fell to **1.64×**.

That third one is the reason this document exists rather than an announcement. A bug in the *control* made the *detector* look good.

## Why it fails, physically

Sensitivity to methane scales with SWIR surface brightness, and the sites that matter are dark:

- Bowen Basin around 0.28 reflectance — workable
- Hunter Valley and PNG around 0.15–0.17 — poor
- Detection needs a coherent multi-percent dip in B12/B11, while measured per-scene noise is 1.4–3.3%

So the achievable floor here is far above Sentinel-2's nominal 1–3 t/hr, and well above most real events. Meanwhile the surfaces themselves — active mines, tropical vegetation, steep terrain — change between passes in ways that mimic absorption.

## What would be needed

Not incremental tuning. The gap is methodological:

1. **Constrain the reference to a single relative orbit** so viewing geometry is constant. Ours mixed orbits, which injects BRDF differences directly into the ratio.
2. **Correct for surface reflectance and terrain** rather than assuming the median of other passes cancels them.
3. **Go where the physics works** — bright, arid, stable ground. That is not vegetated coal country or rainforest.
4. **Prefer a spectrometer.** EMIT resolves 285 bands and supports a matched-filter retrieval; Sentinel-2 offers two broad SWIR bands. The instrument, not the algorithm, is the main limit.

UNEP's MARS-S2L does this operationally with a team behind it. Matching it in a single build was not a realistic target, and the honest read is that **our value is in fusion, infrastructure association and regional depth — not in re-deriving detections that better-resourced groups already publish**.

## For PNG specifically

The gap is real: no provider publishes a single plume there, and TROPOMI cannot see it. But Sentinel-2 band-ratio detection is not the way in — PNG has the worst combination of dark vegetation, persistent cloud and steep terrain in the region.

More promising routes, in order:

1. **Nominate PNG facilities for EMIT targeting.** EMIT observes by request; getting the PNG LNG chain onto the target list costs an email, not a retrieval pipeline.
2. **Track GOSAT-GW**, launched June 2025 with 1–3 km methane mapping, whose L2 products are pending release.
3. **Keep the infrastructure layer authoritative for PNG** — it is already the best public map of that chain, and it is ours.

## Running it

The harness is retained and worth keeping, precisely because it can falsify itself:

```bash
matlas s2-detect --region au            # or png, or control
matlas s2-detect --site grosvenor --max-scenes 8
```

Any future detector should be scored the same way: against infrastructure-free controls on matched scenes, and against the 797 independently measured plumes. A ratio near 1.7× and one coincidence in twenty is the bar to beat.
