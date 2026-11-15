"use client";

import * as Tabs from "@radix-ui/react-tabs";
import { CheckCircle2, CircleAlert, CircleX } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge, Button, Card, inputClass } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtIst } from "@/lib/utils";

interface Overview {
  sources: {
    id: string;
    name: string;
    kind: string;
    enabled: boolean;
    last_run_at: string | null;
    last_status: string | null;
    last_error: string | null;
    documents: number;
    errors_24h: number;
  }[];
  counts: Record<string, number>;
  ingestion_volume: { day: string; source_id: string; n: number }[];
  latency: {
    p50: number | null;
    p95: number | null;
    ft_p50: number | null;
    ft_p95: number | null;
    cache_rate: number | null;
    n: number;
  };
  cache: { process_hits: number; process_misses: number; hit_rate_7d: number };
  tomtom_budget: { used?: number; cap?: number; remaining?: number; date?: string; error?: string };
}
interface EvalRun {
  id: number;
  started_at: string;
  label: string;
  metrics: Record<string, number>;
  n_questions: number;
  git_sha: string | null;
}
interface Worst {
  id: string;
  question: string | null;
  answer: string;
  intent: string;
  faithfulness: number | null;
  rating: number | null;
  comment: string | null;
  in_review_queue: boolean;
  created_at: string;
}
interface EvalQ {
  id: string;
  question: string;
  expected_answer: string | null;
  intent: string;
  language: string;
  status: string;
  origin: string;
  unanswerable: boolean;
}

function StatusIcon({ s }: { s: string | null }) {
  if (s === "ok")
    return (
      <span className="inline-flex items-center gap-1 text-ok">
        <CheckCircle2 className="h-4 w-4" /> ok
      </span>
    );
  if (s === "error")
    return (
      <span className="inline-flex items-center gap-1 text-danger">
        <CircleX className="h-4 w-4" /> error
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1 text-warn">
      <CircleAlert className="h-4 w-4" /> {s ?? "never run"}
    </span>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card className="p-4">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
    </Card>
  );
}

