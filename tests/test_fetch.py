import httpx2

from digest.config import Feed
from digest.fetch import PAUSE_SECONDS, fetch_all

FEED = b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>'
GOOD = Feed(name="Good", url="https://good.example.org/feed")
BAD = Feed(name="Bad", url="https://bad.example.org/feed")


def run(handler, feeds=(GOOD,)):
    """Call fetch_all against a fake server; returns (result, requests seen, sleeps asked for)."""
    requests: list[httpx2.Request] = []
    sleeps: list[float] = []

    def recording(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return handler(request, len(requests))

    raw = fetch_all(
        list(feeds),
        "research-digest/test",
        transport=httpx2.MockTransport(recording),
        sleep=sleeps.append,
    )
    return raw, requests, sleeps


def test_success_returns_the_bytes_and_sends_our_user_agent():
    raw, requests, sleeps = run(lambda request, n: httpx2.Response(200, content=FEED))
    assert raw == {"Good": FEED}
    assert requests[0].headers["user-agent"] == "research-digest/test"
    assert sleeps == []


def test_temporary_errors_are_retried_with_backoff():
    def handler(request, n):
        return httpx2.Response(503) if n < 3 else httpx2.Response(200, content=FEED)

    raw, requests, sleeps = run(handler)
    assert raw == {"Good": FEED}
    assert len(requests) == 3
    assert sleeps == [2, 4]


def test_connection_errors_are_retried():
    def handler(request, n):
        if n == 1:
            raise httpx2.ConnectTimeout("slow", request=request)
        return httpx2.Response(200, content=FEED)

    raw, requests, _ = run(handler)
    assert raw == {"Good": FEED}
    assert len(requests) == 2


def test_permanent_errors_are_not_retried():
    raw, requests, sleeps = run(lambda request, n: httpx2.Response(404))
    assert raw == {}
    assert len(requests) == 1
    assert sleeps == []


def test_a_bot_challenge_page_is_not_a_feed():
    page = b"<!DOCTYPE html><html><body>Checking your browser</body></html>"
    raw, _, _ = run(lambda request, n: httpx2.Response(200, content=page))
    assert raw == {}


def test_a_failing_feed_is_skipped_and_the_rest_still_load():
    def handler(request, n):
        if request.url.host == "bad.example.org":
            return httpx2.Response(500)
        return httpx2.Response(200, content=FEED)

    raw, requests, sleeps = run(handler, feeds=(BAD, GOOD))
    assert raw == {"Good": FEED}
    assert len(requests) == 4  # three attempts at the bad feed, then the good one
    assert sleeps == [2, 4, PAUSE_SECONDS]  # backoff, then the polite pause between feeds
