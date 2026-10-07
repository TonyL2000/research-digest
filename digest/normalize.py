"""Turn raw feed bytes into Item objects with clean, comparable fields."""

import hashlib
import logging
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser

from digest.config import Feed
from digest.models import Item

log = logging.getLogger(__name__)

# Query parameters that only track where a click came from. Anything starting with
# "utm_" is dropped as well.
TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "dclid",
    "msclkid",
    "yclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "_hsenc",
    "_hsmi",
    "ref_src",
}

# Tags that sit inside a sentence. Every other tag separates blocks of text, so it is
# replaced by a space when HTML is stripped.
INLINE_TAGS = {"a", "b", "i", "em", "strong", "span", "sub", "sup", "code", "u"}

# Text that feeds add around the real summary.
ARXIV_PREFIX = re.compile(r"^arXiv:\S+ Announce Type: \S+ Abstract: ")
BOILERPLATE = [
    # WordPress feeds (Physics World, Quanta): "The post <title> appeared first on <site>."
    re.compile(r"\s*The post .{1,300}? (?:appeared first|first appeared) on .{1,80}$"),
    # APS Physics: "[Physics 19, 135] Published Wed Oct 07, 2026"
    re.compile(r"\s*\[Physics \d+, \w+\] Published .{1,40}$"),
]


def canonical_url(url: str) -> str:
    """One spelling per page: https, lowercase host, no tracking parameters,
    no fragment, no trailing slash."""
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/")
    return urlunsplit(("https", parts.netloc.lower(), path, urlencode(query), ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


class _TextExtractor(HTMLParser):
    """Collects the text of an HTML fragment, skipping scripts and styles."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style"):
            self.hidden += 1
        if tag not in INLINE_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1
        if tag not in INLINE_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def strip_html(text: str) -> str:
    parser = _TextExtractor()
    parser.feed(text)
    parser.close()
    return "".join(parser.parts)


def squeeze(text: str) -> str:
    """Collapse every run of whitespace into one space."""
    return " ".join(text.split())


def truncate(text: str, limit: int) -> str:
    """Cut at a word boundary and mark the cut with an ellipsis."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def clean_summary(text: str, feed: Feed, authors: str, limit: int) -> str:
    # arXiv abstracts are plain text full of "<" and ">" from mathematics; an HTML
    # parser would eat them, so plain-text feeds skip that step.
    if not feed.plain_text:
        text = strip_html(text)
    text = squeeze(text)
    text = ARXIV_PREFIX.sub("", text)
    text = text.removeprefix(f"Author(s): {authors}").lstrip()  # APS repeats the byline
    for pattern in BOILERPLATE:
        text = pattern.sub("", text)
    return truncate(text, limit)


def parse_feed(feed: Feed, raw: bytes, now: datetime, max_summary_chars: int) -> list[Item]:
    """Parse one downloaded feed. Entries without a title or an http(s) link are dropped."""
    # sanitize_html is off because feedparser's sanitizer treats arXiv's "<" as markup.
    # We strip HTML ourselves and escape everything again when rendering.
    parsed = feedparser.parse(raw, sanitize_html=False, resolve_relative_uris=False)
    if parsed.bozo and not parsed.entries:
        log.warning("feed %r is malformed: %s", feed.name, type(parsed.bozo_exception).__name__)
    items = []
    for entry in parsed.entries:
        link = entry.get("link", "")
        title = squeeze(entry.get("title", ""))
        if not title or urlsplit(link).scheme not in ("http", "https"):
            continue
        url = canonical_url(link)
        authors = squeeze(", ".join(a["name"] for a in entry.get("authors", []) if a.get("name")))
        # APS only has an "updated" date. A feed with no date at all counts as new now.
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        published = datetime(*stamp[:6], tzinfo=UTC) if stamp else now
        items.append(
            Item(
                id=url_hash(url)[:12],
                title=title,
                url=url,
                source=feed.name,
                authors=authors,
                published=published,
                summary=clean_summary(entry.get("summary", ""), feed, authors, max_summary_chars),
                announce_type=entry.get("arxiv_announce_type", ""),
            )
        )
    return items
