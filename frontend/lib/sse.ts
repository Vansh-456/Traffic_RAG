import { API_URL } from "./api";

export interface SseEvent {
  event: string;
  data: unknown;
}

/** Parse a chunk of an SSE byte stream; returns complete events and the leftover buffer. */
export function parseSse(buffer: string): { events: SseEvent[]; rest: string } {
  const events: SseEvent[] = [];
  const normalized = buffer.replace(/\r\n/g, "\n");
  const blocks = normalized.split("\n\n");
  const rest = blocks.pop() ?? "";
  for (const block of blocks) {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith(":")) continue; // comment / ping
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
    }
    if (!dataLines.length) continue;
    const raw = dataLines.join("\n");
    let data: unknown = raw;
    try {
      data = JSON.parse(raw);
    } catch {
      /* keep string */
    }
    events.push({ event, data });
  }
  return { events, rest };
}

/** POST /api/chat and invoke `onEvent` for each SSE event (fetch-based: EventSource can't POST). */
export async function streamChat(
  body: { message: string; conversation_id?: string | null },
  onEvent: (e: SseEvent) => void,
  signal?: AbortSignal,
  adminToken?: string | null,
): Promise<void> {
  const headers: Record<string, string> = { "content-type": "application/json", accept: "text/event-stream" };
  if (adminToken) headers["x-admin-token"] = adminToken;
  const res = await fetch(`${API_URL}/api/chat`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    credentials: "include",
    signal,
  });
  if (!res.ok || !res.body) {
    let msg = `Request failed (${res.status})`;
    try {
      const j = (await res.json()) as { detail?: string };
      if (j.detail) msg = j.detail;
    } catch {
      /* ignore */
    }
    onEvent({ event: "error", data: { message: msg } });
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = parseSse(buffer);
    buffer = rest;
    events.forEach(onEvent);
  }
  if (buffer.trim()) parseSse(buffer + "\n\n").events.forEach(onEvent);
}
