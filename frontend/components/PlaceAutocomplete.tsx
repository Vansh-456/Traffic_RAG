"use client";

import { useEffect, useId, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { Place } from "@/lib/types";
import { inputClass } from "./ui";

export function PlaceAutocomplete({
  label,
  value,
  onSelect,
}: {
  label: string;
  value: Place | null;
  onSelect: (p: Place | null) => void;
}) {
  const [q, setQ] = useState(value?.name ?? "");
  const [items, setItems] = useState<Place[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => setQ(value?.name ?? ""), [value]);

  useEffect(() => {
    window.clearTimeout(timer.current);
    if (q.trim().length < 2 || q === value?.name) {
      setItems([]);
      return;
    }
    timer.current = window.setTimeout(() => {
      api<Place[]>(`/api/places/search?q=${encodeURIComponent(q.trim())}`)
        .then((r) => {
          setItems(r);
          setActive(0);
          setOpen(true);
        })
        .catch(() => setItems([]));
    }, 200);
  }, [q, value]);

  const choose = (p: Place) => {
    onSelect(p);
    setQ(p.name);
    setOpen(false);
  };

  return (
    <div className="relative">
      <label className="block text-sm font-medium" htmlFor={listId + "-input"}>
        {label}
      </label>
      <input
        id={listId + "-input"}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        className={inputClass + " mt-1"}
        value={q}
        placeholder="ITO, AIIMS, लाल किला…"
        onChange={(e) => {
          setQ(e.target.value);
          if (value) onSelect(null);
        }}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (!open || !items.length) return;
          if (e.key === "ArrowDown") setActive((a) => Math.min(items.length - 1, a + 1));
          else if (e.key === "ArrowUp") setActive((a) => Math.max(0, a - 1));
          else if (e.key === "Enter") {
            e.preventDefault();
            const it = items[active];
            if (it) choose(it);
          } else if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && items.length > 0 && (
        <ul
          id={listId}
          role="listbox"
          className="absolute z-20 mt-1 max-h-60 w-full overflow-auto rounded-md border border-border bg-card shadow-lg"
        >
          {items.map((p, i) => (
            <li
              key={`${p.name}-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={() => choose(p)}
              className={`cursor-pointer px-3 py-2 text-sm ${i === active ? "bg-muted" : ""}`}
            >
              <span className="font-medium">{p.name}</span>
              {p.name_hi ? <span className="ml-2 text-muted-foreground">{p.name_hi}</span> : null}
              <span className="ml-2 text-xs text-muted-foreground">{p.type}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
