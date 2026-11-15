"use client";

import type { GeoJSONSource, Map as MlMap, MapMouseEvent, StyleSpecification } from "maplibre-gl";
import { Layers } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { FeatureCollection, LiveMap } from "@/lib/types";
import { fmtIst, LEVEL_COLORS, SEVERITY_COLORS } from "@/lib/utils";

const DELHI: [number, number] = [77.209, 28.6139];
const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] };
const TOMTOM_KEY = process.env.NEXT_PUBLIC_TOMTOM_TILES_KEY;

function baseStyle(): StyleSpecification {
  return {
    version: 8,
    sources: {
      osm: {
        type: "raster",
        tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        tileSize: 256,
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxzoom: 19,
      },
      ...(TOMTOM_KEY
        ? {
            tomtom: {
              type: "raster" as const,
              tiles: [
                `https://api.tomtom.com/traffic/map/4/tile/flow/relative0/{z}/{x}/{y}.png?key=${TOMTOM_KEY}`,
              ],
              tileSize: 256,
              attribution: "Traffic © TomTom",
            },
          }
        : {}),
    },
    layers: [
      { id: "osm", type: "raster", source: "osm" },
      ...(TOMTOM_KEY
        ? [
            {
              id: "tomtom",
              type: "raster" as const,
              source: "tomtom",
              layout: { visibility: "none" as const },
              paint: { "raster-opacity": 0.8 },
            },
          ]
        : []),
    ],
  };
}

type LayerKey = "segments" | "incidents" | "advisories" | "answer" | "tomtom";

interface Props {
  highlights?: FeatureCollection | null;
  picks?: { lat: number; lon: number; label: string }[];
  onMapClick?: (lat: number, lon: number) => void;
  showLive?: boolean;
  className?: string;
}

