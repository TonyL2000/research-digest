"""Cheap filters that run before the Claude call. None of them costs API money."""

import re
from datetime import datetime, timedelta

from digest.config import Feed
from digest.models import Item


def mark_watched(items: list[Item], watch_authors: list[str]) -> None:
    """Flag items with a watched author. The author field is one string (arXiv joins
    names with commas, and affiliations contain commas too), so search inside it."""
    # A name must match as a whole: "Wei Li" must match neither "Wei Liang" nor
    # "Jian-Wei Li". The pattern says "not preceded and not followed by a letter, a
    # digit or a hyphen".
    patterns = [
        re.compile(rf"(?<![\w-]){re.escape(name.strip().casefold())}(?![\w-])")
        for name in watch_authors
        if name.strip()
    ]
    for item in items:
        authors = item.authors.casefold()
        item.watched = any(pattern.search(authors) for pattern in patterns)


def within_lookback(items: list[Item], now: datetime, hours: int) -> list[Item]:
    cutoff = now - timedelta(hours=hours)
    return [item for item in items if item.published >= cutoff]


def keep_announce_types(items: list[Item], feeds: list[Feed]) -> list[Item]:
    """arXiv labels each entry new, cross, replace or replace-cross. A feed can list the
    types it wants; replacements are old papers with a new version."""
    wanted = {feed.name: feed.announce_types for feed in feeds}
    return [
        item
        for item in items
        if not wanted.get(item.source) or item.announce_type in wanted[item.source]
    ]


def _text(item: Item) -> str:
    return f"{item.title} {item.summary}".casefold()


def keyword_hits(item: Item, keywords: list[str]) -> int:
    """How many of the keywords appear in the item's title or summary."""
    text = _text(item)
    return sum(word.casefold() in text for word in keywords)


def keyword_filter(items: list[Item], include: list[str], exclude: list[str]) -> list[Item]:
    """Keep items that match an include keyword (if any are set) and no exclude keyword.
    Watched authors pass regardless."""
    include = [word.casefold() for word in include]
    exclude = [word.casefold() for word in exclude]
    kept = []
    for item in items:
        text = _text(item)
        wanted = not include or any(word in text for word in include)
        unwanted = any(word in text for word in exclude)
        if item.watched or (wanted and not unwanted):
            kept.append(item)
    return kept


def cap(
    items: list[Item], feeds: list[Feed], priority_keywords: list[str], max_candidates: int
) -> tuple[list[Item], int]:
    """Apply each feed's quota, then the overall limit.

    Returns the candidates and the number of items left out.
    """

    def rank(item: Item) -> tuple:
        # Sorted ascending: more keyword hits first, then newest. arXiv items share one
        # timestamp, so among them the keyword count and the feed's own order decide.
        return (-keyword_hits(item, priority_keywords), -item.published.timestamp())

    chosen: list[Item] = []
    for feed in feeds:
        own = sorted((item for item in items if item.source == feed.name), key=rank)
        watched = [item for item in own if item.watched]
        others = [item for item in own if not item.watched]
        if feed.quota is not None:  # watched authors always get in; the rest fill the quota
            others = others[: max(feed.quota - len(watched), 0)]
        chosen += watched + others

    chosen.sort(key=lambda item: (not item.watched, -item.published.timestamp()))
    candidates = chosen[:max_candidates]
    return candidates, len(items) - len(candidates)
