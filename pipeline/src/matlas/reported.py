"""Reported versus observed — what a facility declares against what satellites see.

Australia is unusual: under the Safeguard Mechanism the Clean Energy Regulator
publishes, per facility, a "GHG Methane" figure in tonnes CO2-equivalent. Every
methane portal shows detections; none joins them to what the operator itself
reported. That join is the point of this project.

Putting the two in the same units is what makes it legible:

    t CH4 / year  = tCO2-e / GWP
    kg/hr average = t CH4 x 1000 / 8760

A satellite gives an instantaneous rate at one overpass; the CER figure is an
annual total. They are not the same measurement and the code never pretends
otherwise — but expressing the reported total as an average kg/hr puts a
satellite snapshot on a scale a reader can reason about.

Interpretation, stated plainly because it cuts both ways:

  * Observed peaks *below* the reported average are expected. Satellites catch
    large, brief events; steady low-level venting is invisible to them.
  * Observed peaks far *above* the reported average are worth a second look —
    though a single plume is a moment, not a year.
  * A facility reporting large methane with no detections at all is not a
    contradiction: it may vent below the detection floor, or simply never have
    been imaged. The watch status says which.

Name matching is deliberately conservative. CER uses operational names
("APN01 Appin Colliery - ICH Facility") where our infrastructure layer uses
common ones ("Appin"). Wrong matches in an accountability tool are defamatory,
so anything short of confident is written to a review file instead of published.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
from pathlib import Path
from typing import Any

# NGER reports methane on AR5 GWP-100, where 1 t CH4 = 28 t CO2-e.
GWP_METHANE = 28
HOURS_PER_YEAR = 8760

# The reported figure covers one Australian financial year. Comparing it to
# detections from other years is meaningless — Grosvenor, for instance, reports
# almost nothing for 2024-25 because an underground fire halted production, yet
# carries detections back to 2021. Observations are therefore scoped to the same
# window before any comparison is made.
REPORTING_PERIOD = ("2024-07-01", "2025-06-30")

# Industries where a methane figure plausibly belongs to something we map.
RELEVANT_ANZSIC = ("coal mining", "oil and gas extraction", "gas supply", "electricity")

# Operational cruft that carries no identifying signal.
NOISE_WORDS = {
    "facility", "operations", "operation", "colliery", "mine", "mines", "mining",
    "coal", "project", "the", "pty", "ltd", "limited", "no", "joint", "venture",
    "complex", "site", "plant", "station", "power", "and", "of",
}
# Leading site codes such as "APN01 " or "ARC01 ".
CODE_RE = re.compile(r"^[A-Z]{2,4}\d{2,3}\b[\s-]*")


def _tokens(name: str) -> set[str]:
    cleaned = CODE_RE.sub("", (name or "").strip())
    cleaned = re.sub(r"\([^)]*\)", " ", cleaned)
    cleaned = re.sub(r"[^A-Za-z0-9 ]+", " ", cleaned).lower()
    return {t for t in cleaned.split() if t and t not in NOISE_WORDS and len(t) > 2}


def _num(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).replace(",", "").replace("$", "").strip()
    if s in ("", "-", "–", "N/A", "n/a"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def load_cer(path: Path) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit(f"cannot decode {path}")

    lines = text.splitlines()
    header = next(
        (i for i, l in enumerate(lines[:25]) if "facility" in l.lower() and l.count(",") >= 3),
        0,
    )
    rows = list(csv.DictReader(io.StringIO("\n".join(lines[header:]))))

    out = []
    for r in rows:
        name = (r.get("Facility name") or "").strip()
        if not name:
            continue
        methane = _num(r.get("GHG Methane"))
        out.append(
            {
                "cer_name": name,
                "operator": (r.get("Responsible emitter") or "").strip(),
                "state": (r.get("State/Territory of operation") or "").strip(),
                "industry": (r.get("ANZSIC") or "").strip(),
                "reported_methane_tco2e": methane,
                "covered_emissions_tco2e": _num(r.get("Covered emissions")),
                "baseline_tco2e": _num(r.get("Baseline emissions number")),
                "tokens": _tokens(name),
            }
        )
    return out


def score(fac: dict[str, Any], cer: dict[str, Any]) -> float:
    """Confidence that a mapped facility and a CER entry are the same site."""
    ftok, ctok = _tokens(fac["name"]), cer["tokens"]
    if not ftok or not ctok:
        return 0.0
    overlap = ftok & ctok
    if not overlap:
        return 0.0

    # Jaccard-ish, biased toward covering the shorter (usually our) name.
    s = len(overlap) / min(len(ftok), len(ctok))

    # State agreement is strong evidence; disagreement is close to fatal, since
    # mine names repeat across states.
    fstate = (fac.get("state") or "").upper()
    cstate = (cer.get("state") or "").upper()
    if fstate and cstate:
        s += 0.25 if fstate == cstate else -0.6

    # Industry coherence is a hard gate, not a nudge.
    #
    # A name can match perfectly and still be the wrong entity: our "Moranbah"
    # gas plant scored 1.0 against CER's "Moranbah", which is Dyno Nobel's
    # ammonia plant. Published, that would have read as "reports 1 kg/h, we
    # observe 19,000" — a false accusation against a company whose methane
    # report is entirely correct. Wrong industry now disqualifies outright.
    industry = cer["industry"].lower()
    if fac["layer"] == "coal_mines":
        if "coal" not in industry:
            return 0.0
        s += 0.2
    elif fac["layer"] == "gas_plants":
        if not any(k in industry for k in ("gas", "electricity", "petroleum")):
            return 0.0
        s += 0.2

    # A single short shared token is weak on its own.
    if len(overlap) == 1 and max(len(t) for t in overlap) <= 4:
        s -= 0.3
    return s


ACCEPT = 0.95  # published
REVIEW = 0.60  # written to the review file, not published


def match(facilities: list[dict[str, Any]], cer: list[dict[str, Any]]):
    accepted: dict[str, dict[str, Any]] = {}
    review: list[dict[str, Any]] = []
    used: set[str] = set()

    pairs = []
    for fac in facilities:
        for c in cer:
            s = score(fac, c)
            if s >= REVIEW:
                pairs.append((s, fac, c))
    pairs.sort(key=lambda p: -p[0])

    for s, fac, c in pairs:
        key = f"{fac['layer']}::{fac['name']}"
        if key in accepted or c["cer_name"] in used:
            continue
        if s >= ACCEPT:
            accepted[key] = {**c, "match_score": round(s, 2)}
            used.add(c["cer_name"])
        else:
            review.append(
                {
                    "facility": fac["name"],
                    "layer": fac["layer"],
                    "state": fac.get("state"),
                    "cer_name": c["cer_name"],
                    "cer_state": c["state"],
                    "cer_methane_tco2e": c["reported_methane_tco2e"],
                    "score": round(s, 2),
                }
            )
    return accepted, review


def _observations_in_period(
    plumes: list[dict[str, Any]], name: str, layer: str
) -> dict[str, Any]:
    """Detections for one facility inside the CER reporting year."""
    lo, hi = REPORTING_PERIOD
    rates, count = [], 0
    for p in plumes:
        props = p["properties"]
        if props.get("facility_name") != name or props.get("facility_layer") != layer:
            continue
        when = (props.get("datetime_utc") or "")[:10]
        if not (lo <= when <= hi):
            continue
        count += 1
        if props.get("emission_kg_hr"):
            rates.append(props["emission_kg_hr"])
    return {
        "detections_in_period": count,
        "peak_in_period_kg_hr": round(max(rates)) if rates else None,
    }


def enrich(data_dir: Path, cer_path: Path) -> dict[str, Any]:
    fac_path = data_dir / "facilities.json"
    payload = json.loads(fac_path.read_text(encoding="utf-8"))
    facilities = payload["facilities"]
    plumes = json.loads((data_dir / "plumes.geojson").read_text(encoding="utf-8"))["features"]
    cer = load_cer(cer_path)
    print(f"{len(cer)} CER facilities, {len(facilities)} mapped facilities")

    with_methane = [c for c in cer if (c["reported_methane_tco2e"] or 0) > 0]
    print(f"  {len(with_methane)} CER entries report methane > 0")

    accepted, review = match(facilities, cer)
    print(f"  matched {len(accepted)} confidently, {len(review)} need review")

    matched_with_data = 0
    for fac in facilities:
        key = f"{fac['layer']}::{fac['name']}"
        m = accepted.get(key)
        if not m:
            continue
        fac["cer_name"] = m["cer_name"]
        fac["operator"] = m["operator"] or fac.get("operator")
        fac["cer_match_score"] = m["match_score"]
        fac["covered_emissions_tco2e"] = m["covered_emissions_tco2e"]
        fac["baseline_tco2e"] = m["baseline_tco2e"]
        t = m["reported_methane_tco2e"]
        if t and t > 0:
            fac["reported_methane_tco2e"] = round(t)
            tonnes_ch4 = t / GWP_METHANE
            fac["reported_ch4_tonnes_yr"] = round(tonnes_ch4)
            fac["reported_avg_kg_hr"] = round(tonnes_ch4 * 1000 / HOURS_PER_YEAR)
            fac["reporting_period"] = f"{REPORTING_PERIOD[0]}..{REPORTING_PERIOD[1]}"
            matched_with_data += 1

            # Same-period observations only. No ratio is published: an
            # instantaneous peak divided by an annual mean is always a large
            # number and reads as an accusation, when it is arithmetic. Both
            # figures are carried so a reader can see them side by side.
            fac.update(_observations_in_period(plumes, fac["name"], fac["layer"]))

    payload["facilities"] = facilities
    payload["summary"]["reported"] = {
        "cer_source": cer_path.name,
        "cer_facilities": len(cer),
        "matched": len(accepted),
        "matched_with_methane": matched_with_data,
        "needs_review": len(review),
        "gwp_methane": GWP_METHANE,
        "accept_threshold": ACCEPT,
        "reporting_period": f"{REPORTING_PERIOD[0]}..{REPORTING_PERIOD[1]}",
        "note": (
            "Reported figures are annual totals under the Safeguard Mechanism for "
            f"{REPORTING_PERIOD[0][:4]}-{REPORTING_PERIOD[1][:4]}, converted at GWP "
            f"{GWP_METHANE} and spread over {HOURS_PER_YEAR} hours to give an average "
            "kg/hr. Observations are restricted to the same window. The two are "
            "different measurements — an annual mean against instantaneous peaks — "
            "so no ratio between them is published: a peak far above an average is "
            "expected arithmetic, not evidence of under-reporting. Matching requires "
            "industry agreement, because a name alone can match the wrong company."
        ),
    }
    fac_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    (data_dir / "cer_review.json").write_text(
        json.dumps(sorted(review, key=lambda r: -r["score"]), indent=2), encoding="utf-8"
    )
    return payload


def stamp(data_dir: Path, facilities: list[dict[str, Any]]) -> int:
    by_key = {(f["layer"], f["name"]): f for f in facilities}
    n = 0
    for layer in ("coal_mines", "gas_plants"):
        path = data_dir / f"{layer}.geojson"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for feat in data.get("features", []):
            rec = by_key.get((layer, (feat.get("properties") or {}).get("name")))
            if not rec:
                continue
            for field in (
                "reported_methane_tco2e",
                "reported_avg_kg_hr",
                "reporting_period",
                "detections_in_period",
                "peak_in_period_kg_hr",
                "operator",
                "cer_name",
            ):
                if rec.get(field) is not None:
                    feat["properties"][field] = rec[field]
                    n += 1
        path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return n


def run(data_dir: Path, cer_path: Path) -> dict[str, Any]:
    if not cer_path.exists():
        raise SystemExit(f"CER file not found: {cer_path}")
    payload = enrich(data_dir, cer_path)
    facilities = payload["facilities"]
    stamp(data_dir, facilities)

    rated = [
        f for f in facilities
        if f.get("reported_avg_kg_hr") and f.get("detections_in_period")
    ]
    rated.sort(key=lambda f: -(f.get("reported_avg_kg_hr") or 0))

    period = f"{REPORTING_PERIOD[0]}..{REPORTING_PERIOD[1]}"
    print(f"\n{len(rated)} facilities have a reported figure AND detections within {period}\n")
    print(f"{'facility':26s} {'reported avg':>14s} {'peak seen':>11s} {'dets':>5s}")
    for f in rated[:16]:
        peak = f.get("peak_in_period_kg_hr")
        print(
            f"{f['name'][:26]:26s} {f['reported_avg_kg_hr']:>11,} kg/h "
            f"{(f'{peak:,}' if peak else '—'):>11s} {f['detections_in_period']:>5d}"
        )

    # The genuinely interesting case, and the only one worth flagging: a
    # facility that reports substantial methane, was imaged repeatedly, and
    # still shows nothing. Either it vents below the detection floor, or the
    # reporting and the sky disagree.
    silent = [
        f for f in facilities
        if (f.get("reported_avg_kg_hr") or 0) >= 500
        and not f.get("detections_in_period")
        and f.get("watch_status") == "watched, no plume"
    ]
    if silent:
        silent.sort(key=lambda f: -(f["reported_avg_kg_hr"]))
        print(
            f"\n{len(silent)} facilities report >=500 kg/h average, were imaged repeatedly,"
            f"\nand show no plume in the same period:"
        )
        for f in silent[:12]:
            print(
                f"  {f['name'][:26]:26s} reports {f['reported_avg_kg_hr']:>6,} kg/h  "
                f"{f.get('emit_overpasses')} EMIT passes"
            )
        print(
            "  (Not an accusation: steady venting spread across a site is exactly what"
            "\n   these instruments are worst at seeing.)"
        )
    print(f"\n-> {data_dir / 'facilities.json'}")
    return payload["summary"]["reported"]
