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

type Manifest = {
  kind: string;
  bounds: [number, number, number, number];
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
  return `<div class="popup-title">${esc(props.name ?? "(unnamed)")}</div><div class="popup-kv">${rows}${src}</div>`;
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
  const [band, setBand] = useState<Band>("anomaly");
  const [showMethane, setShowMethane] = useState(true);
  const [visible, setVisible] = useState<Record<string, boolean>>(
    Object.fromEntries(LAYERS.map((l) => [l.id, true])),
  );
  const visibleRef = useRef(visible);
  const [counts, setCounts] = useState<Record<string, number>>({});
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
          map.addLayer({
            id: layer.id,
            type: "circle",
            source: layer.id,
            layout: { visibility },
            paint: {
              "circle-color": color,
              "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 2.5, 8, 6],
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
      setReady(true);
    });

    fetch("/data/status.json")
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => s && setCounts(s.layers ?? {}))
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
    const url = `/data/methane/${band}/${period.period}.png`;
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
  }, [ready, manifest, period, band, showMethane]);

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

  const scale = manifest?.scales[band];
  const legendGradient =
    band === "anomaly"
      ? "linear-gradient(90deg,#2166ac,#67a9cf,#d1e5f0,#f7f7f7,#fddbc7,#ef8a62,#b2182b)"
      : "linear-gradient(90deg,#000004,#1c1044,#4f127b,#812581,#b5367a,#e55064,#fb8761,#fec287,#fcfdbf)";

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
              <button
                className={band === "anomaly" ? "on" : ""}
                onClick={() => setBand("anomaly")}
              >
                Enhancement
              </button>
              <button className={band === "mean" ? "on" : ""} onClick={() => setBand("mean")}>
                Concentration
              </button>
            </div>
            <div className="legend">
              <div className="bar" style={{ background: legendGradient }} />
              <div className="ticks">
                <span>
                  {scale ? (band === "anomaly" ? scale.min.toFixed(0) : scale.min.toFixed(0)) : ""}
                </span>
                <span>{band === "anomaly" ? "ppb vs background" : "ppb"}</span>
                <span>{scale ? `+${scale.max.toFixed(0)}` : ""}</span>
              </div>
            </div>
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
                {period.coverage_pct}% covered · background {period.background_ppb} ppb
              </span>
            </div>
          </>
        ) : (
          <div className="notice">
            Methane composites are still processing. Infrastructure layers below are live.
          </div>
        )}

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
          {manifest ? `${manifest.attribution}. ` : ""}Infrastructure: Geoscience Australia &amp;
          Global Energy Monitor (CC BY 4.0), Open Electricity (CC BY-NC 4.0). Basemap ©
          OpenStreetMap contributors.{" "}
          <a href="https://github.com/samtomrob/methane-atlas" target="_blank" rel="noreferrer">
            source &amp; method
          </a>
        </footer>
      </div>
    </>
  );
}
