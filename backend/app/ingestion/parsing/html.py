"""HTML main-text extraction and boilerplate cleaning."""

from __future__ import annotations

import html as html_lib
import re
from collections import Counter

_BOILERPLATE = re.compile(
    r"(follow us on|follow us at|like us on|subscribe to|download (the )?app|click here|"
    r"read more|also read|share this|whatsapp|telegram channel|log in to post comments|"
    r"all rights reserved|copyright ©|advertisement|हमें फॉलो करें|यह भी पढ़ें)",
    re.IGNORECASE,
)
_EMOJI = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f2ff‍️]+"
)


def strip_tags(fragment: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", fragment, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return html_lib.unescape(text)


def strip_emoji(text: str) -> str:
    return _EMOJI.sub("", text)


def clean_text(text: str) -> str:
    """Normalise whitespace, drop boilerplate lines and lines repeated on many pages."""
    text = text.replace("\r", "\n").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    counts = Counter(ln for ln in lines if ln)
    pages = max(1, text.count("\f") + 1)
    out: list[str] = []
    for ln in lines:
        if not ln:
            if out and out[-1]:
                out.append("")
            continue
        if _BOILERPLATE.search(ln) and len(ln) < 160:
            continue
        # repeated running header/footer (page numbers, letterheads)
        if pages > 2 and counts[ln] >= max(3, pages // 2) and len(ln) < 120:
            continue
        if re.fullmatch(r"(page\s*)?\d+(\s*(of|/)\s*\d+)?", ln, re.I):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def extract_main_text(html: str, url: str | None = None) -> str:
    """trafilatura main-content extraction with a tag-stripping fallback."""
    try:
        import trafilatura

        extracted = trafilatura.extract(
            html, url=url, include_comments=False, include_tables=True, favor_precision=True
        )
        if extracted and len(extracted) > 80:
            return clean_text(extracted)
    except Exception:
        pass
    return clean_text(strip_tags(html))
