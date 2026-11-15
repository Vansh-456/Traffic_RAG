"use client";

import { useI18n } from "@/lib/i18n";

const STEPS = [
  [
    "Ingest",
    "Delhi Traffic Police advisories (RSS, listings, node pages, PDFs with OCR), Google News RSS, TomTom flow & incidents, Open-Meteo weather, OpenStreetMap gazetteer.",
  ],
  [
    "Process",
    "Structure-aware chunking with contextual headers, LLM metadata extraction (event type, places, validity window), geocoding, near-duplicate clustering, embeddings + full-text indexes.",
  ],
  [
    "Understand",
    "Detect language and intent, resolve places and the IST time window, rewrite the query, split compound questions.",
  ],
  [
    "Route",
    "An agent picks tools: advisory search, live traffic, incidents, weather, route check, typical conditions, incident history.",
  ],
  [
    "Retrieve",
    "Hybrid search: dense vectors + hypothetical-question index + Postgres full-text + trigram names, fused with RRF, reranked, then scored by freshness × trust × proximity.",
  ],
  [
    "Answer",
    "A grounded, streamed answer with numbered citations, then a claim-by-claim verification pass that removes or hedges unsupported claims.",
  ],
];

export default function AboutPage() {
  const { t } = useI18n();
  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-8">
      <h1 className="text-2xl font-semibold">{t("about.title")}</h1>
      <p className="rounded-md border border-warn/40 bg-warn/10 p-3 text-sm">{t("notice.not_official")}</p>
      <section aria-labelledby="how">
        <h2 id="how" className="mb-3 text-lg font-semibold">
          How it works
        </h2>
        <ol className="relative space-y-4 border-l-2 border-primary/30 pl-6">
          {STEPS.map(([title, body], i) => (
            <li key={title} className="relative">
              <span className="absolute -left-[33px] grid h-6 w-6 place-items-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">
                {i + 1}
              </span>
              <h3 className="font-medium">{title}</h3>
              <p className="text-sm text-muted-foreground">{body}</p>
            </li>
          ))}
        </ol>
        <p className="mt-3 text-sm">
          Open “How this answer was made” under any answer to see every stage with real scores.
        </p>
      </section>
      <section aria-labelledby="sources" className="space-y-2 text-sm">
        <h2 id="sources" className="text-lg font-semibold">
          Data sources & attribution
        </h2>
        <ul className="list-disc space-y-1 pl-5">
          <li>
            Delhi Traffic Police — traffic.delhipolice.gov.in (official advisories; always linked to the
            original).
          </li>
          <li>
            News via Google News RSS and publisher feeds — headlines and summaries, full text only where
            robots.txt allows.
          </li>
          <li>Live traffic flow and incidents © TomTom.</li>
          <li>Weather — Open-Meteo.com (CC BY 4.0).</li>
          <li>Map data © OpenStreetMap contributors (ODbL).</li>
        </ul>
        <p className="text-muted-foreground">
          Answers can be incomplete or out of date. For emergencies and the latest instructions use the Delhi
          Traffic Police helpline 1095 / 011-25844444 or @dtptraffic.
        </p>
      </section>
    </div>
  );
}
