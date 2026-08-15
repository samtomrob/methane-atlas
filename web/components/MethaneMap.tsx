"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import maplibregl, { Map as MLMap, MapMouseEvent } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

const ROI: [[number, number], [number, number]] = [
  [108, -45],
  [160, 1],
];

type LayerDef = {
  id: string;
  label: string;
  kind: "line" | "circle";
  color: { light: string; dark: string };
};

const LAYERS: LayerDef[] = [
  { id: "pipelines_oil", label: "Oil pipelines", kind: "line", color: { light: "#937b58", dark: "#b39a72" } },
  { id: "pipelines_gas", label: "Gas pipelines", kind: "line", color: { light: "#1c6a8e", dark: "#4bb6e8" } },
  { id: "coal_mines", label: "Coal mines", kind: "circle", color: { light: "#364a5e", dark: "#c3d4e2" } },
  { id: "gas_plants", label: "Gas power stations", kind: "circle", color: { light: "#6741d9", dark: "#a68cf5" } },
];

type Band = "anomaly" | "mean";
type View = Band | "average";

type Manifest = {
  kind: string;
  bounds: [number, number, number, number];
  climatology: {
    url: string;
    scale: { min: number; max: number; unit: string };
    n_periods: number | null;
    median_stderr_ppb: number | null;
    caveat: string | null;
  } | null;
  scales: Record<Band, { min: number; max: number; unit: string }>;
  periods: {
    period: string;
    coverage_pct: number;
    background_ppb: number;
    max_anomaly_ppb: number | null;
    median_obs_per_cell: number | null;
  }[];
  method: string;
  attribution: string;
};

const PLUME_COLOR = { light: "#c2255c", dark: "#ff6b9d" };

/** Facility verdicts. The distinction no plume catalogue makes: a site with no
 *  detections is only meaningfully clean if something actually looked at it. */
const WATCH = {
  emitting: { light: "#d9480f", dark: "#ff7a2f", label: "Emitting" },
  "watched, no plume": { light: "#0b7285", dark: "#3bc9db", label: "Watched, no plume" },
  "under-observed": { light: "#b0851f", dark: "#ffd43b", label: "Too few looks" },
  "blind spot": { light: "#868e96", dark: "#adb5bd", label: "Never observed" },
  unknown: { light: "#adb5bd", dark: "#6c757d", label: "Unknown" },
} as const;

type WatchKey = keyof typeof WATCH;

function watchColorExpression(dark: boolean): maplibregl.ExpressionSpecification {
  const pairs: string[] = [];
  for (const [key, v] of Object.entries(WATCH)) {
    if (key === "unknown") continue;
    pairs.push(key, dark ? v.dark : v.light);
  }
  return [
    "match",
    ["get", "watch_status"],
    ...pairs,
    dark ? WATCH.unknown.dark : WATCH.unknown.light,
  ] as unknown as maplibregl.ExpressionSpecification;
}

