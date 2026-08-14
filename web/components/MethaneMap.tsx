"use client";

import { useEffect, useRef, useState } from "react";
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

// Overlay layers, drawn in this order (lines under points).
const LAYERS: LayerDef[] = [
  { id: "pipelines_oil", label: "Oil pipelines", kind: "line", color: { light: "#937b58", dark: "#b39a72" } },
  { id: "pipelines_gas", label: "Gas pipelines", kind: "line", color: { light: "#d9480f", dark: "#ff7a2f" } },
  { id: "coal_mines", label: "Coal mines", kind: "circle", color: { light: "#364a5e", dark: "#9db8cd" } },
  { id: "gas_plants", label: "Gas power stations", kind: "circle", color: { light: "#6741d9", dark: "#a68cf5" } },
];

const POPUP_FIELDS: [string, string][] = [
  ["status", "Status"],
  ["state", "State"],
  ["country", "Country"],
  ["subtype", "Type"],
  ["fuel_techs", "Fuel tech"],
  ["capacity_mw", "Capacity (MW)"],
  ["length_km", "Length (km)"],
];

function popupHtml(props: Record<string, unknown>): string {
  const esc = (v: unknown) =>
    String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const rows = POPUP_FIELDS.filter(([k]) => props[k] != null && props[k] !== "")
    .map(([k, label]) => `${label}: <b>${esc(props[k])}</b>`)
    .join("<br/>");
  const src = props.source
    ? `<br/>Source: ${
        props.source_url && String(props.source_url).startsWith("http")
          ? `<a href="${esc(props.source_url)}" target="_blank" rel="noreferrer">${esc(props.source)}</a>`
          : `${esc(props.source)}`
      } (${esc(props.license ?? "")})`
    : "";
  return `<div class="popup-title">${esc(props.name ?? "(unnamed)")}</div><div class="popup-kv">${rows}${src}</div>`;
}

export default function MethaneMap() {
  const mapRef = useRef<MLMap | null>(null);
  const [visible, setVisible] = useState<Record<string, boolean>>(
    Object.fromEntries(LAYERS.map((l) => [l.id, true])),
  );
  // Layers are added asynchronously on style load; reading visibility from a
  // ref lets toggles made before that moment still apply.
  const visibleRef = useRef(visible);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [generatedAt, setGeneratedAt] = useState<string>("");
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
            paint: { "line-color": color, "line-width": 1.6, "line-opacity": 0.85 },
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
              "circle-opacity": 0.85,
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
    });

    fetch("/data/status.json")
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => {
        if (!s) return;
        setCounts(s.layers ?? {});
        setGeneratedAt(String(s.generated_at ?? "").slice(0, 10));
      })
      .catch(() => {});

    return () => map.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggle(id: string) {
    const next = { ...visible, [id]: !visible[id] };
    setVisible(next);
    visibleRef.current = next;
    const map = mapRef.current;
    if (map?.getLayer(id)) {
      map.setLayoutProperty(id, "visibility", next[id] ? "visible" : "none");
    }
  }

  return (
    <>
      <div id="map" />
      <div className="panel">
        <h1>Methane Atlas</h1>
        <p className="sub">
          Methane sources across Australia &amp; PNG — v0 infrastructure scaffold (working name).
        </p>
        <div className="notice">
          Satellite methane layers (TROPOMI weekly composites, plume detections) activate as
          data-access tokens land. This build shows the infrastructure baseline.
        </div>
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
          Data: Geoscience Australia (CC BY 4.0) · Global Energy Monitor (CC BY 4.0) · Open
          Electricity (CC BY-NC 4.0) · Basemap © OpenStreetMap contributors via OpenFreeMap.
          {generatedAt ? ` Compiled ${generatedAt}.` : ""} Noncommercial public-good project ·{" "}
          <a href="https://github.com/samtomrob/methane-atlas" target="_blank" rel="noreferrer">
            source
          </a>
        </footer>
      </div>
    </>
  );
}