/** Single-series daily bar chart with hover tooltips; a table view sits below. */
function VolumeChart({ rows }: { rows: { day: string; n: number }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...rows.map((r) => r.n));
  const W = 560,
    H = 160,
    pad = 24;
  const bw = rows.length ? (W - pad) / rows.length : 0;
  return (
    <figure className="relative">
      <svg
        viewBox={`0 0 ${W} ${H + 20}`}
        className="w-full"
        role="img"
        aria-label="Documents ingested per day"
      >
        <line x1={pad} x2={W} y1={H} y2={H} className="stroke-border" />
        <text x={pad - 4} y={10} textAnchor="end" className="fill-muted-foreground text-[10px]">
          {max}
        </text>
        {rows.map((r, i) => {
          const h = (r.n / max) * (H - 12);
          return (
            <g key={r.day} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={pad + i * bw} y={0} width={bw} height={H} fill="transparent" />
              <rect
                x={pad + i * bw + 2}
                y={H - h}
                width={Math.max(2, bw - 4)}
                height={h}
                rx={4}
                className={hover === i ? "fill-primary" : "fill-primary/70"}
              />
              {(i === 0 || i === rows.length - 1) && (
                <text
                  x={pad + i * bw + bw / 2}
                  y={H + 14}
                  textAnchor="middle"
                  className="fill-muted-foreground text-[10px]"
                >
                  {r.day.slice(5)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {hover !== null && rows[hover] && (
        <div
          className="pointer-events-none absolute top-0 rounded-md border border-border bg-card px-2 py-1 text-xs shadow"
          style={{ left: `${((pad + hover * bw) / W) * 100}%` }}
        >
          {rows[hover].day}: <strong>{rows[hover].n}</strong> docs
        </div>
      )}
    </figure>
  );
}

export default function AdminPage() {
  const [token, setToken] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [ov, setOv] = useState<Overview | null>(null);
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [worst, setWorst] = useState<Worst[]>([]);
  const [qs, setQs] = useState<EvalQ[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    try {
      setSaved(window.localStorage.getItem("dtc-admin-token"));
    } catch {
      /* ignore */
    }
  }, []);

  const load = useCallback(async (tk: string) => {
    setErr(null);
    try {
      const [o, r, w, q] = await Promise.all([
        api<Overview>("/api/admin/overview", { admin: tk }),
        api<EvalRun[]>("/api/admin/eval/runs", { admin: tk }),
        api<Worst[]>("/api/admin/answers/worst", { admin: tk }),
        api<EvalQ[]>("/api/admin/eval/questions?status=draft", { admin: tk }),
      ]);
      setOv(o);
      setRuns(r);
      setWorst(w);
      setQs(q);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, []);

  useEffect(() => {
    if (saved) void load(saved);
  }, [saved, load]);

  const volume = useMemo(() => {
    const byDay = new Map<string, number>();
    ov?.ingestion_volume.forEach((v) => byDay.set(v.day, (byDay.get(v.day) ?? 0) + v.n));
    return [...byDay.entries()].sort().map(([day, n]) => ({ day, n }));
  }, [ov]);

  if (!saved) {
    return (
      <div className="mx-auto w-full max-w-sm space-y-3 px-4 py-12">
        <h1 className="text-xl font-semibold">Admin</h1>
        <label className="block text-sm">
          Admin token
          <input
            type="password"
            className={inputClass + " mt-1"}
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
        </label>
        <Button
          onClick={() => {
            try {
              window.localStorage.setItem("dtc-admin-token", token);
            } catch {
              /* ignore */
            }
            setSaved(token);
          }}
          disabled={!token}
        >
          Sign in
        </Button>
      </div>
    );
  }

  const b = ov?.tomtom_budget;
  const latest = runs[0]?.metrics;
  return (
    <div className="mx-auto w-full max-w-6xl space-y-4 px-4 py-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Admin dashboard</h1>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => saved && load(saved)}>
            Refresh
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              try {
                window.localStorage.removeItem("dtc-admin-token");
              } catch {
                /* ignore */
              }
              setSaved(null);
            }}
          >
            Sign out
          </Button>
        </div>
      </div>
      {err && (
        <p role="alert" className="text-sm text-danger">
          {err}
        </p>
      )}
      <Tabs.Root defaultValue="health">
        <Tabs.List className="flex gap-1 border-b border-border" aria-label="Admin sections">
          {["health", "eval", "answers", "review"].map((v) => (
            <Tabs.Trigger
              key={v}
              value={v}
              className="border-b-2 border-transparent px-3 py-2 text-sm capitalize data-[state=active]:border-primary data-[state=active]:font-medium"
            >
              {v}
            </Tabs.Trigger>
          ))}
        </Tabs.List>

        <Tabs.Content value="health" className="space-y-4 pt-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label="Documents / chunks"
              value={`${ov?.counts.documents ?? "—"} / ${ov?.counts.chunks ?? "—"}`}
              sub={`${ov?.counts.active_chunks ?? 0} active · ${ov?.counts.places ?? 0} places`}
            />
            <Stat
              label="Latency p50 / p95"
              value={`${Math.round(ov?.latency.p50 ?? 0)} / ${Math.round(ov?.latency.p95 ?? 0)} ms`}
              sub={`first token p95 ${Math.round(ov?.latency.ft_p95 ?? 0)} ms · ${ov?.latency.n ?? 0} answers (7d)`}
            />
            <Stat
              label="Cache hit rate (7d)"
              value={`${Math.round((ov?.cache.hit_rate_7d ?? 0) * 100)}%`}
              sub={`process: ${ov?.cache.process_hits ?? 0} hits / ${ov?.cache.process_misses ?? 0} misses`}
            />
            <Card className="p-4">
              <div className="text-xs text-muted-foreground">TomTom budget today</div>
              <div className="mt-1 text-2xl font-semibold tabular-nums">
                {b?.used ?? 0}{" "}
                <span className="text-sm font-normal text-muted-foreground">/ {b?.cap ?? "—"}</span>
              </div>
              <div
                className="mt-2 h-2 overflow-hidden rounded bg-muted"
                role="meter"
                aria-valuemin={0}
                aria-valuemax={b?.cap ?? 0}
                aria-valuenow={b?.used ?? 0}
                aria-label="TomTom requests used"
              >
                <div
                  className="h-full rounded bg-primary"
                  style={{ width: `${Math.min(100, ((b?.used ?? 0) / (b?.cap || 1)) * 100)}%` }}
                />
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                {b?.remaining ?? "—"} remaining · {b?.date}
              </div>
            </Card>
          </div>
          <Card className="p-4">
            <h2 className="mb-2 text-sm font-semibold">Ingestion volume (documents/day, 14 days)</h2>
            {volume.length ? (
              <VolumeChart rows={volume} />
            ) : (
              <p className="text-sm text-muted-foreground">No documents yet.</p>
            )}
            <details className="mt-2 text-xs">
              <summary className="cursor-pointer text-muted-foreground">Table view</summary>
              <table className="mt-1">
                <tbody>
                  {ov?.ingestion_volume.map((v) => (
                    <tr key={v.day + v.source_id}>
                      <td className="pr-3">{v.day}</td>
                      <td className="pr-3">{v.source_id}</td>
                      <td>{v.n}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          </Card>
          <Card className="overflow-x-auto p-0">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">Source</th>
                  <th>Status</th>
                  <th>Last run</th>
                  <th>Docs</th>
                  <th>Errors 24h</th>
                  <th>Enabled</th>
                </tr>
              </thead>
              <tbody>
                {ov?.sources.map((s) => (
                  <tr key={s.id} className="border-t border-border align-top">
                    <td className="px-3 py-2">
                      <div className="font-medium">{s.name}</div>
                      <div className="text-xs text-muted-foreground">{s.id}</div>
                      {s.last_error && (
                        <div className="max-w-md truncate text-xs text-danger" title={s.last_error}>
                          {s.last_error}
                        </div>
                      )}
                    </td>
                    <td>
                      <StatusIcon s={s.last_status} />
                    </td>
                    <td className="text-xs">{s.last_run_at ? fmtIst(s.last_run_at) : "—"}</td>
                    <td className="tabular-nums">{s.documents}</td>
                    <td className="tabular-nums">{s.errors_24h}</td>
                    <td>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={async () => {
                          await api(`/api/admin/sources/${s.id}/toggle`, { method: "POST", admin: saved });
                          void load(saved);
                        }}
                      >
                        {s.enabled ? "on" : "off"}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </Tabs.Content>

        <Tabs.Content value="eval" className="space-y-4 pt-4">
          {latest && (
            <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
              {(
                [
                  "recall@10",
                  "mrr",
                  "faithfulness",
                  "citation_accuracy",
                  "abstention_accuracy",
                  "language_match",
                ] as const
              ).map((k) => (
                <Stat key={k} label={k} value={(latest[k] ?? 0).toFixed(3)} sub={runs[0]?.label} />
              ))}
            </div>
          )}
          <Card className="overflow-x-auto p-0">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">Run</th>
                  <th>When</th>
                  <th>n</th>
                  <th>Recall@10</th>
                  <th>MRR</th>
                  <th>nDCG@10</th>
                  <th>Faithful</th>
                  <th>Abstain acc.</th>
                  <th>p95 ms</th>
                  <th>git</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id} className="border-t border-border tabular-nums">
                    <td className="px-3 py-2">{r.label}</td>
                    <td className="text-xs">{fmtIst(r.started_at)}</td>
                    <td>{r.n_questions}</td>
                    <td>{r.metrics["recall@10"]?.toFixed(3)}</td>
                    <td>{r.metrics.mrr?.toFixed(3)}</td>
                    <td>{r.metrics["ndcg@10"]?.toFixed(3)}</td>
                    <td>{r.metrics.faithfulness?.toFixed(3)}</td>
                    <td>{r.metrics.abstention_accuracy?.toFixed(3)}</td>
                    <td>{Math.round(r.metrics.latency_p95_ms ?? 0)}</td>
                    <td className="text-xs">{r.git_sha}</td>
                  </tr>
                ))}
                {!runs.length && (
                  <tr>
                    <td className="px-3 py-3 text-muted-foreground" colSpan={10}>
                      No eval runs yet — run `copilot eval run`.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </Card>
        </Tabs.Content>

        <Tabs.Content value="answers" className="space-y-3 pt-4">
          {worst.map((w) => (
            <Card key={w.id} className="space-y-1 p-4 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="muted">{w.intent}</Badge>
                {w.faithfulness != null && (
                  <Badge tone={w.faithfulness < 0.7 ? "danger" : "ok"}>
                    faithfulness {w.faithfulness.toFixed(2)}
                  </Badge>
                )}
                {w.rating === -1 && <Badge tone="danger">👎 {w.comment}</Badge>}
                {w.rating === 1 && <Badge tone="ok">👍</Badge>}
                <span className="text-xs text-muted-foreground">{fmtIst(w.created_at)}</span>
              </div>
              <div className="font-medium">Q: {w.question}</div>
              <div className="whitespace-pre-line text-muted-foreground">{w.answer}</div>
            </Card>
          ))}
          {!worst.length && <p className="text-sm text-muted-foreground">No scored answers yet.</p>}
        </Tabs.Content>

        <Tabs.Content value="review" className="space-y-3 pt-4">
          <p className="text-sm text-muted-foreground">
            Draft eval questions (from `copilot eval generate-questions` or promoted feedback). Approve, then
            `copilot eval export`.
          </p>
          {qs.map((q) => (
            <Card key={q.id} className="flex items-start justify-between gap-3 p-4 text-sm">
              <div>
                <div className="font-medium">{q.question}</div>
                <div className="text-muted-foreground">{q.expected_answer}</div>
                <div className="mt-1 flex gap-1">
                  <Badge tone="muted">{q.intent}</Badge>
                  <Badge tone="muted">{q.language}</Badge>
                  <Badge tone="muted">{q.origin}</Badge>
                </div>
              </div>
              <div className="flex shrink-0 gap-1">
                {(["approved", "rejected"] as const).map((s) => (
                  <Button
                    key={s}
                    variant={s === "approved" ? "default" : "outline"}
                    size="sm"
                    onClick={async () => {
                      await api(`/api/admin/eval/questions/${q.id}`, {
                        method: "PATCH",
                        admin: saved,
                        body: JSON.stringify({ status: s }),
                      });
                      setQs((all) => all.filter((x) => x.id !== q.id));
                    }}
                  >
                    {s === "approved" ? "Approve" : "Reject"}
                  </Button>
                ))}
              </div>
            </Card>
          ))}
          {!qs.length && <p className="text-sm text-muted-foreground">Nothing to review.</p>}
        </Tabs.Content>
      </Tabs.Root>
    </div>
  );
}