function facilityRecordHtml(props: Record<string, unknown>): string {
  const status = String(props.watch_status ?? "unknown") as WatchKey;
  const meta = WATCH[status] ?? WATCH.unknown;
  const n = Number(props.detections ?? 0);
  const passes = props.emit_overpasses;
  const rows: string[] = [];

  if (n > 0) {
    rows.push(
      `<b>${n}</b> detection${n === 1 ? "" : "s"}` +
        (props.last_detection ? `, most recent <b>${esc(props.last_detection)}</b>` : ""),
    );
    if (props.max_rate_kg_hr != null) {
      rows.push(
        `Peak measured rate <b>${Number(props.max_rate_kg_hr).toLocaleString()} kg/hr</b>`,
      );
    }
  } else if (passes != null) {
    rows.push(
      `<b>No plume detected</b> across <b>${esc(passes)}</b> EMIT overpasses of this site.`,
    );
    rows.push(
      status === "watched, no plume"
        ? `<span style="opacity:.8">Looked at often enough for the absence to mean something.</span>`
        : `<span style="opacity:.8">Too few looks to call it clean — absence here is not evidence.</span>`,
    );
  }
  if (passes != null && n > 0) {
    rows.push(`<span style="opacity:.8">${esc(passes)} EMIT overpasses of this site.</span>`);
  }

  const chip =
    `<span style="display:inline-block;padding:1px 7px;border-radius:3px;font-size:.68rem;` +
    `background:${meta.light}22;color:${meta.light};border:1px solid ${meta.light}55">` +
    `${esc(meta.label)}</span>`;

  // Reported vs observed. Both figures shown, never a ratio: an annual mean
  // and an instantaneous peak are different measurements, and dividing one by
  // the other produces a big number that reads as an accusation.
  let reported = "";
  if (props.reported_avg_kg_hr != null) {
    const avg = Number(props.reported_avg_kg_hr);
    const peak = props.peak_in_period_kg_hr;
    const dets = Number(props.detections_in_period ?? 0);
    reported =
      `<div style="margin-top:7px;padding-top:6px;border-top:1px solid currentColor;opacity:.95">` +
      `<div style="font-size:.7rem;letter-spacing:.06em;text-transform:uppercase;opacity:.65;margin-bottom:3px">` +
      `Reported vs observed · ${esc(String(props.reporting_period ?? "").slice(0, 4))}–${esc(
        String(props.reporting_period ?? "").slice(14, 18),
      )}</div>` +
      `<div class="popup-kv">` +
      `Operator declared <b>${avg.toLocaleString()} kg/hr</b> average` +
      ` <span style="opacity:.7">(${Number(props.reported_methane_tco2e).toLocaleString()} tCO₂-e/yr)</span><br/>` +
      (dets > 0 && peak != null
        ? `Satellites saw a peak of <b>${Number(peak).toLocaleString()} kg/hr</b> across ${dets} detection${dets === 1 ? "" : "s"}`
        : `No plume detected in that year`) +
      `<br/><span style="opacity:.7">An annual average and an instantaneous peak measure different things — ` +
      `a higher peak is expected, not a discrepancy.</span>` +
      `</div></div>`;
  }

  return (
    `<div style="margin-top:6px">${chip}</div>` +
    `<div class="popup-kv" style="margin-top:5px">${rows.join("<br/>")}</div>${reported}`
  );
}

const PROVIDER_LABEL: Record<string, string> = {
  "Carbon Mapper": "Carbon Mapper",
  "NASA EMIT": "EMIT",
  "UNEP IMEO": "UNEP IMEO",
  SRON: "SRON",
};

const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];

function windArrowSvg(towardDeg: number): string {
  // Arrow points the way the plume drifts, so it can be read against the raster.
  return (
    `<svg width="34" height="34" viewBox="0 0 34 34" style="vertical-align:middle">` +
    `<g transform="rotate(${towardDeg} 17 17)">` +
    `<line x1="17" y1="27" x2="17" y2="8" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>` +
    `<path d="M17 5 L22 14 L17 11.5 L12 14 Z" fill="currentColor"/>` +
    `</g></svg>`
  );
}

function daysAgo(iso: string): number | null {
  const d = Date.parse(iso);
  if (Number.isNaN(d)) return null;
  return Math.floor((Date.now() - d) / 86400000);
}

function plumePopupHtml(props: Record<string, unknown>): string {
  const rate = props.emission_kg_hr;
  const unc = props.uncertainty_kg_hr;
  const when = String(props.datetime_utc ?? "").slice(0, 10);
  const provider = String(props.provider ?? "");
  const age = daysAgo(String(props.datetime_utc ?? ""));
  const rows: string[] = [];
  if (rate != null) {
    rows.push(
      `Emission rate: <b>${Number(rate).toLocaleString(undefined, {
        maximumFractionDigits: 0,
      })} kg/hr</b>${unc != null ? ` ± ${Number(unc).toFixed(0)}` : ""}`,
    );
  }
  if (props.wind_speed_ms != null && props.plume_toward_deg != null) {
    const toward = Number(props.plume_toward_deg);
    const oct = COMPASS[Math.floor(((toward + 22.5) % 360) / 45)];
    rows.push(
      `Wind: <b>${Number(props.wind_speed_ms).toFixed(1)} m/s</b>, plume drifting <b>${esc(oct)}</b>` +
        `<span style="float:right;line-height:0">${windArrowSvg(toward)}</span>`,
    );
  }
  if (props.sector) rows.push(`Sector: <b>${esc(props.sector)}</b>`);
  if (props.instrument) rows.push(`Instrument: <b>${esc(props.instrument)}</b>`);

  if (props.facility_name) {
    rows.push(
      `Nearest facility: <b>${esc(props.facility_name)}</b> ` +
        `(${esc(String(props.facility_layer ?? "").replace("_", " "))}, ${esc(props.facility_km)} km)`,
    );
  } else if (props.nearest_name) {
    rows.push(
      `Nearest infrastructure: <b>${esc(props.nearest_name)}</b> ` +
        `(${esc(String(props.nearest_layer ?? "").replace("pipelines_", "").replace("_", " "))}, ` +
        `${esc(props.nearest_km)} km)`,
    );
  } else {
    rows.push("No mapped infrastructure within 10 km");
  }
  rows.push(
    `<span style="opacity:.75">Proximity only — what is nearby, not what emitted. The rate is one` +
      ` instantaneous snapshot and cannot be extrapolated to an annual total.</span>`,
  );
  const link = props.provider_url
    ? `<br/><a href="${esc(props.provider_url)}" target="_blank" rel="noreferrer">View at ${esc(provider)}</a>`
    : "";
  const ageLabel = age == null ? "" : age < 1 ? " · today" : ` · ${age} days ago`;
  const imagery = props.imagery
    ? `<div style="margin-top:6px;font-size:.72rem;opacity:.8">Concentration imagery shown on the map.</div>`
    : "";
  return (
    `<div class="popup-title">${esc(PROVIDER_LABEL[provider] ?? provider)} plume — ${esc(when)}${esc(ageLabel)}</div>` +
    `<div class="popup-kv">${rows.join("<br/>")}${link}</div>${imagery}`
  );
}

