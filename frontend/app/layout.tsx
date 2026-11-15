import "maplibre-gl/dist/maplibre-gl.css";
import "./globals.css";

import type { Metadata, Viewport } from "next";
import { Noto_Sans, Noto_Sans_Devanagari } from "next/font/google";
import type { ReactNode } from "react";

import { Header } from "@/components/Header";
import { ServiceWorker } from "@/components/ServiceWorker";
import { AppProviders } from "@/lib/i18n";

const sans = Noto_Sans({ subsets: ["latin"], variable: "--font-latin", display: "swap" });
const deva = Noto_Sans_Devanagari({
  subsets: ["devanagari"],
  variable: "--font-deva",
  weight: ["400", "500", "600"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Delhi Traffic Copilot",
  description:
    "Grounded, cited answers about Delhi traffic diversions, closures, incidents and live congestion.",
  manifest: "/manifest.json",
  icons: { icon: "/icon.svg", apple: "/icon.svg" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#1d4ed8" },
    { media: "(prefers-color-scheme: dark)", color: "#0b1220" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${sans.variable} ${deva.variable}`}>
      <body
        style={{ ["--font-sans" as string]: "var(--font-latin), var(--font-deva)" }}
        className="flex min-h-screen flex-col"
      >
        <AppProviders>
          <a
            href="#main"
            className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-primary focus:px-3 focus:py-2 focus:text-primary-foreground"
          >
            Skip to content
          </a>
          <Header />
          <main id="main" className="flex flex-1 flex-col">
            {children}
          </main>
          <ServiceWorker />
        </AppProviders>
      </body>
    </html>
  );
}
