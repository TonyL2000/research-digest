"""Turn raw feed bytes into Item objects with clean, comparable fields."""

import hashlib
import logging
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

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
# WordPress feeds (Physics World, Quanta) end with "The post <title> appeared first on
# <site>." The whole remaining text must match, see clean_summary.
WORDPRESS_FOOTER = re.compile(r"The post .{1,300} (?:appeared first|first appeared) on .{1,80}")
# APS Physics ends with "[Physics 19, 135] Published Wed Oct 07, 2026".
APS_CITATION = re.compile(r"\s*\[Physics \d+, \w+\] Published .{1,40}$")


def canonical_url(url: str) -> str:
    """One spelling per page: https, lowercase host, no tracking parameters,
    no fragment, no trailing slash."""
    parts = urlsplit(url.strip())
    # Drop the port only when it is the default one for the scheme the site used.
    default_port = {"http": ":80", "https": ":443"}.get(parts.scheme.lower(), ":")
    host = parts.netloc.lower().removesuffix(default_port)
    # Drop tracking parameters and leave the others exactly as the site wrote them.
    kept = [pair for pair in parts.query.split("&") if pair and not _is_tracking(pair)]
    return urlunsplit(("https", host, parts.path.rstrip("/"), "&".join(kept), ""))


def _is_tracking(pair: str) -> bool:
    key = pair.split("=", 1)[0].lower()
    return key.startswith("utm_") or key in TRACKING_PARAMS


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


def clean_summary(text: str, feed: Feed, title: str, authors: str, limit: int) -> str:
    # arXiv abstracts are plain text full of "<" and ">" from mathematics; an HTML
    # parser would eat them, so plain-text feeds skip that step.
    if not feed.plain_text:
        text = strip_html(text)
    text = squeeze(text)
    text = ARXIV_PREFIX.sub("", text)
    text = text.removeprefix(f"Author(s): {authors}").lstrip()  # APS repeats the byline
    text = APS_CITATION.sub("", text)
    # The footer repeats the entry's own title, so look for exactly that first. Cut
    # only if everything from there to the end is the footer, so that a teaser which
    # happens to contain the words "The post" is left alone.
    start = text.rfind(f"The post {title} ")
    if start == -1:
        start = text.rfind("The post ")
    if start != -1 and WORDPRESS_FOOTER.fullmatch(text[start:]):
        text = text[:start].rstrip()
    return truncate(text, limit)


def parse_feed(feed: Feed, raw: bytes, now: datetime, max_summary_chars: int) -> list[Item]:
    """Parse one downloaded feed. Entries without a title or a usable http(s) link are
    dropped."""
    # sanitize_html is off because feedparser's sanitizer treats arXiv's "<" as markup.
    # We strip HTML ourselves and escape everything again when rendering.
    parsed = feedparser.parse(raw, sanitize_html=False, resolve_relative_uris=False)
    if parsed.bozo and not parsed.entries:
        log.warning("feed %r is malformed: %s", feed.name, type(parsed.bozo_exception).__name__)
    items = []
    for entry in parsed.entries:
        link = entry.get("link", "")
        title = entry.get("title", "")
        # Strip markup only from titles feedparser itself classifies as HTML. Run on a
        # plain title, an HTML parser would cut "Cooling to T<Tc" down to "Cooling to T".
        if not feed.plain_text and entry.get("title_detail", {}).get("type") != "text/plain":
            title = strip_html(title)
        title = squeeze(title)
        try:
            parts = urlsplit(link)
        except ValueError:  # for example an unmatched "[" in the host
            continue
        if not title or parts.scheme not in ("http", "https") or not parts.hostname:
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
                summary=clean_summary(
                    entry.get("summary", ""), feed, title, authors, max_summary_chars
                ),
                announce_type=entry.get("arxiv_announce_type", ""),
            )
        )
    return items
