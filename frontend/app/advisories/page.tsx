"use client";

import { ExternalLink, Languages } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { MapView } from "@/components/MapView";
import { PlaceAutocomplete } from "@/components/PlaceAutocomplete";
import { Badge, Button, Card, Skeleton, inputClass } from "@/components/ui";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { Advisory, Place } from "@/lib/types";
import { fmtIst } from "@/lib/utils";

const TYPES = [
  "",
  "closure",
  "general_advisory",
  "festival",
  "procession",
  "vip_movement",
  "protest",
  "sports_event",
  "roadwork",
  "construction",
  "waterlogging",
  "accident",
  "breakdown",
];

function AdvisoryCard({ a }: { a: Advisory }) {
  const { t, lang } = useI18n();
  const [linked, setLinked] = useState<{ title: string; text: string } | null>(null);
  const [showLinked, setShowLinked] = useState(false);
  const toggleLinked = async () => {
    if (!a.linked_document_id) return;
    if (!linked) {
      const d = await api<{ title: string; chunks: { text: string }[] }>(
        `/api/advisories/${a.linked_document_id}`,
      );
      setLinked({ title: d.title, text: d.chunks.map((c) => c.text).join("\n\n") });
    }
    setShowLinked((s) => !s);
  };
  const summary = lang === "hi" && a.summary_hi ? a.summary_hi : a.summary_en;
  return (
    <Card className="grid gap-3 p-4 sm:grid-cols-[1fr_200px]">
      <div className="min-w-0 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge
            tone={a.trust_level === "official" ? "primary" : a.trust_level === "sensor" ? "warn" : "muted"}
          >
            {a.trust_level}
          </Badge>
          {a.event_type && <Badge>{a.event_type.replace("_", " ")}</Badge>}
          {a.severity && (
            <Badge tone={a.severity === "high" ? "danger" : a.severity === "medium" ? "warn" : "muted"}>
              {a.severity}
            </Badge>
          )}
          {a.also_reported_by > 0 && (
            <span className="text-xs text-muted-foreground">
              {t("chat.also_reported", { n: a.also_reported_by })}
            </span>
          )}
        </div>
        <h2 className="font-semibold leading-snug">{showLinked && linked ? linked.title : a.title}</h2>
        <p className="text-sm text-muted-foreground">
          {t("adv.valid")}: {a.valid_from ? fmtIst(a.valid_from) : "—"} →{" "}
          {a.valid_to ? fmtIst(a.valid_to) : t("adv.until_further")}
          {a.published_at ? ` · ${a.authority ?? a.publisher ?? ""} ${fmtIst(a.published_at)}` : ""}
        </p>
        <p className="whitespace-pre-line text-sm">
          {showLinked && linked ? linked.text : summary || a.snippet}
        </p>
        <div className="flex flex-wrap gap-2">
          {a.url.startsWith("http") && (
            <a
              href={a.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-sm text-primary underline-offset-2 hover:underline"
            >
              <ExternalLink className="h-3.5 w-3.5" /> {t("adv.source_link")}
            </a>
          )}
          {a.linked_document_id && (
            <Button variant="ghost" size="sm" onClick={toggleLinked}>
              <Languages className="h-3.5 w-3.5" />{" "}
              {a.language === "hi" ? t("adv.show_english") : t("adv.show_hindi")}
            </Button>
          )}
        </div>
      </div>
      {a.geometry && (
        <div className="h-40 overflow-hidden rounded-md border border-border sm:h-auto">
          <MapView
            showLive={false}
            className="relative h-40 w-full sm:h-full"
            highlights={{
              type: "FeatureCollection",
              features: [
                { type: "Feature", geometry: a.geometry, properties: { kind: "advisory", label: a.title } },
              ],
            }}
          />
        </div>
      )}
    </Card>
  );
}

export default function AdvisoriesPage() {
  const { t } = useI18n();
  const [items, setItems] = useState<Advisory[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [type, setType] = useState("");
  const [source, setSource] = useState("");
  const [when, setWhen] = useState<"active" | "upcoming">("active");
  const [area, setArea] = useState<Place | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (p: number) => {
      setLoading(true);
      setError(null);
      const qs = new URLSearchParams({
        page: String(p),
        active: String(when === "active"),
        upcoming: String(when === "upcoming"),
      });
      if (type) qs.set("type", type);
      if (source) qs.set("source", source);
      if (area) qs.set("near", `${area.lat},${area.lon}`);
      try {
        const r = await api<{ items: Advisory[]; total: number }>(`/api/advisories?${qs}`);
        setItems((prev) => (p === 1 ? r.items : [...prev, ...r.items]));
        setTotal(r.total);
        setPage(p);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
    },
    [type, source, when, area],
  );

  useEffect(() => {
    void load(1);
  }, [load]);

  return (
    <div className="mx-auto w-full max-w-5xl space-y-4 px-4 py-6">
      <h1 className="text-2xl font-semibold">{t("adv.title")}</h1>
      <div className="grid gap-3 sm:grid-cols-4">
        <label className="text-sm">
          <span className="font-medium">{t("adv.filter.when")}</span>
          <select
            className={inputClass + " mt-1"}
            value={when}
            onChange={(e) => setWhen(e.target.value as "active" | "upcoming")}
          >
            <option value="active">{t("adv.active")}</option>
            <option value="upcoming">{t("adv.upcoming")}</option>
          </select>
        </label>
        <label className="text-sm">
          <span className="font-medium">{t("adv.filter.type")}</span>
          <select className={inputClass + " mt-1"} value={type} onChange={(e) => setType(e.target.value)}>
            {TYPES.map((x) => (
              <option key={x} value={x}>
                {x ? x.replace("_", " ") : "—"}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="font-medium">{t("adv.filter.source")}</span>
          <select className={inputClass + " mt-1"} value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">—</option>
            <option value="official">official</option>
            <option value="news">news</option>
            <option value="sensor">sensor</option>
          </select>
        </label>
        <PlaceAutocomplete label={t("adv.filter.area")} value={area} onSelect={setArea} />
      </div>
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      <div className="space-y-3">
        {items.map((a) => (
          <AdvisoryCard key={a.chunk_id} a={a} />
        ))}
        {loading && [0, 1, 2].map((i) => <Skeleton key={i} className="h-32 w-full" />)}
        {!loading && !items.length && !error && (
          <p className="text-sm text-muted-foreground">{t("adv.empty")}</p>
        )}
      </div>
      {!loading && items.length < total && (
        <Button variant="outline" onClick={() => load(page + 1)}>
          {t("adv.load_more")}
        </Button>
      )}
    </div>
  );
}
