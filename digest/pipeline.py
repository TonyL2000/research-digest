"""The whole run, top to bottom. Read this file first."""

import logging
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path

from digest import deliver as channels
from digest.config import Config, archive_url
from digest.dedup import SeenStore, is_duplicate, merge_duplicates
from digest.models import Digest
from digest.normalize import parse_feed
from digest.prefilter import cap, keep_announce_types, keyword_filter, mark_watched, within_lookback
from digest.render import render_html, render_markdown, render_wechat, subject, wechat_title
from digest.selection import select

log = logging.getLogger(__name__)


def run(
    config: Config,
    *,
    now: datetime,
    raw: dict[str, bytes],
    client,
    seen_path: Path | None,
    archive_dir: Path,
    preview_dir: Path | None = None,
    send: bool = True,
    env: Mapping[str, str],
) -> int:
    """Build one digest from already-downloaded feeds. Returns the process exit code.

    raw: {feed name: feed bytes}; feeds that failed to download are simply absent.
    client: an Anthropic client, a fake with the same messages.parse method, or None.
    seen_path: the seen store, or None to neither read nor write one (dry run).
    preview_dir: if set, the HTML and WeChat renderings are written there as files.
    """
    # 1. Normalize every feed into Item objects.
    items = []
    failed_feeds = []
    for feed in config.feeds:
        if feed.name not in raw:  # the download failed; fetch.py has logged why
            failed_feeds.append(feed.name)
            continue
        try:
            items += parse_feed(feed, raw[feed.name], now, config.max_summary_chars)
        except Exception as exc:
            # Feed content is written by strangers. Whatever is wrong with one feed must
            # not stop the others.
            log.error("feed %r could not be parsed: %s", feed.name, type(exc).__name__)
            failed_feeds.append(feed.name)
    if len(failed_feeds) == len(config.feeds):
        log.error("every feed failed; nothing to do")
        return 1
    mark_watched(items, config.watch_authors)

    # 2. Keep what is new and eligible. The cheap filters run before the title comparison
    #    so that an old copy of a story can never displace a fresh one.
    seen = SeenStore.load(seen_path) if seen_path else SeenStore()
    eligible = [item for item in items if not seen.contains(item.url)]
    eligible = within_lookback(eligible, now, config.lookback_hours)
    eligible = keep_announce_types(eligible, config.feeds)
    eligible = keyword_filter(eligible, config.include_keywords, config.exclude_keywords)
    merged = merge_duplicates(eligible)
    candidates, left_out = cap(
        merged, config.feeds, config.priority_keywords, config.max_candidates
    )
    log.info(
        "%d items fetched, %d eligible, %d after merging duplicates, %d candidates",
        len(items),
        len(eligible),
        len(merged),
        len(candidates),
    )
    if not candidates:
        log.info("nothing new today: no Claude call, no archive, nothing sent")
        return 0

    # 3. One Claude call (or the unranked fallback), then render.
    result = select(client, candidates, config)
    digest = Digest(
        day=now.date(),
        entries=result.entries,
        fallback_reason=result.fallback_reason,
        candidates=len(candidates),
        left_out=left_out,
        failed_feeds=failed_feeds,
        usage=result.usage,
    )
    markdown = render_markdown(digest)
    html = render_html(digest)

    # 4. Archive first, then mark as seen. A run that crashes before this point has
    #    marked nothing, so running it again the same day starts from the same items.
    archive = write_archive(archive_dir, digest.day, markdown, keep_existing=bool(seen_path))
    log.info("wrote %s (%d entries, ranked=%s)", archive, len(digest.entries), digest.ranked)
    if seen_path:
        # A ranked run uses up everything that was eligible: each item gets one chance.
        # An unranked run uses up only what it showed (and the copies of those stories
        # from other feeds), so that a second run the same day, once the model works
        # again, can still rank the rest.
        used = eligible
        if not digest.ranked:
            shown = [entry.item for entry in digest.entries]
            used = [item for item in eligible if any(is_duplicate(s, item) for s in shown)]
        seen.add((item.url for item in used), now.date())
        seen.prune(now.date())
        seen.save(seen_path)

    base = archive_url(config, env)
    wechat = render_wechat(digest, f"{base}/{archive.name}" if base else "")
    if preview_dir:
        preview_dir.mkdir(parents=True, exist_ok=True)
        (preview_dir / "email.html").write_text(html, encoding="utf-8")
        (preview_dir / "wechat.md").write_text(wechat, encoding="utf-8")
    if not send:
        return 0

    # 5. Deliver. Each channel is isolated; this step fails only if every enabled
    #    channel failed.
    enabled = {}
    if channels.email_enabled(env):
        enabled["email"] = lambda: channels.send_email(env, subject(digest), markdown, html)
    if channels.wechat_enabled(env):
        enabled["wechat"] = lambda: channels.send_wechat(env, wechat_title(digest), wechat)
    if not enabled:
        log.warning("no delivery channel is configured; the digest was only archived")
        return 0
    results = channels.deliver(enabled)
    return 0 if any(results.values()) else 1


def write_archive(folder: Path, day: date, markdown: str, keep_existing: bool) -> Path:
    """Write YYYY-MM-DD.md. With keep_existing, a second run on the same day gets a -2
    suffix instead of overwriting: thanks to the seen store its content is different."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{day.isoformat()}.md"
    number = 1
    while keep_existing and path.exists():
        number += 1
        path = folder / f"{day.isoformat()}-{number}.md"
    path.write_text(markdown, encoding="utf-8")
    return path
