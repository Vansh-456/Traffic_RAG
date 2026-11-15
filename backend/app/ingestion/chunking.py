"""Structure-aware chunking with contextual chunk headers.

Rules (see docs/rag-design.md):
- Short documents (< ~800 tokens) → exactly one chunk. A diversion list is
  never separated from its heading.
- Long advisories → split on detected section headings (Restrictions,
  Diversions, Parking, Metro, Alternate routes, Do's and Don'ts, ... in
  English and Hindi) and on numbered-list structure. Sections larger than
  ~700 tokens are windowed on item/paragraph boundaries to 300-700 tokens
  with ~12% overlap *within* the section only; every window repeats the
  section heading so it stays self-contained.
- A contextual header (title, authority, date, validity, section) is
  prepended to produce ``text_with_context`` - the text that is embedded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from app.timeutil import to_ist

SHORT_DOC_TOKENS = 800
TARGET_MAX = 700
TARGET_MIN = 300
OVERLAP_RATIO = 0.12

SECTION_KEYWORDS = [
    r"restrictions?", r"diversions?", r"parking", r"metro", r"alternate\s+routes?",
    r"alternative\s+routes?", r"do'?s\s*(?:&|and)\s*don'?ts", r"dos\s+and\s+donts", r"advisory",
    r"road\s+closures?", r"closures?", r"timings?", r"traffic\s+arrangements?",
    r"routes?\s+to\s+be\s+avoided", r"roads?\s+to\s+be\s+avoided", r"general\s+instructions?",
    r"commuters?", r"heavy\s+vehicles?", r"goods\s+vehicles?", r"buses", r"emergency",
    r"no\s+parking", r"pick\s*(?:up)?\s*(?:&|and)\s*drop", r"helpline",
    r"प्रतिबंध", r"डायवर्जन", r"पार्किंग", r"मेट्रो", r"वैकल्पिक\s+मार्ग", r"क्या\s+करें",
    r"क्या\s+न\s+करें", r"यातायात\s+व्यवस्था", r"बंद\s+सड़कें", r"सलाह",
]
_SECTION_RE = re.compile(
    r"^\s*(?:[A-Z]\.|[IVX]+\.|\d+\.|\(\w\)|[-•*])?\s*(?:" + "|".join(SECTION_KEYWORDS) + r")\b.{0,60}$",
    re.IGNORECASE,
)
_LIST_ITEM = re.compile(r"^\s*(?:\d+[.)]|\(?[a-z]\)|[ivx]+[.)]|[-•*▪●])\s+", re.IGNORECASE)


def estimate_tokens(text: str) -> int:
    """Cheap tokenizer-free estimate (Devanagari tokenises ~2x denser)."""
    dev = len(re.findall(r"[ऀ-ॿ]", text))
    other = len(text) - dev
    return max(1, int(other / 4 + dev / 2))


def is_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 90:
        return False
    if _SECTION_RE.match(s):
        return True
    letters = re.sub(r"[^A-Za-z]", "", s)
    if len(letters) >= 6 and letters.isupper() and len(s.split()) <= 8:
        return True
    return bool(s.endswith(":") and len(s.split()) <= 8 and not _LIST_ITEM.match(s))


@dataclass
class Section:
    heading: str | None
    lines: list[str]

    @property
    def text(self) -> str:
        body = "\n".join(self.lines).strip()
        return f"{self.heading}\n{body}".strip() if self.heading else body


@dataclass
class ChunkDraft:
    ordinal: int
    section: str | None
    text: str
    token_count: int
    text_with_context: str = ""


def split_sections(text: str) -> list[Section]:
    sections: list[Section] = [Section(heading=None, lines=[])]
    for line in text.split("\n"):
        if is_heading(line):
            sections.append(Section(heading=line.strip().rstrip(":"), lines=[]))
        else:
            sections[-1].lines.append(line)
    return [s for s in sections if s.text.strip()]


def _units(lines: list[str]) -> list[str]:
    """Group lines into atomic units: list items (with continuation lines) or paragraphs."""
    units: list[str] = []
    buf: list[str] = []
    for line in lines:
        if not line.strip():
            if buf:
                units.append("\n".join(buf))
                buf = []
            continue
        if _LIST_ITEM.match(line) and buf:
            units.append("\n".join(buf))
            buf = [line]
        else:
            buf.append(line)
    if buf:
        units.append("\n".join(buf))
    # split monster paragraphs on sentence boundaries
    out: list[str] = []
    for u in units:
        if estimate_tokens(u) <= TARGET_MAX:
            out.append(u)
            continue
        sentences = re.split(r"(?<=[.!?।])\s+", u)
        cur = ""
        for s in sentences:
            if cur and estimate_tokens(cur + " " + s) > TARGET_MAX // 2:
                out.append(cur)
                cur = s
            else:
                cur = f"{cur} {s}".strip()
        if cur:
            out.append(cur)
    return out


def window_section(section: Section) -> list[str]:
    """Split one long section into overlapping windows that repeat its heading."""
    units = _units(section.lines)
    heading = section.heading
    windows: list[str] = []
    cur: list[str] = []
    for unit in units:
        candidate = [*cur, unit]
        if cur and estimate_tokens("\n".join(candidate)) > TARGET_MAX:
            windows.append("\n".join(cur))
            # overlap: carry trailing units up to OVERLAP_RATIO of the window
            budget = int(TARGET_MAX * OVERLAP_RATIO)
            carry: list[str] = []
            for prev in reversed(cur):
                if estimate_tokens("\n".join([prev, *carry])) > budget:
                    break
                carry.insert(0, prev)
            cur = [*carry, unit]
        else:
            cur = candidate
    if cur:
        windows.append("\n".join(cur))
    out = []
    for i, w in enumerate(windows):
        if heading:
            label = heading if i == 0 else f"{heading} (contd.)"
            out.append(f"{label}\n{w}")
        else:
            out.append(w)
    return out


def chunk_text(text: str) -> list[ChunkDraft]:
    """Chunk a cleaned document body."""
    text = text.strip()
    if not text:
        return []
    if estimate_tokens(text) < SHORT_DOC_TOKENS:
        return [ChunkDraft(0, None, text, estimate_tokens(text))]
    sections = split_sections(text)
    pieces: list[tuple[str | None, str]] = []
    pending: tuple[str | None, str] | None = None
    for section in sections:
        if estimate_tokens(section.text) > TARGET_MAX:
            if pending:
                pieces.append(pending)
                pending = None
            pieces.extend((section.heading, w) for w in window_section(section))
            continue
        # merge small adjacent sections up to TARGET_MAX so chunks aren't tiny
        if pending and estimate_tokens(pending[1]) < TARGET_MIN and estimate_tokens(
            pending[1] + "\n\n" + section.text
        ) <= TARGET_MAX:
            heading = " / ".join(h for h in (pending[0], section.heading) if h) or None
            pending = (heading, pending[1] + "\n\n" + section.text)
        else:
            if pending:
                pieces.append(pending)
            pending = (section.heading, section.text)
    if pending:
        pieces.append(pending)
    return [
        ChunkDraft(i, heading, body.strip(), estimate_tokens(body))
        for i, (heading, body) in enumerate(pieces)
        if body.strip()
    ]


def _fmt(dt: datetime | None) -> str | None:
    return to_ist(dt).strftime("%d %b %Y %H:%M IST") if dt else None


def build_context_header(
    *,
    title: str,
    authority: str | None,
    published_at: datetime | None,
    valid_from: datetime | None,
    valid_to: datetime | None,
    section: str | None,
    source_label: str,
) -> str:
    parts = [f"Source: {source_label}"]
    if authority:
        parts.append(f"Issued by: {authority}")
    parts.append(f"Title: {title.strip()}")
    if published_at:
        parts.append(f"Published: {_fmt(published_at)}")
    if valid_from or valid_to:
        parts.append(f"Valid: {_fmt(valid_from) or '?'} to {_fmt(valid_to) or 'until further notice'}")
    if section:
        parts.append(f"Section: {section}")
    return "[" + " | ".join(parts) + "]"


def with_context(header: str, text: str) -> str:
    return f"{header}\n{text}"
