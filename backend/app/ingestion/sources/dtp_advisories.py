"""Delhi Traffic Police advisories (primary, ``trust_level=official``).

Site structure (traffic.delhipolice.gov.in, Drupal 7):
- RSS: ``/en/rss.xml`` and ``/hi/rss.xml`` with the latest news-alert nodes
- listing: ``/{en,hi}/news-alertadvisory/advisory`` (taxonomy term pages)
- node pages ``<article class="node node-news-alert ...">`` with fields:
  advisory text, Language, category (Advisory/Alert), News Date
  (``13-Nov-2026``), End Date (``2026-11-28``), End Time (``18:00``), Link
- press-release nodes (``node-press-release``) often attach PDFs under
  ``/sites/default/files/...pdf``; legacy advisories live under
  ``/sites/default/files/Traffic Diversions/Advisory/*.pdf``

Incremental job: RSS + listing pages → new node URLs → fetch node + PDFs.
Backfill: sweep ``/en/node/{1..max}`` (max taken from the listing), keeping
news-alert and traffic press-release nodes. Everything goes through the
polite fetcher (robots, ≥ 2 s per domain) and the raw cache.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, field
from datetime import datetime, time
from urllib.parse import urljoin

import feedparser
from dateutil import parser as dtparser

from app.ingestion.http import PoliteFetcher
from app.ingestion.parsing.html import clean_text, strip_emoji, strip_tags
from app.ingestion.pipeline import DocumentDraft
from app.observability.logging import get_logger
from app.timeutil import IST

log = get_logger(__name__)

BASE = "https://traffic.delhipolice.gov.in"
SOURCE_ID = "dtp_advisories"
AUTHORITY = "Delhi Traffic Police"
RSS_URLS = [f"{BASE}/en/rss.xml", f"{BASE}/hi/rss.xml"]
LISTING_URLS = [f"{BASE}/en/news-alertadvisory/advisory", f"{BASE}/hi/news-alertadvisory/advisory"]
TRAFFIC_PDF_HINT = re.compile(r"traffic|advis|divers|restrict|arrangement|यातायात", re.I)


@dataclass
class NodeData:
    url: str
    node_id: int | None
    node_type: str
    title: str
    body: str
    language: str | None
    category: str | None
    news_date: datetime | None
    end_at: datetime | None
    link: str | None
    pdf_links: list[str] = field(default_factory=list)


def _field(html: str, name: str) -> str | None:
    m = re.search(
        rf'field-name-field-{name}[\s"].*?<div class="field-item[^"]*">(.*?)</div>', html, re.S
    )
    return clean_text(strip_tags(m.group(1))) if m else None


def _parse_news_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return dtparser.parse(value, dayfirst=True).replace(tzinfo=IST)
    except (ValueError, OverflowError):
        return None


def _parse_end(date_s: str | None, time_s: str | None) -> datetime | None:
    if not date_s:
        return None
    try:
        d = dtparser.parse(date_s).date()
    except (ValueError, OverflowError):
        return None
    t = time(23, 59)
    if time_s:
        m = re.match(r"(\d{1,2}):(\d{2})", time_s.strip())
        if m:
            t = time(int(m.group(1)) % 24, int(m.group(2)))
    return datetime.combine(d, t, IST)


def parse_node(html: str, url: str) -> NodeData | None:
    """Parse a DTP node page; returns None for non-content pages."""
    art = re.search(r'<article id="node-(\d+)" class="([^"]+)"', html)
    if not art:
        return None
    node_id, classes = int(art.group(1)), art.group(2)
    node_type = next(
        (
            c.removeprefix("node-")
            for c in classes.split()
            if c.startswith("node-")
            and c != "node-promoted"
            and c != "node-sticky"
            and not c.startswith("node-teaser")
        ),
        "unknown",
    )
    title_m = re.search(r"<h1[^>]*>\s*(.*?)\s*</h1>", html, re.S)
    title = strip_emoji(clean_text(strip_tags(title_m.group(1)))).strip() if title_m else ""
    if title.lower() in ("error page", "page not found !", "page not found"):
        return None
    body = _field(html, "news-alert-advisory") or _field(html, "body") or ""
    if not body:
        content = re.search(
            r'<div class="field field-name-body.*?</div>\s*</div>\s*</div>', html, re.S
        )
        body = clean_text(strip_tags(content.group(0))) if content else ""
    lang_m = re.search(r'<label class="control-label">Language</label>\s*([A-Za-zऀ-ॿ]+)', html)
    language = None
    if lang_m:
        language = "hi" if lang_m.group(1).lower().startswith(("hin", "हि")) else "en"
    elif "/hi/" in url:
        language = "hi"
    pdfs = [
        urljoin(BASE, html_lib.unescape(p))
        for p in re.findall(r'href="([^"]+\.pdf)"', html, re.I)
        if "i4c_handbook" not in p
    ]
    return NodeData(
        url=url,
        node_id=node_id,
        node_type=node_type,
        title=title,
        body=strip_emoji(body).strip(),
        language=language,
        category=_field(html, "news"),
        news_date=_parse_news_date(_field(html, "news-date")),
        end_at=_parse_end(_field(html, "end-date"), _field(html, "end-time")),
        link=_field(html, "link"),
        pdf_links=list(dict.fromkeys(pdfs)),
    )


def is_relevant(node: NodeData) -> bool:
    if node.node_type == "news-alert":
        return True
    if node.node_type == "press-release":
        return bool(TRAFFIC_PDF_HINT.search(node.title) or node.pdf_links)
    return False


def node_to_drafts(node: NodeData) -> list[DocumentDraft]:
    """HTML node → one draft; each attached PDF → its own draft."""
    meta = {
        "node_id": node.node_id,
        "node_type": node.node_type,
        "category": node.category,
        "link": node.link,
    }
    drafts = [
        DocumentDraft(
            source_id=SOURCE_ID,
            url=node.url,
            title=node.title,
            trust_level="official",
            content_type="text",
            body_text=f"{node.title}\n\n{node.body}".strip(),
            published_at=node.news_date,
            authority=AUTHORITY,
            language=node.language,
            valid_from_hint=node.news_date,
            valid_to_hint=node.end_at,
            metadata=meta,
        )
    ]
    return drafts


def discover_from_rss(xml: str) -> list[str]:
    feed = feedparser.parse(xml)
    return [e.link for e in feed.entries if getattr(e, "link", None)]


def discover_from_listing(html: str) -> list[str]:
    links = re.findall(r'<article id="node-\d+"[^>]*about="([^"]+)"', html)
    return [urljoin(BASE, html_lib.unescape(link)) for link in links]


def max_node_id(html: str) -> int:
    ids = [int(x) for x in re.findall(r'id="node-(\d+)"', html)]
    return max(ids) if ids else 0


async def fetch_node(fetcher: PoliteFetcher, url: str) -> list[DocumentDraft]:
    res = await fetcher.get(url)
    if res.status != 200:
        return []
    node = parse_node(res.text, res.url)
    if node is None or not is_relevant(node):
        return []
    drafts = node_to_drafts(node)
    for pdf_url in node.pdf_links:
        try:
            pdf = await fetcher.get(pdf_url)
        except Exception as exc:
            log.warning("pdf fetch failed", extra={"url": pdf_url, "err": str(exc)[:200]})
            continue
        if pdf.status != 200 or not pdf.content.startswith(b"%PDF"):
            continue
        drafts.append(
            DocumentDraft(
                source_id=SOURCE_ID,
                url=pdf_url,
                title=node.title or pdf_url.rsplit("/", 1)[-1],
                trust_level="official",
                content_type="pdf",
                raw=pdf.content,
                published_at=node.news_date,
                authority=AUTHORITY,
                valid_from_hint=node.news_date,
                valid_to_hint=node.end_at,
                metadata={"node_url": node.url, "node_id": node.node_id},
            )
        )
    return drafts


async def discover_incremental(fetcher: PoliteFetcher) -> list[str]:
    urls: list[str] = []
    for rss in RSS_URLS:
        try:
            urls += discover_from_rss((await fetcher.get(rss)).text)
        except Exception as exc:
            log.warning("dtp rss failed", extra={"url": rss, "err": str(exc)[:200]})
    for listing in LISTING_URLS:
        for page in range(3):
            url = listing if page == 0 else f"{listing}?page={page}"
            try:
                found = discover_from_listing((await fetcher.get(url)).text)
            except Exception as exc:
                log.warning("dtp listing failed", extra={"url": url, "err": str(exc)[:200]})
                break
            new = [u for u in found if u not in urls]
            if not new:
                break
            urls += new
    return list(dict.fromkeys(urls))


async def discover_backfill(
    fetcher: PoliteFetcher, start: int = 1, max_id: int | None = None
) -> list[str]:
    """Node URLs from `max_id` (default: newest id on the listing page + 5) down to `start`."""
    if max_id is None:
        listing = await fetcher.get(LISTING_URLS[0])
        max_id = max_node_id(listing.text) + 5
    top = max_id
    return [f"{BASE}/en/node/{i}" for i in range(top, start - 1, -1)]
