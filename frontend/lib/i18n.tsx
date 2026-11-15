"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import en from "@/messages/en.json";
import hi from "@/messages/hi.json";

import type { Lang } from "./types";

type Dict = Record<string, string>;
const DICTS: Record<Lang, Dict> = { en, hi };

interface Ctx {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
  theme: "light" | "dark";
  toggleTheme: () => void;
}

const I18nContext = createContext<Ctx | null>(null);

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* private mode */
  }
}

export function translate(
  dict: Dict,
  fallback: Dict,
  key: string,
  vars?: Record<string, string | number>,
): string {
  let s = dict[key] ?? fallback[key] ?? key;
  for (const [k, v] of Object.entries(vars ?? {})) s = s.replace(`{${k}}`, String(v));
  return s;
}

export function AppProviders({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>("en");
  const [theme, setTheme] = useState<"light" | "dark">("light");

  useEffect(() => {
    const storedLang = readStorage("dtc-lang");
    if (storedLang === "hi" || storedLang === "en") setLangState(storedLang);
    const storedTheme = readStorage("dtc-theme");
    const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
    setTheme(storedTheme === "dark" || (!storedTheme && prefersDark) ? "dark" : "light");
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    document.documentElement.lang = lang;
  }, [theme, lang]);

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    writeStorage("dtc-lang", l);
  }, []);
  const toggleTheme = useCallback(() => {
    setTheme((prev) => {
      const next = prev === "dark" ? "light" : "dark";
      writeStorage("dtc-theme", next);
      return next;
    });
  }, []);
  const t = useCallback(
    (key: string, vars?: Record<string, string | number>) => translate(DICTS[lang], DICTS.en, key, vars),
    [lang],
  );
  const value = useMemo(
    () => ({ lang, setLang, t, theme, toggleTheme }),
    [lang, setLang, t, theme, toggleTheme],
  );
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): Ctx {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used inside AppProviders");
  return ctx;
}
