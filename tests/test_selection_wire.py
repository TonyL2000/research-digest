"""The selection step against the real Anthropic SDK, with the network replaced.

The fake client in test_selection.py checks our logic. These tests check our assumptions
about the SDK: the request it builds from a Pydantic model, and what parse() does with a
good reply, a refusal, a cut-off reply and an HTTP error. Nothing leaves the machine.
"""

import json

import anthropic
import httpx2
from helpers import make_config, make_item

from digest.selection import select

ITEMS = [make_item("First", "https://example.org/1"), make_item("Second", "https://example.org/2")]


def api_message(text: str | None, stop_reason: str = "end_turn") -> dict:
    """A Messages API response body, as documented."""
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [] if text is None else [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 321, "output_tokens": 54},
    }


def client_answering(status: int, body: dict, seen: list | None = None) -> anthropic.Anthropic:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx2.Response(status, json=body)

    return anthropic.Anthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def picks_json(*ids: str) -> str:
    pick = {"score": 4, "why_it_matters": "Why.", "summary": "Summary.", "tags": ["optics"]}
    return json.dumps({"picks": [{"id": item_id, **pick} for item_id in ids]})


def test_real_sdk_happy_path_and_request_shape():
    requests: list[dict] = []
    client = client_answering(200, api_message(picks_json(ITEMS[1].id)), requests)
    result = select(client, ITEMS, make_config())

    assert result.fallback_reason == ""
    assert [entry.item.title for entry in result.entries] == ["Second"]
    assert (result.usage.input_tokens, result.usage.output_tokens) == (321, 54)

    (request,) = requests  # one call
    assert request["model"] == "claude-haiku-5-5"
    assert "temperature" not in request
    schema = request["output_config"]["format"]["schema"]
    score = schema["$defs"]["Pick"]["properties"]["score"]
    assert score["enum"] == [1, 2, 3, 4, 5]  # the API itself enforces the 1-5 range


def test_real_sdk_refusal_falls_back():
    client = client_answering(200, api_message(None, "refusal"))
    assert "refusal" in select(client, ITEMS, make_config()).fallback_reason


def test_real_sdk_cut_off_reply_falls_back():
    cut_off = picks_json(ITEMS[0].id)[:40]
    client = client_answering(200, api_message(cut_off, "max_tokens"))
    assert "the reply was cut off" in select(client, ITEMS, make_config()).fallback_reason


def test_real_sdk_http_error_falls_back():
    error = {"type": "error", "error": {"type": "authentication_error", "message": "bad key"}}
    client = client_answering(401, error)
    assert "AuthenticationError" in select(client, ITEMS, make_config()).fallback_reason
