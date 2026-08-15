# Reported versus observed

_Built 2026-08-15 from the Clean Energy Regulator's Safeguard Mechanism table for 2024-25. As far as we can establish, no public tool joins Australia's declared per-facility methane to satellite detections._

## What it does

Australia is unusual in publishing, for every Safeguard facility, a **"GHG Methane"** figure in tonnes CO₂-equivalent. That makes a comparison possible that exists nowhere else: *what a site declared, next to what satellites saw above it.*

- **228** CER facilities in the 2024-25 table, 227 reporting methane above zero
- **65** matched confidently to facilities we map
- **32** have both a reported figure and detections inside the same reporting year

Both numbers are shown in the facility popup, in the same units.

## The conversion

```
tonnes CH₄/year   = tCO₂-e ÷ 28          (AR5 GWP-100, as NGER uses)
average kg/hour   = tonnes × 1000 ÷ 8760
```

So Appin's 2,245,885 tCO₂-e becomes **9,156 kg/hr sustained across the year**.

## Three ways this went wrong before it was safe to publish

This is the most dangerous feature in the project — it puts a number next to a named company — so each failure is recorded rather than quietly patched.

### 1. It matched the wrong company

Our "Moranbah" gas plant matched CER's "Moranbah" on a **perfect name score**. That entry is **Dyno Nobel's ammonia plant**. Published, it would have read as *"declares 1 kg/hr, we observe 19,000"* — a 19,000× discrepancy alleged against a company whose methane reporting is entirely correct. The 19,000 kg/hr plume belongs to Moranbah North coal mine nearby.

Industry agreement is now a **hard gate**: a coal mine can only match a coal-mining entry, a gas plant only gas or electricity. Name similarity cannot override it. Match count fell from 72 to 65, and the bad ones are gone.

### 2. It compared different years

The CER figure covers FY2024-25. Our detections span 2021 to 2026. **Grosvenor** carried five years of plumes against a single year's report — and reports almost nothing for 2024-25 because an underground fire halted production. Comparing them produced a 417× "discrepancy" that was purely an artefact of mismatched windows.

Observations are now restricted to **2024-07-01 → 2025-06-30**, and Grosvenor correctly drops out of the comparison.

### 3. The headline metric was an accusation dressed as arithmetic

Dividing an instantaneous peak by an annual average *always* yields a large number. A mine venting 1,000 kg/hr on average will show peaks many times that — it is how averages work. Publishing it as a "ratio" invites every reader to interpret ordinary arithmetic as under-reporting.

**No ratio is published.** Both figures sit side by side with a plain statement that they measure different things. The field was withdrawn from the data, not merely hidden, since the stamper only ever adds keys and a retired metric would otherwise persist indefinitely.

## What the comparison actually shows

| Facility | Declared average | Peak observed | Detections |
|---|---|---|---|
| Appin | 9,156 kg/hr | 1,772 kg/hr | 4 |
| Moranbah North | 5,117 kg/hr | 19,000 kg/hr | 3 |
| Goonyella | 3,549 kg/hr | 1,219 kg/hr | 1 |
| Kestrel | 3,459 kg/hr | 4,741 kg/hr | 11 |
| Aquila-Capcoal | 3,338 kg/hr | 20,000 kg/hr | 13 |
| Ashton | 2,720 kg/hr | 2,116 kg/hr | 9 |
| Mandalong | 2,482 kg/hr | 8,371 kg/hr | 17 |

Note the direction of travel. **Appin and Goonyella declare more than satellites ever caught** — the opposite of under-reporting, and exactly how steady venting should appear to instruments tuned for brief, large events. This is not a tool that finds wrongdoing wherever it looks.

## The one pattern worth attention

Seven facilities declare **≥500 kg/hr average**, were imaged 18–29 times by EMIT, and show **no plume at all** in the same year:

Curragh (3,091 kg/hr), Carmichael (1,882), Blackwater (1,795), Byerwen (635), Drake (622), Russell Vale (618), Cook (555).

**This is not an allegation.** Diffuse venting spread across a large open-cut, which is what most of these are, is precisely what plume imagers are worst at seeing — they detect concentrated releases, not broad low-level emission. The honest reading is that these sites are consistent with their reports *and* invisible to this class of instrument. It is a statement about measurement coverage, not about conduct.

## Limits

- **65 of 184** mapped facilities matched; the rest have no Safeguard entry, or names too different to match confidently. One case sits in `cer_review.json` for manual checking rather than being guessed.
- Safeguard covers facilities above 100 kt CO₂-e; smaller sites report nothing.
- Satellites see large, brief events. Absence of detection is not absence of emission, and the facility watch status says how much looking was actually done.
- The reported figure is self-reported under a regulated method. NGER permits four estimation methods of differing rigour, and which one a facility used is published separately — a worthwhile future addition.

## Rebuilding

```bash
matlas facilities     # detection history + observability
matlas reported       # join the CER table
```

Expects `data/cer/baselines-and-emissions-table-2024-25.csv`, downloaded from the [CER Safeguard data page](https://cer.gov.au/markets/reports-and-data/safeguard-data/2024-25-baselines-and-emissions-data). The site renders its download links via JavaScript, so the file has to be fetched by hand.
