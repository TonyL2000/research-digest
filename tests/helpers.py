"""Small builders shared by the tests."""

from datetime import UTC, datetime
from types import SimpleNamespace

import yaml

from digest.config import ROOT, Config, Feed
from digest.models import Entry, Item, Pick, Selection
from digest.normalize import canonical_url, url_hash

NOW = datetime(2026, 10, 7, 20, 0, tzinfo=UTC)
FIXTURES = ROOT / "fixtures"


def make_item(title: str = "A title", url: str = "https://example.org/a", **fields) -> Item:
    url = canonical_url(url)
    values = {
        "id": url_hash(url)[:12],
        "title": title,
        "url": url,
        "source": "Feed A",
        "authors": "",
        "published": NOW,
        "summary": "",
    }
    return Item(**(values | fields))


def make_config(**fields) -> Config:
    values = {
        "feeds": [
            Feed(name="Feed A", url="https://example.org/a.xml", republish_summary=True),
            Feed(name="Feed B", url="https://example.org/b.xml"),
        ],
        "interests": "quantum optics",
    }
    return Config(**(values | fields))


def repo_config() -> Config:
    """The feeds from the committed config.yaml, with the settings a user is expected to
    edit pinned to fixed values, so that changing top_k, the model or the interests in
    config.yaml does not break the tests. config.local.yaml is not read."""
    data = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    data.update(
        interests="quantum optics and factor investing",
        watch_authors=[],
        include_keywords=[],
        exclude_keywords=[],
        top_k=10,
        max_candidates=150,
        lookback_hours=36,
        max_summary_chars=600,
        model="claude-haiku-5-5",
    )
    return Config(**data)


def make_entry(item: Item, **fields) -> Entry:
    values = {"score": 4, "why_it_matters": "Why.", "summary": "Summary.", "tags": ["optics"]}
    return Entry(item=item, **(values | fields))


def make_pick(item_id: str, score: int = 4, **fields) -> Pick:
    values = {"why_it_matters": "Why.", "summary": "Summary.", "tags": ["optics"]}
    return Pick(id=item_id, score=score, **(values | fields))


def model_reply(picks: list[Pick] | None, stop_reason: str = "end_turn") -> SimpleNamespace:
    """The parts of an SDK response that the selection step reads."""
    return SimpleNamespace(
        parsed_output=None if picks is None else Selection(picks=picks),
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
    )


class FakeClient:
    """Stands in for anthropic.Anthropic: returns a canned reply or raises."""

    def __init__(self, reply: SimpleNamespace | None = None, error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.calls: list[dict] = []
        self.messages = self

    def parse(self, **kwargs) -> SimpleNamespace:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.reply
