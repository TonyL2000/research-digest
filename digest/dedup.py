"""Deduplicate across runs (the seen store) and within a run (same URL or similar title)."""

import json
import re
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path

from rapidfuzz import fuzz

from digest.models import Item
from digest.normalize import url_hash

SEEN_MAX_AGE_DAYS = 60
TITLE_MATCH = 90  # minimum rapidfuzz token_set_ratio (0-100) to call two titles the same
MIN_LENGTH_RATIO = 0.5  # the shorter title must be at least half as long as the longer one


class SeenStore:
    """Maps sha256(canonical url) to the date the item was first seen."""

    def __init__(self, entries: dict[str, str] | None = None) -> None:
        self.entries = dict(entries or {})

    @classmethod
    def load(cls, path: Path) -> SeenStore:
        if not path.exists():
            return cls()
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def contains(self, url: str) -> bool:
        return url_hash(url) in self.entries

    def add(self, urls: Iterable[str], today: date) -> None:
        for url in urls:
            self.entries.setdefault(url_hash(url), today.isoformat())

    def prune(self, today: date) -> None:
        """Forget entries older than SEEN_MAX_AGE_DAYS so the file stops growing."""
        cutoff = (today - timedelta(days=SEEN_MAX_AGE_DAYS)).isoformat()
        self.entries = {key: day for key, day in self.entries.items() if day >= cutoff}

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Sorted, one entry per line: the daily git diff shows only what changed.
        text = json.dumps(self.entries, indent=0, sort_keys=True)
        path.write_text(text + "\n", encoding="utf-8")


def normalize_title(title: str) -> str:
    """Lowercase, punctuation to spaces, single spaces."""
    return " ".join(re.sub(r"[^\w\s]", " ", title.casefold()).split())


def is_duplicate(a: Item, b: Item) -> bool:
    if a.id == b.id:  # same canonical URL
        return True
    # Fuzzy matching only across feeds. Inside one feed, similar titles are different
    # entries ("... Part I" and "... Part II" on arXiv).
    if a.source == b.source:
        return False
    title_a, title_b = normalize_title(a.title), normalize_title(b.title)
    # token_set_ratio scores 100 when one title's words are a subset of the other's, so a
    # two-word title would match any longer title containing those words. Require
    # comparable lengths first.
    shorter, longer = sorted((len(title_a), len(title_b)))
    if shorter < MIN_LENGTH_RATIO * longer:
        return False
    return fuzz.token_set_ratio(title_a, title_b) >= TITLE_MATCH


def merge_duplicates(items: list[Item]) -> list[Item]:
    """Keep the earliest copy of each item and note the other feeds that carried it."""
    kept: list[Item] = []
    for item in sorted(items, key=lambda it: it.published):  # stable: ties keep feed order
        twin = next((other for other in kept if is_duplicate(other, item)), None)
        if twin is None:
            kept.append(item)
            continue
        twin.watched = twin.watched or item.watched
        if item.source != twin.source and item.source not in twin.also_in:
            twin.also_in.append(item.source)
    return kept
