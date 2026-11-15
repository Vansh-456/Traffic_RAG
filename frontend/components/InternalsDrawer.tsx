"use client";

import { CheckCircle2, XCircle } from "lucide-react";
import type { ReactNode } from "react";

import type { Internals } from "@/lib/types";
import { cn, fmtIst } from "@/lib/utils";

import { MapView } from "./MapView";
import { Badge, Sheet } from "./ui";

function Section({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <section className="border-b border-border py-4 last:border-0" aria-labelledby={`int-${n}`}>
      <h3 id={`int-${n}`} className="mb-2 flex items-center gap-2 text-sm font-semibold">
        <span className="grid h-5 w-5 place-items-center rounded-full bg-primary text-[11px] text-primary-foreground">
          {n}
        </span>
        {title}
      </h3>
      <div className="space-y-2 text-sm">{children}</div>
    </section>
  );
}

function KV({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex gap-2">
      <span className="w-36 shrink-0 text-muted-foreground">{k}</span>
      <span className="min-w-0 flex-1 break-words">{v}</span>
    </div>
  );
}

const num = (x: number | null | undefined, d = 3) => (x == null ? "—" : x.toFixed(d));

export function InternalsDrawer({
  open,
  onOpenChange,
  internals,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  internals?: Internals;
}) {
  const i = internals ?? {};
  const qu = i.query_understanding;
  const r = i.retrieval;
  const v = i.verification;
  const lat = i.latency_ms ?? {};
  return (
    <Sheet open={open} onOpenChange={onOpenChange} title="RAG internals">
      {!internals ? (
        <p className="text-sm text-muted-foreground">Internals will appear when the answer finishes.</p>
      ) : (
        <div>
          <Section n={1} title="Semantic cache">
            <KV
              k="Result"
              v={<Badge tone={i.cache?.hit ? "ok" : "muted"}>{i.cache?.hit ? "hit" : "miss"}</Badge>}
            />
            <KV k="Bucket" v={<code className="text-xs">{i.cache?.bucket}</code>} />
            <KV
              k="Best similarity"
              v={`${num(i.cache?.best_similarity)} (threshold ${i.cache?.threshold})`}
            />
          </Section>

          <Section n={2} title="Query understanding">
            {qu ? (
              <>
                <KV k="Method" v={qu.method} />
                <KV k="Intent" v={<Badge tone="primary">{qu.intent}</Badge>} />
                <KV k="Language" v={qu.language} />
                <KV k="Standalone query" v={qu.standalone} />
                <KV
                  k="Time window"
                  v={`${qu.time_window.label}: ${fmtIst(qu.time_window.start)} → ${fmtIst(qu.time_window.end)}${qu.time_window.is_live ? " (live)" : ""}`}
                />
                <KV
                  k="Rewrites"
                  v={
                    <ul className="list-disc pl-4">
                      {qu.rewritten_queries.map((q) => (
                        <li key={q}>{q}</li>
                      ))}
                    </ul>
                  }
                />
                {qu.sub_questions.length > 0 && (
                  <KV
                    k="Sub-questions"
                    v={
                      <ul className="list-disc pl-4">
                        {qu.sub_questions.map((q) => (
                          <li key={q}>{q}</li>
                        ))}
                      </ul>
                    }
                  />
                )}
                <KV
                  k="Locations"
                  v={
                    qu.locations.length
                      ? qu.locations
                          .map(
                            (l) =>
                              `${l.name} ← "${l.mention}" (${l.method}, ${num(l.confidence, 2)}, r=${l.radius_m} m)`,
                          )
                          .join("; ")
                      : "none resolved"
                  }
                />
                {qu.locations.length > 0 && (
                  <div className="h-48 overflow-hidden rounded-md border border-border">
                    <MapView
                      showLive={false}
                      className="relative h-48 w-full"
                      highlights={{
                        type: "FeatureCollection",
                        features: qu.locations.map((l) => ({
                          type: "Feature",
                          geometry: { type: "Point", coordinates: [l.lon, l.lat] },
                          properties: { kind: "query_location", label: l.name },
                        })),
                      }}
                    />
                  </div>
                )}
              </>
            ) : (
              <p className="text-muted-foreground">Not run.</p>
            )}
          </Section>

          <Section n={3} title="Tools called">
            {i.tools?.length ? (
              <table className="w-full text-xs">
                <thead className="text-left text-muted-foreground">
                  <tr>
                    <th className="py-1">Tool</th>
                    <th>Origin</th>
                    <th>Args</th>
                    <th className="text-right">ms</th>
                  </tr>
                </thead>
                <tbody>
                  {i.tools.map((t) => (
                    <tr key={t.name} className="border-t border-border align-top">
                      <td className="py-1 font-medium">
                        {t.ok ? "✓" : "✗"} {t.name}
                        <div className="font-normal text-muted-foreground">{t.summary}</div>
                      </td>
                      <td>{t.origin}</td>
                      <td>
                        <code>{JSON.stringify(t.args)}</code>
                      </td>
                      <td className="text-right">{Math.round(t.latency_ms)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="text-muted-foreground">No tools.</p>
            )}
            {i.llm_selected_tools && (
              <p className="text-xs text-muted-foreground">
                LLM chose: {i.llm_selected_tools.join(", ") || "none (rules applied)"}
              </p>
            )}
          </Section>

          <Section n={4} title="Retrieval">
            {r ? (
              <>
                <KV k="Filters" v={<code className="text-xs">{JSON.stringify(r.filters)}</code>} />
                <KV
                  k="Lists"
                  v={Object.entries(r.list_sizes)
                    .map(([k, n]) => `${k}: ${n}`)
                    .join(" · ")}
                />
                <KV
                  k="Fused / reranked"
                  v={`${r.fused} → ${r.reranked} (reranker ${r.reranker}, embedder ${r.embedder})`}
                />
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead className="text-left text-muted-foreground">
                      <tr>
                        <th className="py-1">Chunk</th>
                        <th>Ranks</th>
                        <th>RRF</th>
                        <th>Rerank</th>
                        <th>Fresh</th>
                        <th>Trust</th>
                        <th>Prox</th>
                        <th>Final</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[
                        ...r.candidates.map((c) => ({ ...c, kept: true })),
                        ...r.dropped.map((c) => ({ ...c, kept: false })),
                      ].map((c) => (
                        <tr
                          key={c.chunk_id}
                          className={cn("border-t border-border align-top", !c.kept && "opacity-50")}
                        >
                          <td className="max-w-[14rem] py-1">
                            <div className="truncate font-medium" title={c.title}>
                              #{c.chunk_id} {c.title}
                            </div>
                            <div className="text-muted-foreground">
                              {c.source} · {c.trust_level}
                              {c.kept ? "" : " · dropped"}
                            </div>
                            {c.matched_question && (
                              <div className="text-muted-foreground">Q: {c.matched_question}</div>
                            )}
                          </td>
                          <td className="whitespace-nowrap">
                            {Object.entries(c.ranks)
                              .map(([k, v]) => `${k}:${v}`)
                              .join(" ")}
                          </td>
                          <td>{num(c.rrf, 4)}</td>
                          <td>{num(c.rerank)}</td>
                          <td>{num(c.freshness)}</td>
                          <td>{c.trust}</td>
                          <td>{num(c.proximity)}</td>
                          <td className="font-semibold">{num(c.final)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <KV
                  k="Stage timings"
                  v={Object.entries(r.timings_ms)
                    .map(([k, ms]) => `${k} ${ms} ms`)
                    .join(" · ")}
                />
              </>
            ) : (
              <p className="text-muted-foreground">No retrieval for this answer.</p>
            )}
            {i.relevance_gate && (
              <KV
                k="Relevance gate"
                v={`best ${num(i.relevance_gate.best_score)} vs threshold ${i.relevance_gate.threshold}; tool data: ${i.relevance_gate.tool_data ? "yes" : "no"} → ${i.relevance_gate.abstain ? "ABSTAIN" : "answer"}`}
              />
            )}
          </Section>

          <Section n={5} title="Context passed to the LLM">
            <KV k="Evidence items" v={i.context?.evidence_items ?? "—"} />
            <KV k="Approx. tokens" v={i.context?.approx_tokens ?? "—"} />
            <KV k="Generated by" v={i.generation?.by ?? "—"} />
          </Section>

          <Section n={6} title="Verification">
            {v ? (
              <>
                <KV
                  k="Method / faithfulness"
                  v={`${v.method} · ${num(v.faithfulness, 2)}${i.regenerated ? " · regenerated once (strict)" : ""}`}
                />
                <ul className="space-y-1">
                  {v.claims.map((c) => (
                    <li key={c.index} className="flex gap-2 text-xs">
                      {c.supported ? (
                        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ok" />
                      ) : (
                        <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" />
                      )}
                      <span>
                        {c.sentence}{" "}
                        <span className="text-muted-foreground">
                          [{c.citations.join(",") || "no cite"}] {c.reason}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <p className="text-muted-foreground">Not run.</p>
            )}
          </Section>

          <Section n={7} title="Latency & cost">
            <KV k="Total / first token" v={`${lat.total ?? "—"} ms / ${lat.first_token ?? "—"} ms`} />
            <KV
              k="Stages"
              v={Object.entries(lat)
                .filter(([k]) => !["total", "first_token"].includes(k))
                .map(([k, ms]) => `${k} ${ms} ms`)
                .join(" · ")}
            />
            <KV
              k="Tokens / cost"
              v={`${i.usage?.total_tokens ?? 0} tokens · $${(i.usage?.cost_usd ?? 0).toFixed(5)}`}
            />
            {i.usage?.calls.length ? (
              <ul className="text-xs text-muted-foreground">
                {i.usage.calls.map((c, idx) => (
                  <li key={idx}>
                    {c.purpose}: {c.model} · {c.prompt_tokens}+{c.completion_tokens} tok ·{" "}
                    {Math.round(c.latency_ms)} ms
                  </li>
                ))}
              </ul>
            ) : null}
          </Section>

          <Section n={8} title="Trace">
            {i.langfuse_trace_url ? (
              <a
                className="text-primary underline"
                href={i.langfuse_trace_url}
                target="_blank"
                rel="noopener noreferrer"
              >
                Open Langfuse trace
              </a>
            ) : (
              <p className="text-muted-foreground">
                Langfuse link is shown to admins when LANGFUSE_* keys are configured.
              </p>
            )}
          </Section>
        </div>
      )}
    </Sheet>
  );
}
