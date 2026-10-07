"""The data shapes that pass between pipeline steps."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class Item(BaseModel):
    """One feed entry after normalization."""

    id: str  # first 12 hex characters of sha256(url); the id the model sees
    title: str
    url: str  # canonical form, see normalize.canonical_url
    source: str  # the feed's name from config.yaml
    authors: str  # one display string, e.g. "A. Author, B. Author"
    published: datetime  # timezone-aware, UTC
    summary: str  # plain text, at most max_summary_chars long
    announce_type: str = ""  # arXiv only: new, cross, replace or replace-cross
    watched: bool = False  # an author is on the watch list
    also_in: list[str] = []  # other feeds that carried the same item


class Pick(BaseModel):
    """One item chosen by the model."""

    id: str
    score: Literal[1, 2, 3, 4, 5]
    why_it_matters: str
    summary: str
    tags: list[str]


class Selection(BaseModel):
    """The structured output we ask Claude for."""

    picks: list[Pick]


class Entry(BaseModel):
    """One digest entry: a feed item plus the text shown with it."""

    item: Item
    score: int | None = None
    why_it_matters: str = ""
    summary: str = ""
    tags: list[str] = []


class Usage(BaseModel):
    """Token counts and estimated cost of the one Claude call."""

    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass
class SelectResult:
    """What the selection step returns. An empty fallback_reason means the model ranked it."""

    entries: list[Entry]
    usage: Usage | None = None
    fallback_reason: str = ""


class Digest(BaseModel):
    """Everything the renderers need."""

    day: date
    entries: list[Entry]
    fallback_reason: str = ""  # non-empty means the digest is unranked
    candidates: int = 0  # items shown to the model
    left_out: int = 0  # eligible items dropped by the candidate cap
    failed_feeds: list[str] = []
    usage: Usage | None = None

    @property
    def ranked(self) -> bool:
        return not self.fallback_reason
