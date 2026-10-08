"""Stand-ins for the network and for Claude, used by `python -m digest run --dry-run`."""

import json
import logging
from datetime import UTC, datetime
from types import SimpleNamespace

from digest.config import ROOT, Feed
from digest.models import Pick, Selection

log = logging.getLogger(__name__)

FIXTURES = ROOT / "fixtures"
# About an hour after the fixture feeds were fetched. A dry run pretends it is this
# time, so that the lookback window still contains the recorded items.
FIXTURE_NOW = datetime(2026, 10, 7, 20, 0, tzinfo=UTC)


def read_fixtures(feeds: list[Feed]) -> dict[str, bytes]:
    """The offline replacement for fetch.fetch_all."""
    raw = {}
    for feed in feeds:
        if feed.fixture:
            raw[feed.name] = (FIXTURES / feed.fixture).read_bytes()
        else:
            log.warning("feed %r has no fixture; a dry run skips it", feed.name)
    return raw


class FakeMessages:
    """Answers like client.messages, without the network: picks the first ten candidates."""

    def parse(self, *, messages: list[dict], **_ignored) -> SimpleNamespace:
        prompt = messages[0]["content"]
        data = prompt.split("<candidates>\n", 1)[1].split("\n</candidates>", 1)[0]
        picks = [
            Pick(
                id=row["id"],
                score=5 - index % 3,
                why_it_matters="[dry run] The model would explain the relevance here.",
                summary="[dry run] The model would summarize the item here.",
                tags=[row["source"].lower()],
            )
            for index, row in enumerate(json.loads(data)[:10])
        ]
        return SimpleNamespace(
            parsed_output=Selection(picks=picks),
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=0, output_tokens=0),
        )


class FakeClient:
    """Has the one attribute of anthropic.Anthropic that the pipeline uses."""

    messages = FakeMessages()
