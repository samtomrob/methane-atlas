# Papua New Guinea has been looked at 44 times. Nothing large is leaking.

_Analysis run 2026-08-15 against the public NASA EMIT archive. As far as we can establish, nobody had processed this data for PNG before. Reproduce with `matlas emit-scan`._

## Why this exists

No public provider publishes a single methane plume over Papua New Guinea — zero from Carbon Mapper, UNEP IMEO, SRON and EMIT alike, against 743 over Australia in the same layer. TROPOMI cannot help: only 0.2% of PNG cells yield usable retrievals, against 58.2% for mainland Australia.

The tempting reading is that PNG is clean. The other possibility is that nobody has looked. Those demand opposite responses, and until now the data to tell them apart had not been examined.

It turns out the data already existed. EMIT has been imaging the PNG LNG chain for two years, and every granule ships a sensitivity raster alongside the enhancement one — precisely what is needed to separate "nothing emitting" from "could not have seen it".

## What was done

For each facility, every archived EMIT methane granule containing it was opened, and a 24×24 pixel box (~1.4 km) sampled at the site. An overpass counts as **usable** only when at least 25% of that box carries valid retrievals — cloud, swath edges and water all void it. Each usable overpass is then scored as excess over the surrounding scene's own median, in units of that scene's robust noise.

Absolute values are useless here: the retrieval's scene noise runs to a median of 476 ppm·m. Only excess over local background means anything.

## Result

| Facility | Granules containing it | Usable overpasses | Observability | Strongest excess |
|---|---|---|---|---|
| Angore wellpads | 24 | 10 | 42% | +0.42σ |
| Hides Gas Conditioning Plant | 20 | 8 | 40% | +0.22σ |
| Kutubu / Moro operations | 18 | 8 | 44% | +0.42σ |
| Juha area petroleum well | 19 | 7 | 37% | +0.12σ |
| Hides gas-to-electricity | 20 | 6 | 30% | +0.25σ |
| PNG LNG plant, Caution Bay | 17 | 5 | 29% | +0.10σ |
| **Total** | **118** | **44** | **37%** | **+0.42σ** |

**Across all 44 usable overpasses, spanning May 2024 to May 2026, not one site exceeded +1σ above its scene background.** None came close: the median excess is +0.08σ and the maximum anywhere is +0.42σ. Zero overpasses reach the +3σ that would constitute even a weak detection.

This agrees with the second, independent reading: EMIT's own plume-complex product — produced by NASA's team with a manual review step — flags **no plumes** anywhere in PNG either. Two different analyses of the same instrument concur.

## What this does and does not mean

**It does mean** the PNG LNG chain has genuinely been observed, repeatedly and recently, and no large methane plume was present at any of those 44 moments. "Nobody has looked" is no longer true. Coverage is also improving sharply: 2 usable overpasses in 2024, 9 in 2025, **33 in 2026**.

**It does not mean PNG emits nothing.** The floor matters:

- This box-median test would only have flagged a sustained excess of roughly **1,400 ppm·m across the full 1.4 km box** — a very large release. EMIT's own plume product detects smaller, spatially coherent features, and found none, which tightens the bound but does not remove it.
- **63% of overpasses are lost**, almost all to cloud. Any intermittent release stands a roughly two-in-three chance of being missed on any given pass.
- Chronic low-rate venting — the kind that dominates real inventories — sits far below this threshold and would be invisible.
- Six facilities are not the whole chain. The ~700 km of pipeline between them is unsampled by this analysis.

So the defensible statement is: **no large methane plume was detectable at PNG's major gas facilities during 44 clear EMIT overpasses across two years.** That is a real, quantified null — very different from the assumed-clean reading, and different again from "unexamined".

## The correction this replaces

An earlier plan was to nominate PNG for EMIT targeting. That was wrong twice over. **EMIT cannot be tasked**: it is body-mounted on the ISS with an 11° field of view and no gimbal, and coverage follows the orbit and an internal mask. No nomination route exists. And it was unnecessary — the observations already existed and simply had not been read.

## Remaining routes for new PNG data

Verified:

- **GHGSat via ESA Third Party Missions** grants genuine tasking on a purpose-built methane instrument, free, on an accepted project proposal — but is restricted to residents of ESA member, cooperating and associate states plus Canada, which appears to exclude Australia.
- **EnMAP** now requires an institutional email address for both tasking and archive access.
- **PRISMA** accepts registrations from people with no affiliation (the form explicitly says to enter "NONE"), but its licence reserves tasking rights by user category and may assign unaffiliated users to a tier with none. One question to the ASI help desk would settle it.
- **Sentinel-2 and Landsat** are systematic and cannot be tasked in any useful sense.

Not established, and deliberately left blank rather than guessed: whether Carbon Mapper or UNEP IMEO accept external site nominations, and what commercial tasking would cost.

## Reproducing

```bash
matlas emit-scan                 # all PNG sites, full archive
matlas emit-scan --limit 5       # quick pass
```

Requires only a free NASA Earthdata token. Output: `web/public/data/png_emit_scan.json`, carrying every overpass with its validity, excess and sensitivity.
