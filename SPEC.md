# Specification

The written specification this project was built from, reproduced so that the
"differs from the specification" table in [DESIGN.md](DESIGN.md) can be checked. Only
the technical sections are included; the parts about motivation, working rules and
publishing are left out.

## Spec

1. Config: config.yaml (committed) plus optional config.local.yaml (gitignored;
   overrides it).
   - feeds: list of {name, url}. Start with:
     - https://rss.arxiv.org/rss/quant-ph+physics.optics
     - https://rss.arxiv.org/rss/q-fin.PM+q-fin.ST
     - 2-3 physics/science news feeds you find (e.g., APS Physics Magazine, Quanta
       Magazine). Verify each URL returns a valid feed; drop any that don't.
   - interests: a short paragraph I will edit: quantum optics, photon statistics and
     photon blockade, entangled-photon sources, integrated photonics, quantum
     information theory, factor investing.
   - watch_authors: optional list; papers by these authors always make the shortlist.
     My real list goes only in config.local.yaml.
   - top_k (10), max_candidates (60), lookback_hours (36), model, max_summary_chars
     (600).
2. Fetch: feedparser + httpx (timeouts, retries with backoff, descriptive User-Agent).
   A failing feed is logged and skipped; it never stops the run.
3. Normalize each item to {id, title, url, source, authors, published, summary}.
   Canonicalize URLs (https, lowercase host, drop utm_*/fbclid and similar, strip
   trailing slash). Strip HTML from summaries.
4. Deduplicate:
   - Across runs: data/seen.json maps sha256(canonical_url) -> first-seen date; prune
     entries older than 60 days.
   - Within a run: merge near-duplicate titles (normalize case/punctuation; rapidfuzz
     token_set_ratio >= 90), keeping the earliest item and noting the other sources.
5. Pre-filter (no API cost): lookback window, optional include/exclude keywords, then
   cap at max_candidates (watched authors first, then newest).
6. Select with Claude: official `anthropic` Python SDK, one call per run.
   - Use structured outputs via `client.messages.parse()` with a Pydantic schema: a
     list of {id, score 1-5, why_it_matters (1 sentence), summary (2-3 sentences),
     tags}.
   - Default model `claude-haiku-5-5` (confirm the current ID at
     https://platform.claude.com/docs/en/models/overview); configurable.
   - Article text is untrusted: wrap it in clearly delimited tags and tell the model to
     treat it as data and ignore any instructions inside it. Model output is only ever
     rendered, never executed.
   - If the call fails, stop_reason is "refusal" or "max_tokens", or the returned ids
     don't match the candidates: fall back to the newest top_k items with their own
     summaries and mark the digest "unranked".
   - Log input/output tokens and estimated cost per run.
7. Render Markdown (archive and WeChat) and HTML (email): date header, items grouped by
   tag, each with linked title, source, why-it-matters, summary.
8. Deliver; each channel is isolated (one failing never blocks the other; exit non-zero
   only if every enabled channel fails):
   - Email: smtplib + STARTTLS; env SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_TO.
   - WeChat: ServerChan via the `serverchan-sdk` package
     (`from serverchan_sdk import sc_send`), env SERVERCHAN_SENDKEY. Check its docs for
     message length limits; truncate gracefully with a link to the full archive.
9. Archive: commit digests/YYYY-MM-DD.md to the repo as public proof that it runs.
10. CLI: `python -m digest run [--dry-run] [--no-send]`. --dry-run uses recorded
    fixtures and a fake LLM client: no network, no API cost, nothing sent; output goes
    to ./out/.

## GitHub Actions

- .github/workflows/daily.yml: schedule cron "0 12 * * *" (UTC; arXiv feeds update daily
  at midnight US Eastern) plus workflow_dispatch, with a concurrency group. Steps:
  checkout, setup-python with pip cache, install, run, then commit digests/ and
  data/seen.json back as github-actions[bot] (permissions: contents: write; skip the
  commit if nothing changed).
- .github/workflows/ci.yml: on push and PR, run ruff and pytest.
- Secrets: ANTHROPIC_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_TO,
  SERVERCHAN_SENDKEY.
- README note: in public repos, GitHub disables scheduled workflows after 60 days
  without repository activity; explain how to re-enable.

## Tests (pytest, fully offline)

URL canonicalization; cross-run and near-duplicate dedup; seen-store pruning; lookback
filter; schema parsing and every fallback path; delivery isolation (one channel raises,
the other is still called); renderer output on fixtures.

## Repo

README (what it does, Mermaid architecture diagram, local setup, GitHub secrets setup,
cost per run, link to a real digest, limitations), DESIGN.md, MIT LICENSE, .gitignore
(.env, out/, __pycache__, config.local.yaml), pinned requirements.txt, type hints,
`logging` instead of print. Small commits with clear messages.
