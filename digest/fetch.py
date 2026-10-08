"""Download feeds. A feed that cannot be downloaded is logged and skipped."""

import logging
import time
from collections.abc import Callable

import httpx2

from digest.config import Feed

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 20.0
ATTEMPTS = 3
RETRY_STATUS = {429, 500, 502, 503, 504}
PAUSE_SECONDS = 3.0  # arXiv asks for at most one request every 3 seconds
FEED_STARTS = (b"<?xml", b"<rss", b"<rdf", b"<feed")


class FeedError(Exception):
    """A feed could not be downloaded, or what came back was not a feed."""


def fetch_one(client: httpx2.Client, url: str, sleep: Callable[[float], None]) -> bytes:
    """GET one feed, retrying temporary failures after 3 and then 6 seconds."""
    problem = ""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = client.get(url)
        except httpx2.TransportError as exc:  # timeouts, DNS, connection resets: retry
            problem = type(exc).__name__
        except (httpx2.HTTPError, httpx2.InvalidURL) as exc:
            # Redirect loops, undecodable bodies, a redirect to a malformed address: no
            # point in retrying. (InvalidURL is not a subclass of HTTPError.)
            raise FeedError(type(exc).__name__) from None
        else:
            if response.status_code == 200:
                return checked_body(response.content)
            problem = f"HTTP {response.status_code}"
            if response.status_code not in RETRY_STATUS:
                raise FeedError(problem)
        if attempt < ATTEMPTS:
            sleep(PAUSE_SECONDS * attempt)
    raise FeedError(f"gave up after {ATTEMPTS} attempts ({problem})")


def checked_body(body: bytes) -> bytes:
    """Reject bot-challenge pages, which arrive as HTTP 200 with HTML inside."""
    start = body.lstrip(b"\xef\xbb\xbf \t\r\n")[:20].lower()
    if not start.startswith(FEED_STARTS):
        raise FeedError("HTTP 200 but the body is not XML")
    return body


def fetch_all(
    feeds: list[Feed],
    user_agent: str,
    *,
    transport: httpx2.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, bytes]:
    """Return {feed name: raw bytes} for every feed that could be downloaded."""
    raw: dict[str, bytes] = {}
    with httpx2.Client(
        headers={"User-Agent": user_agent},
        timeout=TIMEOUT_SECONDS,
        follow_redirects=True,
        transport=transport,
    ) as client:
        for index, feed in enumerate(feeds):
            if index:
                sleep(PAUSE_SECONDS)
            try:
                raw[feed.name] = fetch_one(client, feed.url, sleep)
            except FeedError as exc:
                log.error("feed %r skipped: %s", feed.name, exc)
    return raw
