"""Polite HTTP fetching: robots.txt, per-domain rate limit, retries, UA."""

from __future__ import annotations

import asyncio
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.config import get_settings
from app.observability.logging import get_logger

log = get_logger(__name__)


class RobotsDisallowedError(RuntimeError):
    pass


@dataclass
class FetchResult:
    url: str
    status: int
    content: bytes
    content_type: str

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="ignore")


class PoliteFetcher:
    """Shared fetcher; one instance per worker process."""

    def __init__(self, delay: float | None = None, timeout: float = 30.0) -> None:
        settings = get_settings()
        self.delay = settings.crawl_delay_seconds if delay is None else delay
        self.user_agent = settings.user_agent
        self.client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": self.user_agent, "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8"},
        )
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    async def close(self) -> None:
        await self.client.aclose()

    async def __aenter__(self) -> PoliteFetcher:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _robots_for(self, base: str) -> urllib.robotparser.RobotFileParser | None:
        if base in self._robots:
            return self._robots[base]
        parser: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
        try:
            resp = await self.client.get(f"{base}/robots.txt")
            if resp.status_code == 200 and "text" in resp.headers.get("content-type", "text/plain"):
                assert parser is not None
                parser.parse(resp.text.splitlines())
            else:
                parser = None  # no robots.txt → everything allowed
        except httpx.HTTPError:
            parser = None
        self._robots[base] = parser
        return parser

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        robots = await self._robots_for(f"{parts.scheme}://{parts.netloc}")
        return True if robots is None else robots.can_fetch(self.user_agent, url)

    async def _throttle(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self.delay - (time.monotonic() - self._last.get(host, 0.0))
            if wait > 0:
                await asyncio.sleep(wait)
            self._last[host] = time.monotonic()

    async def get(self, url: str, *, check_robots: bool = True, **kwargs: object) -> FetchResult:
        if check_robots and not await self.allowed(url):
            raise RobotsDisallowedError(url)
        host = urlsplit(url).netloc
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=2, max=30),
            retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
            reraise=True,
        ):
            with attempt:
                await self._throttle(host)
                resp = await self.client.get(url, **kwargs)  # type: ignore[arg-type]
                if resp.status_code >= 500 or resp.status_code == 429:
                    resp.raise_for_status()
        return FetchResult(
            url=str(resp.url),
            status=resp.status_code,
            content=resp.content,
            content_type=resp.headers.get("content-type", ""),
        )
