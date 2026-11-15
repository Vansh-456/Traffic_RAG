"use client";

import { Moon, Navigation, Sun } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

import { Button } from "./ui";

const NAV = [
  { href: "/", key: "nav.home" },
  { href: "/advisories", key: "nav.advisories" },
  { href: "/routes", key: "nav.routes" },
  { href: "/about", key: "nav.about" },
  { href: "/admin", key: "nav.admin" },
];

export function Header() {
  const { t, lang, setLang, theme, toggleTheme } = useI18n();
  const path = usePathname();
  return (
    <header className="sticky top-0 z-30 border-b border-border bg-background/90 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-[1600px] items-center gap-3 px-4">
        <Link href="/" className="flex items-center gap-2 font-semibold">
          <span className="grid h-8 w-8 place-items-center rounded-md bg-primary text-primary-foreground">
            <Navigation className="h-4 w-4" aria-hidden />
          </span>
          <span className="hidden sm:inline">{t("app.title")}</span>
        </Link>
        <nav aria-label="Main" className="ml-2 flex flex-1 items-center gap-1 overflow-x-auto">
          {NAV.map((n) => (
            <Link
              key={n.href}
              href={n.href}
              aria-current={path === n.href ? "page" : undefined}
              className={cn(
                "whitespace-nowrap rounded-md px-2.5 py-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground",
                path === n.href && "bg-muted font-medium text-foreground",
              )}
            >
              {t(n.key)}
            </Link>
          ))}
        </nav>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setLang(lang === "en" ? "hi" : "en")}
          aria-label="Switch language"
        >
          {t("lang.toggle")}
        </Button>
        <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label={t("theme.toggle")}>
          {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </Button>
      </div>
    </header>
  );
}
