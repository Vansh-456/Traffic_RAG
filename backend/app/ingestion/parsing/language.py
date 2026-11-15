"""Script-based language detection and text-quality heuristics.

We avoid statistical language-ID libraries: Delhi traffic text is either
Devanagari Hindi, English, or Latin-script Hinglish, which script ratios plus
a small Hinglish lexicon separate reliably.
"""

from __future__ import annotations

import re
from typing import Literal

Lang = Literal["en", "hi", "mixed"]
QueryLang = Literal["en", "hi", "hinglish"]

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN = re.compile(r"[A-Za-z]")
_WORD = re.compile(r"[A-Za-z]+")

HINGLISH_WORDS = {
    "kya",
    "hai",
    "hain",
    "ke",
    "ki",
    "ka",
    "ko",
    "paas",
    "pass",
    "kal",
    "aaj",
    "kaisa",
    "kaise",
    "rahega",
    "rahegi",
    "raha",
    "rahi",
    "mein",
    "me",
    "nahi",
    "nahin",
    "kyun",
    "kyon",
    "kab",
    "kahan",
    "kaha",
    "jaana",
    "jana",
    "jaam",
    "jam",
    "band",
    "khula",
    "khuli",
    "abhi",
    "subah",
    "shaam",
    "raat",
    "wala",
    "wali",
    "se",
    "tak",
    "par",
    "bhi",
    "koi",
    "kuch",
    "batao",
    "bataiye",
    "chahiye",
    "hoga",
    "hogi",
    "gaya",
    "gayi",
    "kitna",
    "kitni",
    "sadak",
    "rasta",
    "raasta",
    "aur",
    "ya",
    "mujhe",
    "hum",
    "aap",
    "dikkat",
    "bheed",
    "barish",
    "baarish",
}

COMMON_EN = {
    "the",
    "and",
    "of",
    "to",
    "in",
    "on",
    "for",
    "is",
    "be",
    "will",
    "traffic",
    "road",
    "from",
    "at",
    "with",
    "by",
    "are",
    "this",
    "that",
    "delhi",
    "police",
    "advisory",
    "diversion",
    "near",
    "as",
    "an",
    "or",
    "not",
    "may",
    "it",
    "which",
    "vehicles",
    "route",
    "public",
    "avoid",
    "roads",
    "closed",
    "movement",
    "hrs",
    "am",
    "pm",
    "towards",
    "via",
}


def script_ratios(text: str) -> tuple[float, float]:
    """Return (devanagari_ratio, latin_ratio) over letter characters."""
    dev = len(_DEVANAGARI.findall(text))
    lat = len(_LATIN.findall(text))
    total = dev + lat
    if total == 0:
        return 0.0, 0.0
    return dev / total, lat / total


def detect_document_language(text: str) -> Lang:
    dev, lat = script_ratios(text)
    if dev >= 0.7:
        return "hi"
    if lat >= 0.85:
        return "en"
    if dev == 0 and lat == 0:
        return "en"
    return "mixed"


def detect_query_language(text: str) -> QueryLang:
    """en / hi (Devanagari) / hinglish (Latin script with Hindi function words)."""
    dev, _ = script_ratios(text)
    if dev >= 0.3:
        return "hi"
    words = [w.lower() for w in _WORD.findall(text)]
    if not words:
        return "en"
    hits = sum(1 for w in words if w in HINGLISH_WORDS)
    en_hits = sum(1 for w in words if w in COMMON_EN)
    if hits >= 2 or (hits >= 1 and hits > en_hits and len(words) <= 8):
        return "hinglish"
    return "en"


def valid_char_ratio(text: str) -> float:
    """Share of characters that are letters/digits/punctuation/whitespace we expect."""
    if not text:
        return 0.0
    ok = re.findall(r"[ऀ-ॿA-Za-z0-9\s.,;:!?()\-/&'\"%@#₹+–—]", text)
    return len(ok) / len(text)


def english_word_ratio(text: str) -> float:
    words = [w.lower() for w in _WORD.findall(text)]
    if not words:
        return 0.0
    return sum(1 for w in words if w in COMMON_EN) / len(words)


def looks_garbled(text: str) -> bool:
    """Heuristic for broken PDF text layers.

    Catches: empty/near-empty extraction (scanned PDFs), replacement chars,
    and legacy Hindi fonts (Kruti Dev etc.) which extract as Latin gibberish
    like ``fnYyh ;krk;kr iqfyl`` - Latin script but almost no English words.
    """
    stripped = text.strip()
    if len(stripped) < 40:
        return True
    if stripped.count("�") / len(stripped) > 0.01:
        return True
    if valid_char_ratio(stripped) < 0.85:
        return True
    dev, lat = script_ratios(stripped)
    if lat > 0.8 and len(stripped) > 200 and english_word_ratio(stripped) < 0.06:
        return True
    # Kruti Dev telltale: semicolons/tildes glued inside words
    glued = len(re.findall(r"[a-z][;~][a-z]", stripped))
    return glued > 5 and glued / max(1, len(stripped.split())) > 0.05
