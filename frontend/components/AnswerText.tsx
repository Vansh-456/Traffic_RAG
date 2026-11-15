"use client";

import { ExternalLink } from "lucide-react";
import { Fragment, type ReactNode } from "react";

import { useI18n } from "@/lib/i18n";
import type { Citation } from "@/lib/types";
import { fmtIst, splitCitations } from "@/lib/utils";

import { Tooltip } from "./ui";

function CitationChip({ id, citation }: { id: number; citation?: Citation }) {
  const { t } = useI18n();
  const chip = (
    <sup>
      <a
        href={citation?.url ?? "#"}
        target={citation?.url ? "_blank" : undefined}
        rel="noopener noreferrer"
        onClick={(e) => !citation?.url && e.preventDefault()}
        className="mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded bg-primary/15 px-1 text-[10px] font-semibold text-primary no-underline hover:bg-primary/25 focus:outline-none focus:ring-2 focus:ring-primary"
        aria-label={`Source ${id}${citation ? `: ${citation.title}` : ""}`}
      >
        {id}
      </a>
    </sup>
  );
  if (!citation) return chip;
  return (
    <Tooltip
      content={
        <div className="space-y-1">
          <div className="font-medium leading-snug">{citation.title}</div>
          <div className="text-xs text-muted-foreground">
            {citation.source}
            {citation.published_at ? ` · ${fmtIst(citation.published_at)}` : ""}
            {citation.also_reported_by > 0
              ? ` · ${t("chat.also_reported", { n: citation.also_reported_by })}`
              : ""}
          </div>
          <p className="line-clamp-4 text-xs">{citation.snippet.replace(/^\[[^\]]*\]\s*/, "")}</p>
          {citation.url && (
            <span className="inline-flex items-center gap-1 text-xs text-primary">
              <ExternalLink className="h-3 w-3" /> {new URL(citation.url).hostname}
            </span>
          )}
        </div>
      }
    >
      {chip}
    </Tooltip>
  );
}

function inline(text: string, citations: Map<number, Citation>, keyBase: string): ReactNode[] {
  const parts: ReactNode[] = [];
  splitCitations(text).forEach((seg, i) => {
    if (seg.type === "cite") {
      seg.ids.forEach((id) =>
        parts.push(<CitationChip key={`${keyBase}-c${i}-${id}`} id={id} citation={citations.get(id)} />),
      );
      return;
    }
    // **bold**
    seg.value.split(/(\*\*[^*]+\*\*)/g).forEach((chunk, j) => {
      if (chunk.startsWith("**") && chunk.endsWith("**"))
        parts.push(<strong key={`${keyBase}-b${i}-${j}`}>{chunk.slice(2, -2)}</strong>);
      else if (chunk) parts.push(<Fragment key={`${keyBase}-t${i}-${j}`}>{chunk}</Fragment>);
    });
  });
  return parts;
}

/** Minimal streaming-safe markdown: paragraphs, bullet/numbered lists, bold, citation chips. */
export function AnswerText({ text, citations = [] }: { text: string; citations?: Citation[] }) {
  const byId = new Map(citations.map((c) => [c.id, c]));
  const blocks: ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  const flush = () => {
    if (!list) return;
    const Tag = list.ordered ? "ol" : "ul";
    blocks.push(
      <Tag key={`l${blocks.length}`}>
        {list.items.map((it, i) => (
          <li key={i}>{inline(it, byId, `l${blocks.length}-${i}`)}</li>
        ))}
      </Tag>,
    );
    list = null;
  };
  text.split("\n").forEach((raw) => {
    const line = raw.trim();
    const bullet = line.match(/^[-*•]\s+(.*)$/);
    const numbered = line.match(/^\d+[.)]\s+(.*)$/);
    if (bullet || numbered) {
      const ordered = Boolean(numbered);
      if (!list || list.ordered !== ordered) {
        flush();
        list = { ordered, items: [] };
      }
      list.items.push((bullet ?? numbered)![1] ?? "");
      return;
    }
    flush();
    if (!line) return;
    const heading = line.match(/^#{1,4}\s+(.*)$/);
    blocks.push(
      heading ? (
        <p key={`h${blocks.length}`} className="font-semibold">
          {inline(heading[1] ?? "", byId, `h${blocks.length}`)}
        </p>
      ) : (
        <p key={`p${blocks.length}`}>{inline(line, byId, `p${blocks.length}`)}</p>
      ),
    );
  });
  flush();
  return <div className="prose-answer text-[15px]">{blocks}</div>;
}