const POPUP_FIELDS: [string, string][] = [
  ["status", "Status"],
  ["state", "State"],
  ["country", "Country"],
  ["subtype", "Type"],
  ["fuel_techs", "Fuel tech"],
  ["capacity_mw", "Capacity (MW)"],
  ["length_km", "Length (km)"],
];

function esc(v: unknown): string {
  return String(v)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function popupHtml(props: Record<string, unknown>): string {
  const rows = POPUP_FIELDS.filter(([k]) => props[k] != null && props[k] !== "")
    .map(([k, label]) => `${label}: <b>${esc(props[k])}</b>`)
    .join("<br/>");
  const src = props.source
    ? `<br/>Source: ${
        props.source_url && String(props.source_url).startsWith("http")
          ? `<a href="${esc(props.source_url)}" target="_blank" rel="noreferrer">${esc(props.source)}</a>`
          : esc(props.source)
      } (${esc(props.license ?? "")})`
    : "";
  // Facilities carry a watch record; linear infrastructure does not.
  const record = props.watch_status ? facilityRecordHtml(props) : "";
  return (
    `<div class="popup-title">${esc(props.name ?? "(unnamed)")}</div>` +
    `${record}<div class="popup-kv" style="margin-top:5px">${rows}${src}</div>`
  );
}

/** Drape the selected plume's concentration raster at its own bounds.
 *
 * Carbon Mapper publishes the raster plus `plume_bounds`, so the detection can
 * be shown as what it actually looked like rather than as a dot. The scene
 * backdrop goes underneath, the concentration on top.
 */
function showPlumeImagery(map: MLMap, props: Record<string, unknown>) {
  const bounds = props.imagery_bounds as [number, number, number, number] | undefined;
  const imagery = props.imagery as Record<string, string> | undefined;
  const clear = () => {
    for (const id of ["plume-raster", "plume-backdrop"]) {
      if (map.getLayer(id)) map.removeLayer(id);
      if (map.getSource(id)) map.removeSource(id);
    }
  };
  clear();
  if (!bounds || !imagery) return;

  const coords = imageCoords(bounds);
  const add = (id: string, file: string, opacity: number, beforeId?: string) => {
    map.addSource(id, { type: "image", url: `/data/plume-imagery/${file}`, coordinates: coords });
    map.addLayer(
      { id, type: "raster", source: id, paint: { "raster-opacity": opacity, "raster-fade-duration": 0 } },
      beforeId,
    );
  };
  if (imagery.rgb_png) add("plume-backdrop", imagery.rgb_png, 0.9, "plumes");
  if (imagery.plume_png) add("plume-raster", imagery.plume_png, 0.95, "plumes");
}

// MapLibre image sources take corners clockwise from top-left.
function imageCoords(b: [number, number, number, number]) {
  const [w, s, e, n] = b;
  return [
    [w, n],
    [e, n],
    [e, s],
    [w, s],
  ] as [[number, number], [number, number], [number, number], [number, number]];
}

export default function MethaneMap() {
  const mapRef = useRef<MLMap | null>(null);
  const [ready, setReady] = useState(false);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [periodIdx, setPeriodIdx] = useState(0);
  const [view, setView] = useState<View>("average");
  const [showMethane, setShowMethane] = useState(true);
  const [visible, setVisible] = useState<Record<string, boolean>>(
    Object.fromEntries(LAYERS.map((l) => [l.id, true])),
  );
  const visibleRef = useRef(visible);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [showPlumes, setShowPlumes] = useState(true);
  // Recency window. The data itself is weeks-to-months old by provider policy,
  // so the default opens wide enough to actually contain something.
  const AGE_STEPS = [30, 90, 180, 365, 730, 9999];
  const [ageIdx, setAgeIdx] = useState(3);
  const [plumeCounts, setPlumeCounts] = useState<{ total: number; shown: number }>({
    total: 0,
    shown: 0,
  });
  const [facilityStats, setFacilityStats] = useState<{
    facilities: number;
    by_status: Record<string, number>;
  } | null>(null);
  const [plumeStatus, setPlumeStatus] = useState<{
    count: number;
    near_infrastructure: number;
    near_facility?: number;
    high_confidence: number;
    by_provider: Record<string, string>;
    newest_days_old?: number;
    newest_detection?: string;
  } | null>(null);
  const dark =
    typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;

  useEffect(() => {
    const map = new maplibregl.Map({
      container: "map",
      style: `https://tiles.openfreemap.org/styles/${dark ? "dark" : "positron"}`,
      bounds: ROI,
      fitBoundsOptions: { padding: 24 },
      attributionControl: { compact: true },
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

    map.on("load", () => {
      for (const layer of LAYERS) {
        map.addSource(layer.id, { type: "geojson", data: `/data/${layer.id}.geojson` });
        const color = dark ? layer.color.dark : layer.color.light;
        const visibility = visibleRef.current[layer.id] ? "visible" : "none";
        if (layer.kind === "line") {
          map.addLayer({
            id: layer.id,
            type: "line",
            source: layer.id,
            layout: { visibility },
            paint: { "line-color": color, "line-width": 1.4, "line-opacity": 0.9 },
          });
        } else {
          // Facilities are coloured by their watch verdict, not by layer:
          // whether a site is emitting, checked-and-clean, or simply
          // unexamined is the most useful thing the map can say about it.
          const facilityLayer = layer.id === "coal_mines" || layer.id === "gas_plants";
          map.addLayer({
            id: layer.id,
            type: "circle",
            source: layer.id,
            layout: { visibility },
            paint: {
              "circle-color": facilityLayer ? watchColorExpression(dark) : color,
              "circle-radius": facilityLayer
                ? [
                    "interpolate",
                    ["linear"],
                    ["zoom"],
                    3,
                    ["case", [">", ["coalesce", ["get", "detections"], 0], 0], 4, 2.5],
                    8,
                    ["case", [">", ["coalesce", ["get", "detections"], 0], 0], 9, 5],
                  ]
                : ["interpolate", ["linear"], ["zoom"], 3, 2.5, 8, 6],
              "circle-opacity": 0.9,
              "circle-stroke-width": 1,
              "circle-stroke-color": dark ? "#0e1519" : "#ffffff",
            },
          });
        }
        map.on("click", layer.id, (e: MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }) => {
          const f = e.features?.[0];
          if (!f) return;
          new maplibregl.Popup({ closeButton: true })
            .setLngLat(e.lngLat)
            .setHTML(popupHtml(f.properties ?? {}))
            .addTo(map);
        });
        map.on("mouseenter", layer.id, () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", layer.id, () => (map.getCanvas().style.cursor = ""));
      }
      // Plumes sit above everything: they are the only layer that reports a
      // measured emission rate at a specific place.
      map.addSource("plumes", { type: "geojson", data: "/data/plumes.geojson" });
      map.addLayer({
        id: "plumes",
        type: "circle",
        source: "plumes",
        paint: {
          "circle-color": dark ? PLUME_COLOR.dark : PLUME_COLOR.light,
          // Radius carries emission rate where a provider reports one;
          // detections without a rate still show at a legible base size.
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["zoom"],
            3,
            ["case", ["has", "emission_kg_hr"], ["min", 9, ["+", 3, ["/", ["get", "emission_kg_hr"], 400]]], 4],
            9,
            ["case", ["has", "emission_kg_hr"], ["min", 22, ["+", 7, ["/", ["get", "emission_kg_hr"], 150]]], 9],
          ],
          "circle-opacity": 0.75,
          "circle-stroke-width": 1.5,
          "circle-stroke-color": dark ? "#ffd6e5" : "#ffffff",
        },
      });
      map.on("click", "plumes", (e: MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }) => {
        const f = e.features?.[0];
        if (!f) return;
        const props = f.properties ?? {};
        new maplibregl.Popup({ closeButton: true, maxWidth: "300px" })
          .setLngLat(e.lngLat)
          .setHTML(plumePopupHtml(props))
          .addTo(map);
        showPlumeImagery(map, props);
      });
      map.on("mouseenter", "plumes", () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", "plumes", () => (map.getCanvas().style.cursor = ""));

      setReady(true);
    });

    fetch("/data/status.json")
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => s && setCounts(s.layers ?? {}))
      .catch(() => {});

    fetch("/data/facilities.json")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d?.summary && setFacilityStats(d.summary))
      .catch(() => {});

    fetch("/data/plumes_status.json")
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => s && setPlumeStatus(s))
      .catch(() => {});

    fetch("/data/methane/manifest.json")
      .then((r) => (r.ok ? r.json() : null))
      .then((m: Manifest | null) => {
        if (!m?.periods?.length) return;
        setManifest(m);
        setPeriodIdx(m.periods.length - 1); // open on the most recent month
      })
      .catch(() => {});

    return () => map.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Methane raster: create once the map and manifest are both available, then
  // swap the image in place as the slider or band changes.
  const period = manifest?.periods[periodIdx];
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !manifest || !period) return;
    const url =
      view === "average" && manifest.climatology
        ? `/data/methane/${manifest.climatology.url}`
        : `/data/methane/${view === "average" ? "anomaly" : view}/${period.period}.png`;
    const existing = map.getSource("methane") as maplibregl.ImageSource | undefined;
    if (!existing) {
      map.addSource("methane", {
        type: "image",
        url,
        coordinates: imageCoords(manifest.bounds),
      });
      map.addLayer(
        {
          id: "methane",
          type: "raster",
          source: "methane",
          paint: { "raster-opacity": 0.85, "raster-resampling": "nearest" },
        },
        LAYERS[0].id, // keep infrastructure drawn on top
      );
    } else {
      existing.updateImage({ url });
    }
    map.setLayoutProperty("methane", "visibility", showMethane ? "visible" : "none");
  }, [ready, manifest, period, view, showMethane]);

  // Plume timestamps, loaded once, so moving the slider is instant rather than
  // refetching a 480 KB collection on every step.
  const plumeAges = useRef<number[] | null>(null);
  useEffect(() => {
    fetch("/data/plumes.geojson")
      .then((r) => (r.ok ? r.json() : null))
      .then((g) => {
        if (!g) return;
        plumeAges.current = g.features
          .map((f: { properties: { datetime_utc?: string } }) =>
            Date.parse(f.properties.datetime_utc ?? ""),
          )
          .filter((t: number) => !Number.isNaN(t));
        setAgeIdx((i) => i); // recompute counts once ages are known
      })
      .catch(() => {});
  }, []);

  // Recency filter. Provider timestamps are all ISO-prefixed, so a lexicographic
  // comparison against a YYYY-MM-DD cutoff is exact and needs no reprocessing.
  useEffect(() => {
    const map = mapRef.current;
    const days = AGE_STEPS[ageIdx];
    if (ready && map?.getLayer("plumes")) {
      if (days >= 9999) {
        map.setFilter("plumes", null);
      } else {
        const cutoff = new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);
        map.setFilter("plumes", [">=", ["get", "datetime_utc"], cutoff]);
      }
    }
    const ages = plumeAges.current;
    if (ages) {
      const cutoffMs = Date.now() - days * 86400000;
      setPlumeCounts({
        total: ages.length,
        shown: days >= 9999 ? ages.length : ages.filter((t) => t >= cutoffMs).length,
      });
    }
  }, [ready, ageIdx, plumeStatus]);

  const toggle = useCallback(
    (id: string) => {
      setVisible((prev) => {
        const next = { ...prev, [id]: !prev[id] };
        visibleRef.current = next;
        const map = mapRef.current;
        if (map?.getLayer(id)) {
          map.setLayoutProperty(id, "visibility", next[id] ? "visible" : "none");
        }
        return next;
      });
    },
    [],
  );

  const scale =
    view === "average" ? manifest?.climatology?.scale : manifest?.scales[view as Band];
  const legendGradient =
    view === "mean"
      ? "linear-gradient(90deg,#000004,#1c1044,#4f127b,#812581,#b5367a,#e55064,#fb8761,#fec287,#fcfdbf)"
      : "linear-gradient(90deg,#2166ac,#67a9cf,#d1e5f0,#f7f7f7,#fddbc7,#ef8a62,#b2182b)";

  return (
    <>
      <div id="map" />
      <div className="panel">
        <h1>Methane Atlas</h1>
        <p className="sub">Methane and its likely sources across Australia &amp; Papua New Guinea.</p>

        {manifest && period ? (
          <>
            <div className="section-label">Methane — {manifest.kind}ly</div>
            <label className="layer-toggle">
              <input
                type="checkbox"
                checked={showMethane}
                onChange={() => setShowMethane((v) => !v)}
              />
              Show methane layer
            </label>
            <div className="seg">
              {manifest.climatology ? (
                <button className={view === "average" ? "on" : ""} onClick={() => setView("average")}>
                  Average
                </button>
              ) : null}
              <button className={view === "anomaly" ? "on" : ""} onClick={() => setView("anomaly")}>
                Monthly
              </button>
              <button className={view === "mean" ? "on" : ""} onClick={() => setView("mean")}>
                Concentration
              </button>
            </div>
            <div className="legend">
              <div className="bar" style={{ background: legendGradient }} />
              <div className="ticks">
                <span>{scale ? scale.min.toFixed(0) : ""}</span>
                <span>{view === "mean" ? "ppb" : "ppb vs background"}</span>
                <span>{scale ? `+${scale.max.toFixed(0)}` : ""}</span>
              </div>
            </div>

            {view === "average" ? (
              <div className="period">
                <b>{manifest.climatology?.n_periods ?? manifest.periods.length}-month average</b>
                <span>
                  ±{manifest.climatology?.median_stderr_ppb ?? "?"} ppb typical uncertainty ·
                  coastal cells excluded
                </span>
              </div>
            ) : (
              <>
                <input
                  className="slider"
                  type="range"
                  min={0}
                  max={manifest.periods.length - 1}
                  value={periodIdx}
                  onChange={(e) => setPeriodIdx(Number(e.target.value))}
                  aria-label="Time period"
                />
                <div className="period">
                  <b>{period.period}</b>
                  <span>
                    {period.coverage_pct}% covered · background {period.background_ppb} ppb ·
                    median {period.median_obs_per_cell} obs/cell
                  </span>
                </div>
              </>
            )}

            <p className="caveat">
              {view === "average"
                ? "Observed column enhancement, not emissions. Coal and gas basins read positive, but so does the WA wheatbelt where there is no known source — TROPOMI responds to surface brightness too, and here that effect is larger than the Bowen Basin signal."
                : "A single month is mostly retrieval noise at this resolution — the median cell has only 4–6 observations. Use the average for anything you want to rely on."}
            </p>
            <p className="caveat">
              Papua New Guinea has almost no coverage (0.2% of cells): tropical cloud blocks the
              retrieval. PNG methane will come from the plume layers, not this one.
            </p>
          </>
        ) : (
          <div className="notice">
            Methane composites are still processing. Infrastructure layers below are live.
          </div>
        )}

        {plumeStatus && plumeStatus.count > 0 ? (
          <>
            <div className="section-label">Point sources</div>
            {plumeStatus.newest_days_old != null ? (
              <div className="freshness">
                <span className="dot" />
                <span>
                  Newest detection <b>{plumeStatus.newest_days_old} days old</b>. Providers hold
                  plumes back ~30 days before publishing, so this is as current as the data gets.
                </span>
              </div>
            ) : null}
            <label className="layer-toggle">
              <input
                type="checkbox"
                checked={showPlumes}
                onChange={() => {
                  const next = !showPlumes;
                  setShowPlumes(next);
                  const map = mapRef.current;
                  if (map?.getLayer("plumes")) {
                    map.setLayoutProperty("plumes", "visibility", next ? "visible" : "none");
                  }
                }}
              />
              <span
                className="swatch round"
                style={{ background: dark ? PLUME_COLOR.dark : PLUME_COLOR.light }}
              />
              Detected plumes
              <span className="count">{plumeStatus.count}</span>
            </label>
            <div className="agerow">
              <span>Detected within</span>
              <b>
                {AGE_STEPS[ageIdx] >= 9999
                  ? "all time"
                  : AGE_STEPS[ageIdx] >= 365
                    ? `${AGE_STEPS[ageIdx] / 365} year${AGE_STEPS[ageIdx] > 365 ? "s" : ""}`
                    : `${AGE_STEPS[ageIdx]} days`}
              </b>
            </div>
            <input
              className="slider"
              type="range"
              min={0}
              max={AGE_STEPS.length - 1}
              value={ageIdx}
              onChange={(e) => setAgeIdx(Number(e.target.value))}
              aria-label="Detection recency window"
            />
            <div className="period">
              <span>
                showing <b>{plumeCounts.shown.toLocaleString()}</b> of{" "}
                {plumeCounts.total.toLocaleString()} plumes
              </span>
            </div>
            <p className="caveat">
              {plumeStatus.near_facility ?? plumeStatus.near_infrastructure} of{" "}
              {plumeStatus.count} sit within 10 km of a mine or gas plant. Circle size shows the
              measured emission rate — an instantaneous snapshot, not an annual total. Click a
              plume to see its concentration image and wind.
            </p>
          </>
        ) : null}

        {facilityStats ? (
          <>
            <div className="section-label">Facility watch</div>
            <p className="caveat" style={{ marginTop: 0 }}>
              Every mapped mine and gas plant, scored on whether anyone has actually looked at
              it. A site with no detections only counts as clean if it has been observed.
            </p>
            {(
              [
                ["emitting", facilityStats.by_status?.emitting],
                ["watched, no plume", facilityStats.by_status?.["watched, no plume"]],
                ["under-observed", facilityStats.by_status?.["under-observed"]],
                ["blind spot", facilityStats.by_status?.["blind spot"]],
              ] as [WatchKey, number | undefined][]
            )
              .filter(([, n]) => n)
              .map(([key, n]) => (
                <div key={key} className="layer-toggle" style={{ cursor: "default" }}>
                  <span
                    className="swatch round"
                    style={{ background: dark ? WATCH[key].dark : WATCH[key].light }}
                  />
                  {WATCH[key].label}
                  <span className="count">{n}</span>
                </div>
              ))}
          </>
        ) : null}

        <div className="section-label">Infrastructure</div>
        {LAYERS.map((l) => (
          <label key={l.id} className="layer-toggle">
            <input type="checkbox" checked={visible[l.id]} onChange={() => toggle(l.id)} />
            <span
              className={`swatch${l.kind === "circle" ? " round" : ""}`}
              style={{ background: dark ? l.color.dark : l.color.light }}
            />
            {l.label}
            <span className="count">{counts[l.id]?.toLocaleString() ?? ""}</span>
          </label>
        ))}

        <footer>
          <b>Plume detections are other organisations&rsquo; work, not ours.</b> Every plume shown
          was found and quantified by:{" "}
          <a href="https://methanedata.unep.org/" target="_blank" rel="noreferrer">
            UNEP IMEO / MARS
          </a>{" "}
          (CC BY-NC-SA 4.0),{" "}
          <a href="https://carbonmapper.org/" target="_blank" rel="noreferrer">
            Carbon Mapper
          </a>{" "}
          (non-commercial use, attribution required),{" "}
          <a href="https://www.sron.nl/" target="_blank" rel="noreferrer">
            SRON
          </a>{" "}
          (CC BY 4.0, Schuit et al. 2023), and{" "}
          <a href="https://earth.jpl.nasa.gov/emit/" target="_blank" rel="noreferrer">
            NASA EMIT
          </a>{" "}
          (doi:10.5067/EMIT/EMITL2BCH4PLM.002). This project contributes the regional fusion,
          duplicate matching and infrastructure association only.
          <br />
          <br />
          {manifest ? `${manifest.attribution}. ` : ""}Infrastructure: Geoscience Australia &amp;
          Global Energy Monitor (CC BY 4.0), Open Electricity (CC BY-NC 4.0). Basemap ©
          OpenStreetMap contributors. Non-commercial use only — several sources require it.{" "}
          <a
            href="https://github.com/samtomrob/methane-atlas/blob/main/docs/FINDINGS.md"
            target="_blank"
            rel="noreferrer"
          >
            method &amp; known limitations
          </a>
        </footer>
      </div>
    </>
  );
}
