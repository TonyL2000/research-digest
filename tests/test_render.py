import json
from datetime import date

from helpers import make_entry, make_item

from digest.models import Digest, Usage
from digest.render import (
    WECHAT_BODY_LIMIT,
    render_html,
    render_markdown,
    render_wechat,
    subject,
    wechat_title,
)


def sample_digest(**fields) -> Digest:
    optics = make_item(
        "Photon blockade in a ring",
        "https://arxiv.org/abs/2610.00001",
        source="arXiv",
        authors="A. Author, B. Author",
        also_in=["Physics World"],
    )
    finance = make_item("Factor timing", "https://arxiv.org/abs/2610.00002", source="arXiv q-fin")
    clock = make_item("Squeezed clocks", "https://example.org/clocks", source="News")
    values = {
        "day": date(2026, 10, 7),
        "entries": [
            make_entry(finance, score=3, tags=["finance"]),
            make_entry(clock, score=4, tags=["optics"]),
            make_entry(
                optics, score=5, tags=["optics", "cavity qed"], why_it_matters="It is on topic."
            ),
        ],
        "candidates": 150,
        "left_out": 12,
        "failed_feeds": ["Quanta Magazine"],
        "usage": Usage(
            model="claude-haiku-5-5", input_tokens=21345, output_tokens=3210, cost_usd=0.0037
        ),
    }
    return Digest(**(values | fields))


def test_markdown_groups_by_first_tag_best_first():
    text = render_markdown(sample_digest())
    assert text.startswith("# Research digest: 2026-10-07\n")
    order = [
        text.index(marker) for marker in ("## optics", "Photon blockade", "Squeezed", "## finance")
    ]
    assert order == sorted(order)
    assert "### [Photon blockade in a ring](https://arxiv.org/abs/2610.00001)" in text
    assert "arXiv · A. Author, B. Author · score 5/5 · also in Physics World" in text
    assert "**Why it matters:** It is on topic." in text
    assert "Unranked" not in text


def test_footer_reports_counts_failures_and_cost():
    text = render_markdown(sample_digest())
    assert "150 candidates were considered." in text
    assert "12 more were left out by the candidate cap." in text
    assert "Feeds that failed: Quanta Magazine." in text
    assert "21,345 input and 3,210 output tokens, about $0.0037" in text


def hostile_digest() -> Digest:
    item = make_item(
        "Evil <script>alert(1)</script> [click](https://evil.example.org)",
        'https://example.org/a(b)?q="x"&r=1',
        authors="<b>Mallory</b>",
    )
    entry = make_entry(
        item,
        why_it_matters="<img src=x onerror=alert(1)>",
        summary="![pixel](https://evil.example.org/p.png) & more",
        tags=["<i>tag</i>"],
    )
    return sample_digest(entries=[entry])


def test_markdown_neutralizes_markup_from_feeds_and_model():
    text = render_markdown(hostile_digest())
    assert "<script>" not in text and "<img" not in text and "<b>" not in text
    assert "[click]" not in text and "![pixel]" not in text  # no new links or images
    assert "&lt;script&gt;" in text
    assert "(https://example.org/a%28b%29?q=%22x%22&r=1)" in text  # the item's own link survives


def test_html_escapes_everything():
    text = render_html(hostile_digest())
    assert "<script>" not in text and "<img" not in text and "<b>" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert 'href="https://example.org/a(b)?q=%22x%22&amp;r=1"' in text
    assert text.count("<a ") == 1  # only the entry's own link


def test_html_has_the_same_structure_as_markdown():
    text = render_html(sample_digest())
    assert "<h1>Research digest: 2026-10-07</h1>" in text
    assert text.index("<h2>optics</h2>") < text.index("<h2>finance</h2>")
    assert (
        '<h3><a href="https://arxiv.org/abs/2610.00001">Photon blockade in a ring</a></h3>' in text
    )
    assert "<strong>Why it matters:</strong> It is on topic." in text


def test_unranked_digest_says_so_everywhere():
    digest = sample_digest(fallback_reason="stop_reason=refusal")
    assert "Unranked: the model step did not complete (stop_reason=refusal)" in render_markdown(
        digest
    )
    assert "Unranked" in render_html(digest)
    assert "Unranked" in render_wechat(digest)
    assert subject(digest) == "Research digest: 2026-10-07, 3 items (unranked)"
    assert wechat_title(digest) == "Digest 2026-10-07 (3, unranked)"


def test_subject_and_wechat_title():
    digest = sample_digest()
    assert subject(digest) == "Research digest: 2026-10-07, 3 items"
    assert wechat_title(digest) == "Digest 2026-10-07 (3 items)"
    many = sample_digest(entries=digest.entries * 40, fallback_reason="x")
    assert len(wechat_title(many)) <= 32


def test_wechat_body_lists_best_first_with_archive_link():
    link = "https://github.com/someone/research-digest/blob/main/digests/2026-10-07.md"
    text = render_wechat(sample_digest(), link)
    assert f"[Full digest]({link})" in text
    assert text.index("Photon blockade") < text.index("Squeezed") < text.index("Factor timing")
    assert "more items not shown" not in text


def test_wechat_body_is_truncated_by_whole_entries():
    link = "https://example.org/digests/2026-10-07.md"
    long_entries = [
        make_entry(make_item(f"Item {n}", f"https://example.org/{n}"), summary="word " * 150)
        for n in range(60)
    ]
    text = render_wechat(sample_digest(entries=long_entries), link)
    shown = text.count("#### [Item ")
    assert 0 < shown < 60
    assert len(json.dumps(text)) <= WECHAT_BODY_LIMIT
    assert f"(+{60 - shown} more items not shown here. Full digest: {link})" in text
    assert text.rstrip().endswith(")")  # the note is the last thing; no entry is cut in half
