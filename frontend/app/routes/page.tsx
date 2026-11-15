"use client";

import { Bell, Loader2, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { MapView } from "@/components/MapView";
import { PlaceAutocomplete } from "@/components/PlaceAutocomplete";
import { Badge, Button, Card, Field, inputClass } from "@/components/ui";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { Place, SavedRoute } from "@/lib/types";
import { fmtIst } from "@/lib/utils";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

interface RouteStatus {
  advisories: { title: string; severity: string | null }[];
  incidents: { description: string; delay_min: number | null }[];
  segments: { name: string; level: string; current_speed_kmph: number }[];
  weather_flags: string[];
  depart: string;
}

function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

export default function RoutesPage() {
  const { t } = useI18n();
  const [routes, setRoutes] = useState<SavedRoute[]>([]);
  const [alerts, setAlerts] = useState<
    { id: number; sent_at: string; payload: { title: string; body: string }; route: string }[]
  >([]);
  const [status, setStatus] = useState<Record<number, RouteStatus | "loading">>({});
  const [name, setName] = useState("Home → Office");
  const [origin, setOrigin] = useState<Place | null>(null);
  const [dest, setDest] = useState<Place | null>(null);
  const [depart, setDepart] = useState("09:00");
  const [digest, setDigest] = useState("");
  const [days, setDays] = useState([0, 1, 2, 3, 4]);
  const [channels, setChannels] = useState<string[]>(["inapp"]);
  const [push, setPush] = useState<{ enabled: boolean; key: string | null }>({ enabled: false, key: null });
  const [msg, setMsg] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setRoutes(await api<SavedRoute[]>("/api/routes"));
      setAlerts(await api("/api/alerts"));
    } catch (e) {
      setMsg((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void refresh();
    api<{ enabled: boolean; key: string | null }>("/api/push/vapid-public-key")
      .then(setPush)
      .catch(() => undefined);
    const params = new URLSearchParams(window.location.search);
    const token = params.get("verify");
    const email = params.get("email");
    if (token && email) {
      api("/api/auth/verify", { method: "POST", body: JSON.stringify({ token, email }) })
        .then(() => setMsg(`Email ${email} verified`))
        .catch((e: Error) => setMsg(e.message));
    }
  }, [refresh]);

  const enablePush = async () => {
    if (!push.key || !("serviceWorker" in navigator)) return;
    const reg = await navigator.serviceWorker.register("/sw.js");
    const perm = await Notification.requestPermission();
    if (perm !== "granted") return;
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(push.key),
    });
    await api("/api/push/subscribe", { method: "POST", body: JSON.stringify(sub.toJSON()) });
    setChannels((c) => [...new Set([...c, "push"])]);
    setMsg("Push notifications enabled");
  };

  const save = async () => {
    if (!origin || !dest) return;
    try {
      await api("/api/routes", {
        method: "POST",
        body: JSON.stringify({
          name,
          origin: { label: origin.name, lat: origin.lat, lon: origin.lon },
          destination: { label: dest.name, lat: dest.lat, lon: dest.lon },
          depart_time: depart || null,
          digest_time: digest || null,
          days_of_week: days,
          channels,
        }),
      });
      setOrigin(null);
      setDest(null);
      await refresh();
    } catch (e) {
      setMsg((e as Error).message);
    }
  };

  const check = async (id: number) => {
    setStatus((s) => ({ ...s, [id]: "loading" }));
    try {
      const st = await api<RouteStatus>(`/api/routes/${id}/status`);
      setStatus((s) => ({ ...s, [id]: st }));
    } catch (e) {
      setMsg((e as Error).message);
    }
  };

  const onMapClick = (lat: number, lon: number) => {
    const p: Place = {
      id: null,
      name: `${lat.toFixed(4)}, ${lon.toFixed(4)}`,
      name_hi: null,
      type: "point",
      lat,
      lon,
    };
    if (!origin) setOrigin(p);
    else if (!dest) setDest(p);
  };

  return (
    <div className="mx-auto grid w-full max-w-6xl gap-6 px-4 py-6 lg:grid-cols-[1fr_1fr]">
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold">{t("routes.title")}</h1>
        {msg && (
          <p role="status" className="rounded-md bg-muted px-3 py-2 text-sm">
            {msg}
          </p>
        )}
        <Card className="space-y-3 p-4">
          <h2 className="font-semibold">{t("routes.new")}</h2>
          <Field label={t("routes.name")}>
            <input
              className={inputClass}
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={200}
            />
          </Field>
          <PlaceAutocomplete label={t("routes.origin")} value={origin} onSelect={setOrigin} />
          <PlaceAutocomplete label={t("routes.destination")} value={dest} onSelect={setDest} />
          <p className="text-xs text-muted-foreground">{t("routes.pick_on_map")}</p>
          <div className="grid grid-cols-2 gap-3">
            <Field label={t("routes.depart")}>
              <input
                type="time"
                className={inputClass}
                value={depart}
                onChange={(e) => setDepart(e.target.value)}
              />
            </Field>
            <Field label={t("routes.digest")}>
              <input
                type="time"
                className={inputClass}
                value={digest}
                onChange={(e) => setDigest(e.target.value)}
              />
            </Field>
          </div>
          <fieldset>
            <legend className="text-sm font-medium">{t("routes.days")}</legend>
            <div className="mt-1 flex flex-wrap gap-1">
              {DAYS.map((d, i) => (
                <label
                  key={d}
                  className={`cursor-pointer rounded-md border px-2 py-1 text-xs ${days.includes(i) ? "border-primary bg-primary/10" : "border-border"}`}
                >
                  <input
                    type="checkbox"
                    className="sr-only"
                    checked={days.includes(i)}
                    onChange={(e) =>
                      setDays((ds) => (e.target.checked ? [...ds, i].sort() : ds.filter((x) => x !== i)))
                    }
                  />
                  {d}
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset>
            <legend className="text-sm font-medium">{t("routes.channels")}</legend>
            <div className="mt-1 flex flex-wrap items-center gap-3 text-sm">
              {["inapp", "push", "email"].map((c) => (
                <label key={c} className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={channels.includes(c)}
                    disabled={c === "push" && !push.enabled}
                    onChange={(e) =>
                      setChannels((cs) => (e.target.checked ? [...cs, c] : cs.filter((x) => x !== c)))
                    }
                  />
                  {c}
                </label>
              ))}
              {push.enabled ? (
                <Button variant="outline" size="sm" onClick={enablePush}>
                  <Bell className="h-3.5 w-3.5" /> {t("routes.push_enable")}
                </Button>
              ) : (
                <span className="text-xs text-muted-foreground">{t("routes.push_disabled")}</span>
              )}
            </div>
          </fieldset>
          <Button onClick={save} disabled={!origin || !dest || !name.trim()}>
            {t("routes.save")}
          </Button>
        </Card>

        <div className="space-y-3">
          {routes.length === 0 && <p className="text-sm text-muted-foreground">{t("routes.empty")}</p>}
          {routes.map((r) => {
            const st = status[r.id];
            return (
              <Card key={r.id} className="space-y-2 p-4">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="font-semibold">{r.name}</div>
                    <div className="text-sm text-muted-foreground">
                      {r.origin.label} → {r.destination.label} · {r.depart_time ?? "any time"} ·{" "}
                      {r.days_of_week.map((d) => DAYS[d]).join(" ")}
                    </div>
                    <div className="mt-1 flex gap-1">
                      {r.channels.map((c) => (
                        <Badge key={c} tone="muted">
                          {c}
                        </Badge>
                      ))}
                    </div>
                  </div>
                  <div className="flex gap-1">
                    <Button variant="outline" size="sm" onClick={() => check(r.id)}>
                      {st === "loading" ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        t("routes.status")
                      )}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={t("routes.delete")}
                      onClick={async () => {
                        await api(`/api/routes/${r.id}`, { method: "DELETE" });
                        await refresh();
                      }}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
                {st && st !== "loading" && (
                  <div className="space-y-1 rounded-md bg-muted/60 p-3 text-sm">
                    <div>
                      Advisories:{" "}
                      {st.advisories.length ? st.advisories.map((a) => a.title).join("; ") : "none"}
                    </div>
                    <div>
                      Incidents:{" "}
                      {st.incidents.length
                        ? st.incidents
                            .map((i) => `${i.description}${i.delay_min ? ` (+${i.delay_min} min)` : ""}`)
                            .join("; ")
                        : "none"}
                    </div>
                    <div>
                      Congestion:{" "}
                      {st.segments.length
                        ? st.segments.map((s) => `${s.name}: ${t(`level.${s.level}`)}`).join("; ")
                        : "no live data"}
                    </div>
                    {st.weather_flags.length > 0 && <div>Weather: {st.weather_flags.join(", ")}</div>}
                  </div>
                )}
              </Card>
            );
          })}
        </div>

        <div>
          <h2 className="mb-2 font-semibold">{t("routes.alerts")}</h2>
          <ul className="space-y-2">
            {alerts.map((a) => (
              <li key={a.id} className="rounded-md border border-border p-3 text-sm">
                <div className="font-medium">{a.payload.title}</div>
                <div className="whitespace-pre-line text-muted-foreground">{a.payload.body}</div>
                <div className="text-xs text-muted-foreground">
                  {a.route} · {fmtIst(a.sent_at)}
                </div>
              </li>
            ))}
            {!alerts.length && <li className="text-sm text-muted-foreground">—</li>}
          </ul>
        </div>
      </div>
      <div className="h-[60vh] overflow-hidden rounded-lg border border-border lg:sticky lg:top-20">
        <MapView
          onMapClick={onMapClick}
          picks={[
            ...(origin ? [{ lat: origin.lat, lon: origin.lon, label: origin.name }] : []),
            ...(dest ? [{ lat: dest.lat, lon: dest.lon, label: dest.name }] : []),
          ]}
        />
      </div>
    </div>
  );
}
