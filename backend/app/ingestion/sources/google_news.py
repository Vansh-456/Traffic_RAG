"""News via Google News RSS search, extra RSS feeds and GDELT (``trust=news``).

Google News links are redirect URLs, so we store title + summary for those;
direct publisher feeds (``EXTRA_RSS_FEEDS``) and GDELT articles get full text
through trafilatura when robots.txt allows (``NEWS_FULLTEXT``).
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import quote_plus, urlsplit

import feedparser

from app.config import get_settings
from app.ingestion.http import PoliteFetcher, RobotsDisallowedError
from app.ingestion.parsing.html import clean_text, extract_main_text, strip_tags
from app.ingestion.pipeline import DocumentDraft
from app.observability.logging import get_logger

log = get_logger(__name__)

GOOGLE_SOURCE_ID = "google_news"
RSS_SOURCE_ID = "extra_rss"
GDELT_SOURCE_ID = "gdelt"

RELEVANCE = re.compile(
    r"delhi|ncr|noida|gurugram|gurgaon|ghaziabad|faridabad|दिल्ली|एनसीआर", re.IGNORECASE
)
TRAFFIC = re.compile(
    r"traffic|jam|congestion|divert|diversion|closed|closure|road|flyover|waterlog|accident|"
    r"advisory|commut|ट्रैफिक|जाम|यातायात|सड़क|डायवर्जन|हादसा",
    re.IGNORECASE,
)


def google_news_urls(queries: list[str]) -> list[str]:
    urls = []
    for q in queries:
        enc = quote_plus(q)
        if re.search(r"[ऀ-ॿ]", q):
            urls.append(f"https://news.google.com/rss/search?q={enc}&hl=hi&gl=IN&ceid=IN:hi")
        else:
            urls.append(f"https://news.google.com/rss/search?q={enc}&hl=en-IN&gl=IN&ceid=IN:en")
    return urls


def _published(entry: feedparser.FeedParserDict) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(time.mktime(parsed), tz=UTC)


def is_relevant(title: str, summary: str) -> bool:
    blob = f"{title} {summary}"
    return bool(RELEVANCE.search(blob) and TRAFFIC.search(blob))


def parse_feed(xml: str, source_id: str, max_age_days: int = 14) -> list[DocumentDraft]:
    feed = feedparser.parse(xml)
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    drafts: list[DocumentDraft] = []
    for e in feed.entries:
        title = clean_text(strip_tags(e.get("title", "")))
        summary = clean_text(strip_tags(e.get("summary", "")))
        link = e.get("link")
        if not title or not link:
            continue
        publisher = None
        src = e.get("source")
        if src is not None:
            publisher = src.get("title") if isinstance(src, dict) else getattr(src, "title", None)
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(publisher) - 3].strip()
        if summary.startswith(title):  # Google News summary repeats the title
            summary = summary[len(title) :].strip(" -")
        published = _published(e)
        if published and published < cutoff:
            continue
        if not is_relevant(title, summary):
            continue
        drafts.append(
            DocumentDraft(
                source_id=source_id,
                url=link,
                title=title,
                trust_level="news",
                content_type="text",
                body_text=f"{title}\n\n{summary}".strip(),
                published_at=published,
                publisher=publisher or urlsplit(link).netloc,
                metadata={"feed": "google_news" if "news.google.com" in link else "rss"},
            )
        )
    return drafts


async def with_fulltext(fetcher: PoliteFetcher, draft: DocumentDraft) -> DocumentDraft:
    """Fetch the article body when allowed; keep title+summary otherwise."""
    if not get_settings().news_fulltext or "news.google.com" in draft.url:
        return draft
    try:
        res = await fetcher.get(draft.url)
    except RobotsDisallowedError:
        draft.metadata["fulltext"] = "robots_disallowed"
        return draft
    except Exception as exc:
        draft.metadata["fulltext"] = f"error:{type(exc).__name__}"
        return draft
    if res.status == 200 and "html" in res.content_type:
        body = extract_main_text(res.text, res.url)
        if len(body) > len(draft.body_text or ""):
            draft.body_text = f"{draft.title}\n\n{body[:20000]}"
            draft.metadata["fulltext"] = "ok"
    return draft


async def collect_google_news(fetcher: PoliteFetcher) -> list[DocumentDraft]:
    drafts: dict[str, DocumentDraft] = {}
    for url in google_news_urls(get_settings().news_queries):
        try:
            res = await fetcher.get(url, check_robots=False)  # RSS endpoints are meant for readers
            for d in parse_feed(res.text, GOOGLE_SOURCE_ID):
                drafts.setdefault(d.url, d)
        except Exception as exc:
            log.warning("google news feed failed", extra={"url": url, "err": str(exc)[:200]})
    return list(drafts.values())


async def collect_extra_rss(fetcher: PoliteFetcher) -> list[DocumentDraft]:
    out: list[DocumentDraft] = []
    for url in get_settings().extra_rss_feeds:
        try:
            res = await fetcher.get(url)
            for d in parse_feed(res.text, RSS_SOURCE_ID):
                out.append(await with_fulltext(fetcher, d))
        except Exception as exc:
            log.warning("rss feed failed", extra={"url": url, "err": str(exc)[:200]})
    return out


GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


async def collect_gdelt(fetcher: PoliteFetcher, days: int = 180) -> list[DocumentDraft]:
    """Backfill Delhi traffic articles from GDELT DOC 2.0 in 7-day windows."""
    out: dict[str, DocumentDraft] = {}
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    cursor = start
    query = '(delhi OR "new delhi") (traffic OR "road closed" OR diversion OR waterlogging) sourcecountry:IN'
    while cursor < end:
        nxt = min(end, cursor + timedelta(days=7))
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": "250",
            "sort": "datedesc",
            "startdatetime": cursor.strftime("%Y%m%d%H%M%S"),
            "enddatetime": nxt.strftime("%Y%m%d%H%M%S"),
        }
        try:
            res = await fetcher.get(GDELT_URL, params=params, check_robots=False)
            data = json.loads(res.text) if res.content else {}
        except Exception as exc:
            log.warning("gdelt window failed", extra={"from": str(cursor), "err": str(exc)[:200]})
            data = {}
        for art in (data or {}).get("articles", []):
            title, url = art.get("title", ""), art.get("url")
            if not url or not is_relevant(title, title + " delhi"):
                continue
            seen = art.get("seendate")
            published = (
                datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC) if seen else None
            )
            out.setdefault(
                url,
                DocumentDraft(
                    source_id=GDELT_SOURCE_ID,
                    url=url,
                    title=title,
                    trust_level="news",
                    content_type="text",
                    body_text=title,
                    published_at=published,
                    publisher=art.get("domain"),
                    language="hi" if art.get("language") == "Hindi" else "en",
                    metadata={"feed": "gdelt"},
                ),
            )
        cursor = nxt
    drafts = list(out.values())
    return [await with_fulltext(fetcher, d) for d in drafts]
