"""Render a Digest as Markdown (archive and WeChat) and as HTML (email).

Feed text and model text are both untrusted, so every value is escaped on its way out.
Links always come from the feed entry, never from the model.
"""

import html
import json
from urllib.parse import quote

from digest.models import Digest, Entry
from digest.normalize import truncate

AUTHOR_CHARS = 80
WECHAT_TITLE_CHARS = 32  # ServerChan cuts longer titles
# ServerChan documents a 32 KB body limit without saying how it counts. The JSON-escaped
# length is the largest of the plausible readings, so staying under it is always safe.
WECHAT_BODY_LIMIT = 30_000


def md(text: str) -> str:
    """Escape text for Markdown. HTML characters, square brackets and backslashes become
    character codes, so the text cannot form a tag, a link or an image, and cannot
    cancel the escaping of a neighbouring character."""
    text = html.escape(text, quote=False)
    return text.replace("[", "&#91;").replace("]", "&#93;").replace("\\", "&#92;")


def md_link(title: str, url: str) -> str:
    # Percent-encode everything that could end the link early or start markup:
    # parentheses, spaces, quotes, angle brackets, backslashes.
    safe_url = quote(url, safe=":/?#[]@!$&'*+,;=%~")
    return f"[{md(title)}]({safe_url})"


def grouped(digest: Digest) -> list[tuple[str, list[Entry]]]:
    """Group entries by their first tag. Best-scoring groups and entries come first."""
    groups: dict[str, list[Entry]] = {}
    for entry in digest.entries:
        groups.setdefault(entry.tags[0] if entry.tags else "other", []).append(entry)
    for entries in groups.values():
        entries.sort(key=lambda entry: -(entry.score or 0))
    return sorted(groups.items(), key=lambda group: (-(group[1][0].score or 0), group[0]))


def meta_parts(entry: Entry) -> list[str]:
    """The small print under a title: source, authors, score, other feeds."""
    parts = [entry.item.source]
    if entry.item.authors:
        parts.append(truncate(entry.item.authors, AUTHOR_CHARS))
    if entry.score is not None:
        parts.append(f"score {entry.score}/5")
    if entry.item.also_in:
        parts.append("also in " + ", ".join(entry.item.also_in))
    return parts


def unranked_note(digest: Digest) -> str:
    return (
        f"Unranked: the model step did not complete ({digest.fallback_reason}). "
        "These items were picked by keyword and date, without the model's ranking or summaries."
    )


def footer(digest: Digest) -> str:
    lines = [f"{digest.candidates} candidates were considered."]
    if digest.left_out:
        lines.append(f"{digest.left_out} more were left out by the candidate cap.")
    if digest.failed_feeds:
        lines.append("Feeds that failed: " + ", ".join(digest.failed_feeds) + ".")
    if digest.usage:
        usage = digest.usage
        lines.append(
            f"Model {usage.model}: {usage.input_tokens:,} input and "
            f"{usage.output_tokens:,} output tokens, about ${usage.cost_usd:.4f}."
        )
    return " ".join(lines)


def heading(digest: Digest) -> str:
    return f"Research digest: {digest.day.isoformat()}"


def subject(digest: Digest) -> str:
    """Email subject line."""
    suffix = "" if digest.ranked else " (unranked)"
    return f"{heading(digest)}, {len(digest.entries)} items{suffix}"


def entry_markdown(entry: Entry, level: str = "###") -> str:
    lines = [f"{level} {md_link(entry.item.title, entry.item.url)}"]
    lines.append(md(" · ".join(meta_parts(entry))))
    # Untrusted text never starts a line: there "#", "-" or three backticks would turn
    # it into a heading, a list or a code block. A bold label in front prevents that.
    if entry.why_it_matters:
        lines.append(f"**Why it matters:** {md(entry.why_it_matters)}")
    if entry.summary:
        lines.append(f"**Summary:** {md(entry.summary)}")
    return "\n\n".join(lines)


def render_markdown(digest: Digest) -> str:
    blocks = [f"# {heading(digest)}"]
    if not digest.ranked:
        blocks.append(f"> {md(unranked_note(digest))}")
    for tag, entries in grouped(digest):
        blocks.append(f"## {md(tag)}")
        blocks += [entry_markdown(entry) for entry in entries]
    blocks.append("---")
    blocks.append(md(footer(digest)))
    return "\n\n".join(blocks) + "\n"


def render_html(digest: Digest) -> str:
    esc = html.escape  # escapes & < > and both kinds of quote
    out = [
        "<!doctype html>",
        '<html><body style="font-family: sans-serif; max-width: 680px; margin: auto;">',
        f"<h1>{esc(heading(digest))}</h1>",
    ]
    if not digest.ranked:
        out.append(f"<p><em>{esc(unranked_note(digest))}</em></p>")
    for tag, entries in grouped(digest):
        out.append(f"<h2>{esc(tag)}</h2>")
        for entry in entries:
            out.append(f'<h3><a href="{esc(entry.item.url)}">{esc(entry.item.title)}</a></h3>')
            out.append(f'<p style="color: #555;">{esc(" · ".join(meta_parts(entry)))}</p>')
            if entry.why_it_matters:
                out.append(f"<p><strong>Why it matters:</strong> {esc(entry.why_it_matters)}</p>")
            if entry.summary:
                out.append(f"<p><strong>Summary:</strong> {esc(entry.summary)}</p>")
    out.append(f'<hr><p style="color: #777; font-size: 12px;">{esc(footer(digest))}</p>')
    out.append("</body></html>")
    return "\n".join(out) + "\n"


def wechat_title(digest: Digest) -> str:
    count = len(digest.entries)
    detail = f"{count} items" if digest.ranked else f"{count}, unranked"
    return f"Digest {digest.day.isoformat()} ({detail})"[:WECHAT_TITLE_CHARS]


def render_wechat(digest: Digest, archive_link: str = "") -> str:
    """Markdown for ServerChan. If the digest is too long, whole entries are dropped
    from the end and a line says how many, with a link to the full archive."""
    best_first = sorted(digest.entries, key=lambda entry: -(entry.score or 0))
    blocks = [entry_markdown(entry, level="####") for entry in best_first]
    head = [f"# {heading(digest)}"]
    if archive_link:
        head.append(f"[Full digest]({archive_link})")
    if not digest.ranked:
        head.append(md(unranked_note(digest)))

    for shown in range(len(blocks), -1, -1):
        tail = []
        if shown < len(blocks):
            where = f" Full digest: {archive_link}" if archive_link else ""
            tail.append(f"(+{len(blocks) - shown} more items not shown here.{where})")
        body = "\n\n".join(head + blocks[:shown] + tail)
        if len(json.dumps(body)) <= WECHAT_BODY_LIMIT:
            break
    return body
