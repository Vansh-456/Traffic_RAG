"use client";

import {
  BadgeCheck,
  Database,
  Loader2,
  Microscope,
  RotateCcw,
  Send,
  Square,
  ThumbsDown,
  ThumbsUp,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { ChatMessage, FeatureCollection } from "@/lib/types";
import { useChat } from "@/lib/useChat";

import { AnswerText } from "./AnswerText";
import { Cards } from "./Cards";
import { Badge, Button, Skeleton } from "./ui";

const EXAMPLES = [
  "Any diversions near ITO today?",
  "Why is Ring Road slow right now?",
  "Kal Red Fort ke paas traffic kaisa rahega?",
  "Which roads are closed for the Republic Day rehearsal?",
  "Is 6 pm Friday a bad time to drive to IGI Airport?",
  "आज आईटीओ के पास कोई डायवर्जन है?",
];

function AssistantMessage({
  m,
  onFollowUp,
  onInternals,
  onRate,
}: {
  m: ChatMessage;
  onFollowUp: (q: string) => void;
  onInternals: () => void;
  onRate: (r: 1 | -1) => void;
}) {
  const { t } = useI18n();
  const [sent, setSent] = useState(false);
  const rate = async (r: 1 | -1) => {
    onRate(r);
    try {
      await api("/api/feedback", { method: "POST", body: JSON.stringify({ message_id: m.id, rating: r }) });
      setSent(true);
    } catch {
      /* ignore */
    }
  };
  return (
    <div className="space-y-2">
      {m.status && !m.content && (
        <div className="space-y-2" aria-live="polite">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> {m.status}
          </div>
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      )}
      {m.cards && <Cards cards={m.cards} />}
      {m.content && <AnswerText text={m.content} citations={m.citations} />}
      {m.status && m.content && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground" aria-live="polite">
          <Loader2 className="h-3 w-3 animate-spin" /> {m.status}
        </div>
      )}
      {m.error && (
        <div
          role="alert"
          className="flex items-center gap-2 rounded-md bg-danger/10 px-3 py-2 text-sm text-danger"
        >
          <TriangleAlert className="h-4 w-4" /> {m.error === "network" ? t("chat.error") : m.error}
        </div>
      )}
      {m.done && !m.error && (
        <>
          <div className="flex flex-wrap items-center gap-2 pt-1">
            {m.cached && (
              <Badge tone="muted">
                <Database className="h-3 w-3" /> {t("chat.cached")}
              </Badge>
            )}
            {m.verified ? (
              <Badge tone="ok">
                <BadgeCheck className="h-3 w-3" /> {t("chat.verified")}
              </Badge>
            ) : !m.abstained ? (
              <Badge tone="warn">{t("chat.unverified")}</Badge>
            ) : null}
            {m.revised && <Badge tone="primary">{t("chat.revised")}</Badge>}
            <Button variant="ghost" size="sm" onClick={onInternals} className="text-xs">
              <Microscope className="h-3.5 w-3.5" /> {t("chat.internals")}
            </Button>
            <span className="ml-auto flex items-center gap-1">
              {sent ? (
                <span className="text-xs text-muted-foreground">{t("chat.thanks")}</span>
              ) : (
                <>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={t("chat.helpful")}
                    aria-pressed={m.rating === 1}
                    onClick={() => rate(1)}
                  >
                    <ThumbsUp className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={t("chat.not_helpful")}
                    aria-pressed={m.rating === -1}
                    onClick={() => rate(-1)}
                  >
                    <ThumbsDown className="h-4 w-4" />
                  </Button>
                </>
              )}
            </span>
          </div>
          {m.followUps && m.followUps.length > 0 && (
            <div className="flex flex-wrap gap-2" aria-label={t("chat.followups")}>
              {m.followUps.map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => onFollowUp(q)}
                  className="rounded-full border border-border px-3 py-1 text-xs hover:bg-muted focus:outline-none focus:ring-2 focus:ring-primary"
                >
                  {q}
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export function ChatPanel({ onHighlights }: { onHighlights: (fc: FeatureCollection | null) => void }) {
  const { t } = useI18n();
  const [adminToken, setAdminToken] = useState<string | null>(null);
  useEffect(() => {
    try {
      setAdminToken(window.localStorage.getItem("dtc-admin-token"));
    } catch {
      /* ignore */
    }
  }, []);
  const { messages, busy, send, stop, reset, rate } = useChat(adminToken);
  const [input, setInput] = useState("");
  const [drawer, setDrawer] = useState<string | null>(null);
  const [features, setFeatures] = useState<Record<string, boolean>>({});
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<{ features: Record<string, boolean> }>("/health")
      .then((h) => setFeatures(h.features))
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
    const last = [...messages].reverse().find((m) => m.role === "assistant" && m.mapFeatures);
    onHighlights(last?.mapFeatures ?? null);
  }, [messages, onHighlights]);

  const submit = (q: string) => {
    void send(q);
    setInput("");
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      {(features.llm === false || features.tomtom === false) && (
        <div className="border-b border-border bg-warn/10 px-4 py-2 text-xs text-warn" role="status">
          {features.llm === false && <div>{t("notice.llm_disabled")}</div>}
          {features.tomtom === false && <div>{t("notice.tomtom_disabled")}</div>}
        </div>
      )}
      <div ref={scrollRef} className="flex-1 space-y-5 overflow-y-auto px-4 py-4" aria-live="polite">
        {messages.length === 0 ? (
          <div className="mx-auto max-w-xl space-y-4 pt-6 text-center">
            <h1 className="text-2xl font-semibold">{t("chat.empty.title")}</h1>
            <p className="text-sm text-muted-foreground">{t("chat.empty.subtitle")}</p>
            <p className="pt-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {t("chat.examples")}
            </p>
            <div className="flex flex-wrap justify-center gap-2">
              {EXAMPLES.map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => submit(q)}
                  className="rounded-full border border-border bg-card px-3 py-1.5 text-sm hover:bg-muted focus:outline-none focus:ring-2 focus:ring-primary"
                >
                  {q}
                </button>
              ))}
            </div>
            <p className="pt-4 text-xs text-muted-foreground">{t("notice.not_official")}</p>
          </div>
        ) : (
          messages.map((m) =>
            m.role === "user" ? (
              <div
                key={m.id}
                className="ml-auto w-fit max-w-[85%] rounded-lg bg-primary px-3.5 py-2 text-sm text-primary-foreground"
              >
                {m.content}
              </div>
            ) : (
              <div key={m.id} className="max-w-full rounded-lg border border-border bg-card px-4 py-3">
                <AssistantMessage
                  m={m}
                  onFollowUp={submit}
                  onInternals={() => setDrawer(m.id)}
                  onRate={(r) => rate(m.id, r)}
                />
              </div>
            ),
          )
        )}
      </div>
      <form
        className="flex items-end gap-2 border-t border-border bg-background p-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit(input);
        }}
      >
        {messages.length > 0 && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={reset}
            aria-label={t("chat.new")}
            title={t("chat.new")}
          >
            <RotateCcw className="h-4 w-4" />
          </Button>
        )}
        <label htmlFor="chat-input" className="sr-only">
          {t("chat.placeholder")}
        </label>
        <textarea
          id="chat-input"
          value={input}
          maxLength={1000}
          rows={1}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit(input);
            }
          }}
          placeholder={t("chat.placeholder")}
          className="max-h-32 min-h-10 flex-1 resize-none rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
        />
        {busy ? (
          <Button type="button" variant="outline" onClick={stop} aria-label={t("chat.stop")}>
            <Square className="h-4 w-4" />
          </Button>
        ) : (
          <Button type="submit" disabled={!input.trim()} aria-label={t("chat.send")}>
            <Send className="h-4 w-4" />
          </Button>
        )}
      </form>
    </div>
  );
}