export function MapView({ highlights, picks, onMapClick, showLive = true, className }: Props) {
  const { t } = useI18n();
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MlMap | null>(null);
  const [ready, setReady] = useState(false);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [visible, setVisible] = useState<Record<LayerKey, boolean>>({
    segments: true,
    incidents: true,
    advisories: true,
    answer: true,
    tomtom: false,
  });
  const [panelOpen, setPanelOpen] = useState(false);
  const clickRef = useRef(onMapClick);
  clickRef.current = onMapClick;

  useEffect(() => {
    let disposed = false;
    void import("maplibre-gl").then(({ default: maplibregl }) => {
      if (disposed || !container.current) return;
      const map = new maplibregl.Map({
        container: container.current,
        style: baseStyle(),
        center: DELHI,
        zoom: 10.5,
        attributionControl: { compact: true },
      });
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
      map.on("load", () => {
        for (const id of ["segments", "incidents", "advisories", "answer", "picks"])
          map.addSource(id, { type: "geojson", data: EMPTY as never });
        map.addLayer({
          id: "advisories-fill",
          type: "fill",
          source: "advisories",
          filter: ["==", ["geometry-type"], "Polygon"],
          paint: {
            "fill-color": [
              "match",
              ["get", "severity"],
              "high",
              SEVERITY_COLORS.high!,
              "medium",
              SEVERITY_COLORS.medium!,
              SEVERITY_COLORS.low!,
            ],
            "fill-opacity": 0.15,
          },
        });
        map.addLayer({
          id: "advisories-line",
          type: "line",
          source: "advisories",
          filter: ["!=", ["geometry-type"], "Point"],
          paint: { "line-color": SEVERITY_COLORS.medium!, "line-width": 2, "line-dasharray": [2, 2] },
        });
        map.addLayer({
          id: "advisories-pt",
          type: "circle",
          source: "advisories",
          filter: ["==", ["geometry-type"], "Point"],
          paint: {
            "circle-radius": 6,
            "circle-color": [
              "match",
              ["get", "severity"],
              "high",
              SEVERITY_COLORS.high!,
              "medium",
              SEVERITY_COLORS.medium!,
              SEVERITY_COLORS.low!,
            ],
            "circle-stroke-color": "#fff",
            "circle-stroke-width": 1.5,
            "circle-opacity": 0.85,
          },
        });
        const levelColor = [
          "match",
          ["get", "level"],
          ...Object.entries(LEVEL_COLORS).flat(),
          "#94a3b8",
        ] as unknown as string;
        map.addLayer({
          id: "segments-line",
          type: "line",
          source: "segments",
          filter: ["!=", ["geometry-type"], "Point"],
          paint: { "line-color": levelColor, "line-width": 5, "line-opacity": 0.9 },
          layout: { "line-cap": "round" },
        });
        map.addLayer({
          id: "segments-pt",
          type: "circle",
          source: "segments",
          filter: ["==", ["geometry-type"], "Point"],
          paint: {
            "circle-radius": 5,
            "circle-color": levelColor,
            "circle-stroke-color": "#fff",
            "circle-stroke-width": 1,
          },
        });
        map.addLayer({
          id: "incidents-line",
          type: "line",
          source: "incidents",
          filter: ["!=", ["geometry-type"], "Point"],
          paint: { "line-color": "#dc2626", "line-width": 4, "line-dasharray": [1, 1] },
        });
        map.addLayer({
          id: "incidents-pt",
          type: "circle",
          source: "incidents",
          filter: ["==", ["geometry-type"], "Point"],
          paint: {
            "circle-radius": 7,
            "circle-color": "#dc2626",
            "circle-stroke-color": "#fff",
            "circle-stroke-width": 2,
          },
        });
        map.addLayer({
          id: "answer-fill",
          type: "fill",
          source: "answer",
          filter: ["==", ["geometry-type"], "Polygon"],
          paint: { "fill-color": "#2563eb", "fill-opacity": 0.18 },
        });
        map.addLayer({
          id: "answer-line",
          type: "line",
          source: "answer",
          filter: [
            "in",
            ["geometry-type"],
            ["literal", ["LineString", "MultiLineString", "Polygon", "MultiPolygon"]],
          ],
          paint: {
            "line-color": ["match", ["get", "kind"], "route", "#7c3aed", "segment", levelColor, "#2563eb"],
            "line-width": 4,
          },
        });
        map.addLayer({
          id: "answer-pt",
          type: "circle",
          source: "answer",
          filter: ["==", ["geometry-type"], "Point"],
          paint: {
            "circle-radius": ["match", ["get", "kind"], "query_location", 9, 6],
            "circle-color": [
              "match",
              ["get", "kind"],
              "query_location",
              "#2563eb",
              "incident",
              "#dc2626",
              "#f59e0b",
            ],
            "circle-stroke-color": "#fff",
            "circle-stroke-width": 2,
          },
        });
        map.addLayer({
          id: "picks-pt",
          type: "circle",
          source: "picks",
          paint: {
            "circle-radius": 8,
            "circle-color": "#7c3aed",
            "circle-stroke-color": "#fff",
            "circle-stroke-width": 2,
          },
        });

        const popupLayers = [
          "advisories-pt",
          "advisories-fill",
          "segments-line",
          "segments-pt",
          "incidents-pt",
          "incidents-line",
          "answer-pt",
          "answer-line",
        ];
        for (const layer of popupLayers) {
          map.on("click", layer, (e) => {
            const p = (e.features?.[0]?.properties ?? {}) as Record<string, unknown>;
            const label = String(p.label ?? p.title ?? p.name ?? p.description ?? "");
            const extra = p.speed
              ? ` — ${String(p.speed)}/${String(p.free_flow)} km/h`
              : p.delay_min
                ? ` — delay ${String(p.delay_min)} min`
                : "";
            const el = document.createElement("div");
            el.textContent = label + extra;
            new maplibregl.Popup({ closeButton: true }).setLngLat(e.lngLat).setDOMContent(el).addTo(map);
          });
          map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
          map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
        }
        map.on("click", (e: MapMouseEvent) => clickRef.current?.(e.lngLat.lat, e.lngLat.lng));
        mapRef.current = map;
        setReady(true);
      });
    });
    return () => {
      disposed = true;
      mapRef.current?.remove();
      mapRef.current = null;
    };
  }, []);

  // live layers, refreshed every 2 minutes
  useEffect(() => {
    if (!ready || !showLive) return;
    let cancelled = false;
    const load = async () => {
      try {
        const live = await api<LiveMap>("/api/map/live");
        const map = mapRef.current;
        if (cancelled || !map) return;
        (map.getSource("segments") as GeoJSONSource).setData(live.segments as never);
        (map.getSource("incidents") as GeoJSONSource).setData(live.incidents as never);
        (map.getSource("advisories") as GeoJSONSource).setData(live.advisories as never);
        setAsOf(live.as_of);
      } catch {
        /* backend unavailable; keep map usable */
      }
    };
    void load();
    const id = window.setInterval(load, 120_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [ready, showLive]);

  // answer highlights + auto-fit
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const data = highlights ?? EMPTY;
    (map.getSource("answer") as GeoJSONSource).setData(data as never);
    const coords: [number, number][] = [];
    const walk = (c: unknown) => {
      if (Array.isArray(c) && typeof c[0] === "number") coords.push([c[0] as number, c[1] as number]);
      else if (Array.isArray(c)) c.forEach(walk);
    };
    data.features.forEach((f) => walk(f.geometry.coordinates));
    if (coords.length) {
      void import("maplibre-gl").then(({ LngLatBounds }) => {
        const b = coords.reduce((acc, c) => acc.extend(c), new LngLatBounds(coords[0]!, coords[0]!));
        map.fitBounds(b, { padding: 60, maxZoom: 14, duration: 800 });
      });
    }
  }, [highlights, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    (map.getSource("picks") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: (picks ?? []).map((p) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [p.lon, p.lat] },
        properties: { label: p.label },
      })),
    } as never);
  }, [picks, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const groups: Record<LayerKey, string[]> = {
      segments: ["segments-line", "segments-pt"],
      incidents: ["incidents-line", "incidents-pt"],
      advisories: ["advisories-fill", "advisories-line", "advisories-pt"],
      answer: ["answer-fill", "answer-line", "answer-pt"],
      tomtom: TOMTOM_KEY ? ["tomtom"] : [],
    };
    (Object.keys(groups) as LayerKey[]).forEach((k) =>
      groups[k].forEach(
        (id) => map.getLayer(id) && map.setLayoutProperty(id, "visibility", visible[k] ? "visible" : "none"),
      ),
    );
  }, [visible, ready]);

  const toggles: LayerKey[] = [
    "answer",
    "segments",
    "incidents",
    "advisories",
    ...(TOMTOM_KEY ? (["tomtom"] as LayerKey[]) : []),
  ];

  return (
    <div className={className ?? "relative h-full min-h-[320px] w-full"}>
      <div ref={container} className="absolute inset-0" role="region" aria-label="Map of Delhi traffic" />
      <div className="absolute left-3 top-3 z-10">
        <button
          type="button"
          onClick={() => setPanelOpen((o) => !o)}
          aria-expanded={panelOpen}
          className="flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1.5 text-xs font-medium shadow"
        >
          <Layers className="h-3.5 w-3.5" /> {t("map.layers")}
        </button>
        {panelOpen && (
          <fieldset className="mt-2 space-y-1.5 rounded-md border border-border bg-card p-3 text-xs shadow">
            <legend className="sr-only">{t("map.layers")}</legend>
            {toggles.map((k) => (
              <label key={k} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={visible[k]}
                  onChange={(e) => setVisible((v) => ({ ...v, [k]: e.target.checked }))}
                />
                {t(`map.${k}`)}
              </label>
            ))}
          </fieldset>
        )}
      </div>
      {asOf && (
        <div className="absolute bottom-8 left-3 z-10 flex items-center gap-2 rounded-md bg-card/90 px-2 py-1 text-[11px] shadow">
          {(["free", "moderate", "heavy", "severe"] as const).map((l) => (
            <span key={l} className="flex items-center gap-1">
              <span className="h-2 w-3 rounded-sm" style={{ background: LEVEL_COLORS[l] }} />
              {t(`level.${l}`)}
            </span>
          ))}
          <span className="text-muted-foreground">
            · {t("map.as_of")} {fmtIst(asOf, false)}
          </span>
        </div>
      )}
    </div>
  );
}
