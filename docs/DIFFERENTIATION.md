# Why this site is worth existing

_Written 2026-08-15 in answer to a fair challenge: "how is my site not just a copy of someone else's?"_

## First, where the data actually comes from

Measured from our own layer, by instrument:

| Provider | Instrument | Plumes | Whose hardware |
|---|---|---|---|
| Carbon Mapper | `tan` — **Tanager-1** | 287 | **their own satellite** |
| Carbon Mapper | `emi` — EMIT | 48 | NASA |
| UNEP IMEO | EMIT | 171 | NASA |
| UNEP IMEO | Sentinel-5P/TROPOMI | 125 | ESA |
| UNEP IMEO | EnMAP | 115 | DLR |
| UNEP IMEO | PRISMA | 38 | ASI |
| NASA EMIT | EMIT | 9 | NASA |
| SRON | TROPOMI | 5 | ESA |

Two different businesses sit in that table.

**Carbon Mapper owns a spacecraft.** Tanager-1, launched August 2024, supplies 86% of their detections here. That is not replicable with public feeds — it is a satellite.

**UNEP IMEO does not.** Their 492 plumes come from EnMAP, PRISMA, Sentinel-5P and EMIT — the same public agency data we can access. They are doing what we attempted, with a funded team. Our own Sentinel-2 detector was built, validated against controls, and [failed honestly](S2_DETECTOR.md): a 1.74× target-to-control ratio and one coincidence in twenty. Reproducing a retrieval pipeline is a research programme, not a sprint.

So: **we did use the agency feeds** — 20 months of TROPOMI is ours end to end. But for facility-scale detection, the honest position is that we consume other people's catalogues.

## Which is fine, because detection was never the gap

Every provider publishes **plumes**: an event, at a place, on a day. Ask the question an operator, a regulator or a journalist actually asks — *"what is the story of this mine?"* — and none of them can answer, because a plume catalogue is indexed by event, not by asset.

That inversion is the product.

## What the site now does that nothing else does

### 1. Facility records, fused across every provider

184 mines and gas plants, each with a detection history assembled from all four catalogues. Grosvenor: **57 detections, July 2021 to July 2026, seen by two independent providers, peak 10,000 kg/hr**. No provider publishes that record, because each sees only its own instrument.

131 events are corroborated by more than one provider — cross-checks that exist nowhere else, since each catalogue is published in isolation.

### 2. "Has anyone actually looked?" — the distinction nobody else makes

Every catalogue's silence is ambiguous. A mine with no detections might be clean, or might simply never have been imaged. Those demand opposite responses and no public tool separates them.

We can, by counting EMIT overpasses per facility:

| Verdict | Facilities | Meaning |
|---|---|---|
| **Emitting** | 57 | at least one confirmed detection |
| **Watched, no plume** | 125 | imaged 10+ times, nothing found — meaningfully clean |
| **Too few looks** | 2 | absence proves nothing |

That middle row is the valuable one. **125 Australian facilities have been repeatedly examined and are clean** — a positive finding no plume catalogue can state, because a catalogue of detections cannot express a confident non-detection.

The same method applied to PNG produced [an original result](PNG_EMIT_FINDING.md): 44 usable overpasses of six gas facilities, no plume above background, from an archive nobody had processed.

### 3. Regional depth no global portal carries

2,036 infrastructure features for Australia and PNG — including the PNG LNG pipeline route, which OpenStreetMap does not have. 20 months of TROPOMI composites. Per-plume wind, so a detection can be checked against the direction it should have drifted.

### 4. Honesty as a feature

The [findings document](FINDINGS.md) records where our own analysis failed: TROPOMI hotspots that proved to be 100% coastal artifact, a Sentinel-2 detector that did not beat its control, surface bias exceeding the real basin signal. Portals that only publish successes cannot be checked. This one can.

## The one thing still missing, and it is the big one

**Reported versus observed.** Australia's Clean Energy Regulator publishes, per facility, a *"GHG Methane"* figure in tCO2-e under the Safeguard Mechanism. Joining that to satellite detections would let anyone ask: *does what this mine reports match what satellites see above it?*

Nobody does this. It is the single highest-value addition remaining, and the infrastructure join is already built and waiting for it.

**Blocked on one manual step.** The CER site renders its download links via JavaScript and the browser could not reach it without approval. Someone needs to download the "Baselines and emissions table 2024-25" file from
<https://cer.gov.au/markets/reports-and-data/safeguard-data/2024-25-baselines-and-emissions-data>
and drop it in `data/cer/`. The matcher can then run against the 184 facility records already in place.

## Where this leaves the comparison

| | Carbon Mapper | UNEP IMEO | This project |
|---|---|---|---|
| Owns an instrument | yes | no | no |
| Publishes plumes | yes | yes | re-published, attributed |
| Facility histories | no | no | **yes** |
| Cross-provider fusion | no | no | **yes, 131 corroborated** |
| Confident non-detection | no | no | **yes, 125 facilities** |
| Regional infrastructure depth | no | no | **yes** |
| Reported vs observed | no | no | **next** |

Not a copy. A different index over the same observations, plus the questions the observations were never organised to answer.
