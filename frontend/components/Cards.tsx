"use client";

import { AlertTriangle, CloudRain, Gauge } from "lucide-react";

import { useI18n } from "@/lib/i18n";
import type { Card as CardT } from "@/lib/types";
import { LEVEL_COLORS } from "@/lib/utils";

export function Cards({ cards }: { cards: CardT[] }) {
  const { t } = useI18n();
  if (!cards.length) return null;
  return (
    <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-3" aria-label="Current conditions">
      {cards.map((c, i) => (
        <div key={i} className="rounded-md border border-border bg-background p-3 text-sm">
          {c.type === "live_traffic" && (
            <>
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Gauge className="h-3.5 w-3.5" /> {t("cards.live")} · [{c.citation}]
              </div>
              <div className="mt-1 font-medium leading-tight">{c.title}</div>
              <div className="mt-1 flex items-baseline gap-1">
                <span className="text-xl font-semibold" style={{ color: LEVEL_COLORS[c.level ?? "unknown"] }}>
                  {c.current_speed}
                </span>
                <span className="text-xs text-muted-foreground">
                  / {c.free_flow_speed} km/h {t("cards.free_flow")}
                </span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded bg-muted" aria-hidden>
                <div
                  className="h-full"
                  style={{
                    width: `${Math.min(100, ((c.current_speed ?? 0) / (c.free_flow_speed || 1)) * 100)}%`,
                    background: LEVEL_COLORS[c.level ?? "unknown"],
                  }}
                />
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                {t(`level.${c.level ?? "unknown"}`)}
                {c.trend ? ` · ${c.trend}` : ""} · {c.as_of}
              </div>
            </>
          )}
          {c.type === "weather" && (
            <>
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <CloudRain className="h-3.5 w-3.5" /> {c.title} · [{c.citation}]
              </div>
              <div className="mt-1 text-xl font-semibold">
                {c.temperature_c != null ? `${Math.round(c.temperature_c)}°C` : "—"}
              </div>
              <div className="text-xs capitalize">{c.conditions}</div>
              <div className="mt-1 text-xs text-muted-foreground">
                {c.rain_mm ? `${c.rain_mm} mm rain · ` : ""}
                {c.visibility_m != null ? `visibility ${Math.round(c.visibility_m / 100) / 10} km · ` : ""}
                {c.as_of}
              </div>
              {c.flags?.length ? (
                <div className="mt-1 text-xs font-medium text-warn">
                  {c.flags.join(", ").replaceAll("_", " ")}
                </div>
              ) : null}
            </>
          )}
          {c.type === "incident" && (
            <>
              <div className="flex items-center gap-1.5 text-xs text-danger">
                <AlertTriangle className="h-3.5 w-3.5" /> {c.title} · [{c.citation}]
              </div>
              <div className="mt-1 line-clamp-3 leading-snug">{c.description}</div>
              <div className="mt-1 text-xs text-muted-foreground">
                {c.delay_min ? `+${c.delay_min} min · ` : ""}
                {c.as_of}
              </div>
            </>
          )}
        </div>
      ))}
    </div>
  );
}
