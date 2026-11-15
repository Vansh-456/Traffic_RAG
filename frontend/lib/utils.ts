import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

const IST = "Asia/Kolkata";

export function fmtIst(iso: string | null | undefined, withDate = true): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-IN", {
    timeZone: IST,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    ...(withDate ? { day: "numeric", month: "short" } : {}),
  });
}

export const LEVEL_COLORS: Record<string, string> = {
  free: "#16a34a",
  moderate: "#f59e0b",
  heavy: "#ea580c",
  severe: "#dc2626",
  closed: "#7f1d1d",
  unknown: "#94a3b8",
};

export const SEVERITY_COLORS: Record<string, string> = { high: "#dc2626", medium: "#f59e0b", low: "#2563eb" };

/** Split answer text into plain segments and citation markers like [1] or [2, 3]. */
export function splitCitations(
  text: string,
): ({ type: "text"; value: string } | { type: "cite"; ids: number[] })[] {
  const out: ({ type: "text"; value: string } | { type: "cite"; ids: number[] })[] = [];
  const re = /\[(\d+(?:\s*,\s*\d+)*)\]/g;
  let last = 0;
  for (const m of text.matchAll(re)) {
    const idx = m.index ?? 0;
    if (idx > last) out.push({ type: "text", value: text.slice(last, idx) });
    out.push({ type: "cite", ids: (m[1] ?? "").split(/\s*,\s*/).map(Number) });
    last = idx + m[0].length;
  }
  if (last < text.length) out.push({ type: "text", value: text.slice(last) });
  return out;
}

export function uid(): string {
  return Math.random().toString(36).slice(2, 10);
}
