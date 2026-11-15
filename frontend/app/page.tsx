"use client";

import { useCallback, useState } from "react";

import { ChatPanel } from "@/components/ChatPanel";
import { MapView } from "@/components/MapView";
import type { FeatureCollection } from "@/lib/types";

export default function HomePage() {
  const [highlights, setHighlights] = useState<FeatureCollection | null>(null);
  const onHighlights = useCallback((fc: FeatureCollection | null) => setHighlights(fc), []);
  return (
    <div className="mx-auto grid h-[calc(100vh-3.5rem)] w-full max-w-[1600px] grid-rows-[minmax(0,1fr)_40vh] lg:grid-cols-[minmax(380px,1fr)_minmax(0,1.1fr)] lg:grid-rows-1">
      <section aria-label="Chat" className="row-start-1 min-h-0 border-border lg:border-r">
        <ChatPanel onHighlights={onHighlights} />
      </section>
      <section
        aria-label="Map"
        className="relative row-start-2 min-h-0 border-t border-border lg:row-start-1 lg:border-t-0"
      >
        <MapView highlights={highlights} />
      </section>
    </div>
  );
}
