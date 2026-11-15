"use client";

import { useCallback, useRef, useState } from "react";

import { streamChat, type SseEvent } from "./sse";
import type { Card, ChatMessage, Citation, FeatureCollection, Internals } from "./types";
import { uid } from "./utils";

export function applyEvent(msg: ChatMessage, e: SseEvent): ChatMessage {
  const d = e.data as Record<string, unknown>;
  switch (e.event) {
    case "meta":
      return { ...msg, id: String(d.message_id ?? msg.id) };
    case "status":
      return { ...msg, status: String(d.message ?? "") };
    case "token":
      return { ...msg, content: msg.content + String(d.text ?? "") };
    case "revision":
      return { ...msg, content: String(d.text ?? msg.content), revised: true };
    case "citations":
      return { ...msg, citations: e.data as Citation[] };
    case "cards":
      return { ...msg, cards: e.data as Card[] };
    case "map_features":
      return { ...msg, mapFeatures: e.data as FeatureCollection };
    case "internals":
      return { ...msg, internals: e.data as Internals, cached: Boolean((e.data as Internals).cache?.hit) };
    case "done":
      return {
        ...msg,
        done: true,
        status: undefined,
        verified: Boolean(d.verified),
        abstained: Boolean(d.abstained),
        followUps: (d.follow_ups as string[] | undefined) ?? msg.followUps,
      };
    case "error":
      return { ...msg, done: true, status: undefined, error: String(d.message ?? "error") };
    default:
      return msg;
  }
}

export function useChat(adminToken?: string | null) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const conversationId = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(
    async (text: string) => {
      const q = text.trim();
      if (!q || busy) return;
      const userMsg: ChatMessage = { id: uid(), role: "user", content: q };
      const tempId = uid();
      setMessages((m) => [...m, userMsg, { id: tempId, role: "assistant", content: "", status: "…" }]);
      setBusy(true);
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      let currentId = tempId;
      try {
        await streamChat(
          { message: q, conversation_id: conversationId.current },
          (e) => {
            if (e.event === "meta")
              conversationId.current = String((e.data as Record<string, unknown>).conversation_id);
            setMessages((all) =>
              all.map((m) => {
                if (m.id !== currentId) return m;
                const next = applyEvent(m, e);
                currentId = next.id;
                return next;
              }),
            );
          },
          ctrl.signal,
          adminToken,
        );
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          setMessages((all) =>
            all.map((m) =>
              m.id === currentId ? { ...m, done: true, status: undefined, error: "network" } : m,
            ),
          );
        }
      } finally {
        setMessages((all) =>
          all.map((m) => (m.id === currentId && !m.done ? { ...m, done: true, status: undefined } : m)),
        );
        setBusy(false);
      }
    },
    [busy, adminToken],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);
  const reset = useCallback(() => {
    abortRef.current?.abort();
    conversationId.current = null;
    setMessages([]);
  }, []);
  const rate = useCallback(
    (id: string, rating: 1 | -1) =>
      setMessages((all) => all.map((m) => (m.id === id ? { ...m, rating } : m))),
    [],
  );

  return { messages, busy, send, stop, reset, rate };
}
