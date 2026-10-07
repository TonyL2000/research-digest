# Design notes

Every decision in this project, why it was made, what the alternatives were, and how to
switch to one of them. Read [`digest/pipeline.py`](digest/pipeline.py) alongside this
file: the sections below follow the order of that function.

Facts are labelled by where they come from:

- **measured**: observed directly on 2026-10-07, by fetching the feed, reading the
  library source, or running the code offline.
- **docs**: stated in official documentation on that date.
- **judgment**: a design opinion. These are the ones most worth challenging.

## Where this differs from the original specification

The project was built from a written specification. Six points were changed after
checking the specification against reality. Each has its own section below.

| Specification | What was built | Section |
|---|---|---|
| Cap at 60 candidates, newest first | Per-feed quotas, 150 in total, arXiv replacements dropped | [7](#7-the-candidate-cap) |
| Fetch feeds with `httpx` | `httpx2` | [3](#3-http-client-httpx2) |
| WeChat through the `serverchan-sdk` package | The same HTTP request, written out in 12 lines | [13](#13-wechat-delivery) |
| Cron `0 12 * * *` | Cron `17 12 * * *` | [16](#16-github-actions) |
| Merge titles with `token_set_ratio >= 90` | The same test, only across feeds and only for titles of similar length | [6](#near-duplicate-titles) |
| Fallback shows each item's own summary | Only for feeds whose text may be republished | [10](#10-the-unranked-fallback) |

Everything else follows the specification.

## 1. Shape of the program

**Decision.** One linear pipeline of plain functions, one module per step, and a single
`run()` function that calls them in order. No classes except the seen store and the
data models. No plugin system, no async.

**Why.** The job runs once a day over a few hundred items. The slowest part is waiting
for five HTTP responses. A reader can follow `pipeline.py` from top to bottom and find
each step in a file of 60 to 200 lines.

**Alternatives.** An async fetcher would save about ten seconds a day and cost a harder
mental model. A framework (Airflow, Prefect) is far more than a five-step script needs.

## 2. Build or reuse

**Decision.** Written from scratch. Ideas were borrowed from existing projects; no code
was copied. The README lists the prior art.

**Why.** About fifteen open-source digest tools were surveyed first (**measured**). None
used a single structured-output call, combined email with WeChat, or had an offline dry
run. The closest matches were small projects that would have needed most of their code
rewritten, and the two most popular ones are AGPL or GPL licensed, which cannot be
copied into an MIT project.

**Alternatives.** (a) Use [zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily)
as it is: working email digests in an hour or two, but it needs a Zotero library, has no
WeChat channel and does not call the Claude API. (b) Fork a small project and rework it:
the fork badge stays, and most of the code is still new.

## 3. HTTP client: httpx2

**Decision.** Feeds are fetched with `httpx2`, not `httpx`.

**Why.** The Anthropic SDK moved from `httpx` to `httpx2` in version 1.0 (**measured**:
`anthropic==1.12.0` requires `httpx2<3,>=2.0.0`). `httpx2` is the maintained fork of
`httpx`, published by Pydantic, with the same API. Using it adds no package. Using
`httpx` would install a second, nearly identical HTTP stack whose last release dates
from 2024, with class names that are easy to confuse with the SDK's.

**Alternative.** `httpx`, as the specification said.

**To switch.** In `digest/fetch.py` and `digest/deliver.py` replace `import httpx2` with
`import httpx` and rename the references. Add `httpx` and `httpcore` to
`requirements.txt`. In `deliver.py` the logger to silence becomes `"httpx"`. The tests
use `httpx2.MockTransport`; rename there too.

## 4. Fetching

**Decision.** For each feed: a 20-second timeout, up to three attempts, a wait of 2 and
then 4 seconds between attempts, and a retry only for network errors and HTTP 429, 500,
502, 503 and 504. A 3-second pause between feeds. A descriptive `User-Agent`. A response
only counts as a feed if it is HTTP 200 and starts like XML. A feed that fails is logged
and skipped. If every feed fails the run exits with an error.

**Why.**

- Retrying a 404 or a 403 cannot help, so those fail at once.
- The retry loop is 15 lines. A retry library would hide the same logic behind a
  decorator.
- arXiv asks for at most one request every three seconds (**docs**).
- Python's default user agent is rejected by Physics World (HTTP 403) and by several
  feeds that were dropped for that reason (**measured**).
- Nature's feeds answered a script with HTTP 200 and an HTML bot-check page
  (**measured**). Without the "starts like XML" check that would look like an empty
  feed.

**Feeds chosen.** APS Physics Magazine, Physics World and Quanta Magazine. Phys.org and
the Nature journals block scripted requests. Others had windows too short for a daily
job (**measured**). Change the list in `config.yaml`.

**Not known yet.** Whether Physics World and Quanta, both behind Cloudflare, answer
GitHub's servers the way they answered a home connection. The first real run will show.

## 5. Normalizing

**Decision.** `feedparser` parses; the code then builds an `Item` with a canonical URL,
a plain-text summary, one author string and a UTC timestamp.

- **Canonical URL.** `https`, lowercase host, no fragment, no trailing slash, and no
  tracking parameters (`utm_*`, `fbclid`, `gclid` and a few more). The remaining query
  parameters keep their order.
- **Item id.** The seen store is keyed by the full `sha256` of the canonical URL, as
  specified. The id shown to the model is the first 12 hex characters of the same hash.
  Shorter ids cost fewer tokens and are easier for a model to copy exactly. A collision
  among a few hundred items is practically impossible (12 hex characters are 48 bits).
- **Authors as one string.** arXiv puts all authors in one comma-separated field.
  Affiliations in parentheses contain commas too, and one paper in the sample listed
  128 names (**measured**). Splitting on commas would be wrong, so the string is kept
  whole and watched authors are matched by case-insensitive substring.
- **HTML stripping.** Done with the standard library's `html.parser`, skipping
  `<script>` and `<style>`. feedparser's own sanitizer is switched off because it
  treats arXiv's mathematics as markup: it turned `$1<q<3/2$` into `$1<q><3/2$` in one
  of 212 abstracts (**measured**). arXiv feeds are marked `plain_text: true` and skip
  stripping entirely. Nothing is lost by turning the sanitizer off: all text is escaped
  again when it is rendered.
- **Boilerplate.** Three small patterns remove text that feeds wrap around the summary:
  the arXiv `arXiv:... Announce Type: ... Abstract:` prefix, the WordPress "The post ...
  appeared first on ..." footer, and the APS byline and citation line.
- **Dates.** `published`, or `updated` when a feed has no publication date (APS). An
  entry with no date at all is treated as published now, so it gets one chance.
- **Summary length.** Cut to `max_summary_chars` (600) at a word boundary. This bounds
  the size, and so the cost, of the prompt.

**Alternatives.** `beautifulsoup4` for stripping: one more dependency for a job the
standard library does in 20 lines. Parsing the XML by hand: possible for arXiv, but APS
uses a different format (RSS 1.0) and `feedparser` hides that difference.

## 6. Deduplication

### Across runs: the seen store

**Decision.** `data/seen.json` maps `sha256(canonical URL)` to the date first seen.
Entries older than 60 days are removed. Every item that was eligible today is recorded,
whether or not it reached the model or the digest, and only after the archive file has
been written.

**Why.** "Each item gets exactly one chance" is a rule that can be stated in one
sentence. Recording only after the archive exists means a run that crashes earlier will
see the same items again tomorrow. Recording before delivery means a failed email does
not cause the same digest to be rebuilt and re-sent.

**Cost of this choice.** The file grows to roughly 200 entries a day for 60 days, about
1 MB, and is committed daily. Keys are sorted and written one per line, so each day's
diff is small.

**Alternative.** Record only what was shown to the model. The file would be smaller, but
a story merged away as a duplicate today would come back as "new" tomorrow.

### Within a run

**Decision.** Two items are the same if their canonical URLs match, or if they come from
different feeds and their titles are nearly equal.

### Near-duplicate titles

**Decision.** Titles are lowercased and stripped of punctuation, then compared with
rapidfuzz `token_set_ratio`. A score of 90 or more is a match, as specified, with two
extra conditions: the items must come from different feeds, and the shorter title must
be at least half as long as the longer one. The earliest item is kept and the other
feed's name is shown as "also in".

**Why the two conditions.** `token_set_ratio` ignores extra words. It scores 100 when
one title's words are a subset of the other's, so "Photon blockade" would match every
longer title containing those two words. And inside a single arXiv feed, "..., Part I"
and "..., Part II" are different papers. With about 200 arXiv titles a day, the plain
rule would merge papers that are not duplicates (**judgment**, covered by tests).

**Known gap.** If two feeds cover the same story on different days, the second one is
not recognised, because the seen store keeps hashes, not titles.

**To switch.** `TITLE_MATCH` and `MIN_LENGTH_RATIO` are constants at the top of
`digest/dedup.py`. To apply the rule inside one feed as well, delete the two lines in
`is_duplicate` that compare `a.source` and `b.source`.

### Order of the filters

Seen items, old items and unwanted types are removed before titles are compared.
Otherwise a three-week-old copy of a story could be "the earliest", win the merge, and
then be thrown away for being old, taking today's copy with it.

## 7. The candidate cap

**Decision.**

1. arXiv entries announced as `replace` or `replace-cross` are dropped.
2. Each feed has a `quota`: 100 for quant-ph + optics, 20 for q-fin, 10 for each news
   feed.
3. When a feed has more items than its quota, items that mention more
   `priority_keywords` go first, then newer items, then the feed's own order.
4. `max_candidates` is 150 and acts as a final safety limit.

**Why.** The specification said: cap at 60, watched authors first, then newest. On real
data that rule does not do what it intends (**measured** on 2026-10-07):

- The quant-ph + optics feed had 212 entries. 105 of them were replacements: old papers
  with a new version.
- Over that week the feed carried between about 107 and 280 new and cross-listed papers
  a day.
- Every arXiv entry carries the same timestamp, midnight US Eastern. "Newest first"
  among them is a tie, so a cap of 60 would have kept whichever entries happened to be
  listed first.
- News items published after midnight Eastern are "newer" than every paper, and the
  q-fin feed is small. Depending on the tie-break, either the news or the papers would
  have been crowded out entirely.

Quotas give every feed a guaranteed share. Cost is not the constraint: 150 candidates
is roughly 50,000 tokens, half a cent, and stays under the 100,000-token threshold above
which the default model charges five times more (**docs**).

**What `priority_keywords` are not.** They never remove an item. They only decide the
order when a feed is over quota. The hard filters are `include_keywords` and
`exclude_keywords`, which are empty by default.

**Alternatives.** Keep the cap at 60 and maintain an `include_keywords` list by hand.
Or split quant-ph and physics.optics into two feeds with separate quotas, so that optics
(about 20 to 40 papers a day) is never crowded out by quant-ph.

**To switch.** All of it is in `config.yaml`: `max_candidates`, each feed's `quota` and
`announce_types`. Remove `announce_types` from a feed to keep replacements. Setting
`max_candidates: 60` and deleting the quotas reproduces the original rule.

## 8. Watched authors

**Decision.** An item whose author string contains a watched name:

- passes the keyword filters,
- is exempt from its feed's quota and sorts first under the global limit,
- is marked `"must_include": true` in the prompt, and
- is appended to the digest by the code if the model leaves it out.

Nothing in the output says that an item was included because of the watch list.

**Why.** "Always" should not depend on a language model following an instruction. And
the list is meant to be private, while the archive is public.

**The gap this closes.** The specification put the real list in `config.local.yaml`,
which is gitignored and so never reaches GitHub Actions. The optional `WATCH_AUTHORS`
secret (names separated by semicolons) carries it there.

**Limit.** Matching is by substring of the author field as arXiv writes it. A name with
a TeX accent (`Hud\'ak`) must be written the same way to match.

## 9. Selecting with Claude

**Decision.** One call to `client.messages.parse()` per run, with a Pydantic model
(`Selection`: a list of picks, each with `id`, `score`, `why_it_matters`, `summary`,
`tags`). Default model `claude-haiku-5-5`. `max_tokens` 16,000, timeout 120 seconds,
two SDK retries. No sampling, effort or thinking parameters.

**Why one call.** The model ranks better when it sees all candidates together, and one
call means one thing to pay for, log and handle failures of. Most digest tools found in
the survey call the model once per paper.

**Why structured output.** The API constrains the reply to the JSON schema, so there is
no parsing of free text. `score` is declared as `Literal[1, 2, 3, 4, 5]`, which the SDK
sends as an `enum`; the API enforces enums, but not numeric ranges such as "between 1
and 5" (**docs**, and **measured** on the request the SDK builds).

**Why `max_tokens` is 16,000.** The default model thinks before it answers, and
thinking tokens count toward the limit (**docs**). A small limit could end the reply
before the JSON is complete.

**Why no extra parameters.** Parameters such as `effort` are not accepted by every
model. The call uses only what all structured-output models accept, so changing `model`
in `config.yaml` is enough.

**Checks on the reply.** In order: the call did not raise; `stop_reason` is neither
`refusal` nor `max_tokens`; there is a parsed object; it contains at least one pick;
no id appears twice; every id is one of the candidates. Any failure leads to the
fallback in section 10. Then picks are sorted by score, cut to `top_k`, and missing
watched authors are appended.

**What the model's text may do.** Nothing but be displayed. URLs written by the model
are removed, each field is cut to a maximum length, and tags are limited to three.
Titles and links always come from the feed entry with the matching id.

**Untrusted input.** Feed text is written by strangers and could contain instructions
aimed at the model. Defences, in the order they apply:

1. The system prompt states that everything inside `<candidates>` is data, never
   instructions.
2. Candidates are sent as JSON, with `<` and `>` escaped, so a title cannot close the
   `<candidates>` tag or break out of a string.
3. The task instructions come after the data.
4. The call has no tools. The worst a successful injection can do is skew the ranking
   or the wording of a summary.
5. Ids are validated, links never come from the model, and all output is escaped.

**Alternative.** Anthropic's guidance for untrusted content goes one step further and
delivers it as a `tool_result` block rather than in the user message (**docs**). That
needs a fabricated tool call in the conversation. It was left out as more machinery than
a tool-less call justifies (**judgment**).

**Not known yet.** The default model runs safety classifiers that can refuse a request,
and the documentation says ordinary machine-learning, biology or security text can
trigger them. How often a day's abstracts do so is unknown until it has run for a
while. A refusal produces an unranked digest, not a failed run.

## 10. The unranked fallback

**Decision.** If the selection step cannot produce a usable answer, the digest contains
the newest `top_k` candidates (watched authors first), is marked "Unranked" with the
reason, and is delivered and archived as usual.

**Triggers.** No API key; any exception from the call; `stop_reason` of `refusal` or
`max_tokens`; no parsed output; no picks; a repeated id; an id that is not a candidate.

**Why the `except` is broad.** The call can fail in ways that are not Anthropic errors.
Two were found by testing against the real SDK with the network replaced (**measured**):
a reply cut off mid-JSON raises `pydantic.ValidationError`, and a missing key raises
`TypeError` on the first request. The rule is that no failure in this step may cost the
day's digest. The exception type is printed in the digest, so a persistent problem is
visible rather than hidden.

**Own summaries, with one restriction.** The specification says fallback entries show
their own summaries. The archive is public and permanent. arXiv releases its metadata,
abstracts included, under CC0, so arXiv entries keep their summary. APS marks its feed
"Personal use only", and the other news teasers are copyrighted, so those entries show
title and link only. The per-feed switch is `republish_summary` in `config.yaml`.

**To switch.** Set `republish_summary: true` on a feed to show its text in fallback
digests.

## 11. Rendering

**Decision.** Two small hand-written renderers, one for Markdown and one for HTML, fed
by the same `Digest` object. Entries are grouped by their first tag; groups and entries
are ordered by score. A footer reports how many candidates were considered, how many
were left out, which feeds failed, and the token counts and cost.

**Why no template engine.** Jinja2 does not escape by default, and the `markdown`
package passes raw HTML through. Both would add a dependency and a subtler security
story. With f-strings every interpolated value visibly passes through an escape
function.

**Escaping.** HTML: `html.escape` on every value, including link targets. Markdown:
HTML special characters and square brackets become entities, so untrusted text cannot
create a link, an image or a tag. Parentheses in URLs are percent-encoded so they cannot
end a Markdown link early.

**Cost of this choice.** The raw Markdown shows `&#91;` where a title contains `[`. It
renders correctly on GitHub.

## 12. Email delivery

**Decision.** `smtplib` with STARTTLS, a 30-second timeout, and a message with a
plain-text part and an HTML part. Settings come from `SMTP_HOST`, `SMTP_PORT` (587 if
unset), `SMTP_USER`, `SMTP_PASS` and `MAIL_TO`; optional `MAIL_FROM`.

**Why.** It is the standard library, and port 587 with STARTTLS is what mail providers
offer for authenticated sending. GitHub's servers run on Azure, which leaves port 587
open and blocks port 25 (**docs**, Microsoft).

**Not supported.** Implicit TLS on port 465.

## 13. WeChat delivery

**Decision.** One `POST` to `https://sctapi.ftqq.com/<key>.send` with a title and a
Markdown body, written directly with `httpx2` (connect timeout 5 s, 20 s otherwise).
Success means the reply is JSON with `code` equal to 0. One retry, after 10 seconds, and
only if the connection could not be established.

**Why not the `serverchan-sdk` package.** The package is one 25-line function around
`requests.post` (**measured**, read from the published wheel). It sets no timeout, so a
stalled connection would hang the job until GitHub's limit. It pulls in `requests` and
its dependencies. And it switches silently between two different products depending on
the key's prefix. Writing the request out costs about 12 lines and adds a timeout.

**Turbo keys only.** ServerChan has two products. Turbo keys start with `SCT` and
deliver to WeChat through a service account. Keys starting with `sctp` belong to a
different product that delivers to ServerChan's own app (**docs**). A `sctp` key is
refused with a clear error, because it would report success while nothing reached
WeChat.

**Keeping the key secret.** The key is part of the URL, which creates three ways to
leak it into a public log. All three are closed and tested:

- `httpx2` logs every request URL at INFO level (**measured**). `deliver.py` raises
  that logger's level to WARNING.
- Exceptions from HTTP libraries can carry the URL. Only the exception's type name is
  logged, and the connection error is re-raised without its cause.
- `raise_for_status()` puts the URL in its message, so the code checks the JSON instead.

The reply is not logged either: on success it contains ids that open the message's
detail page.

**Length limits.** Title at most 32 characters (**docs**). The body limit is documented
as 32 KB without saying how it is counted, so the code measures the JSON-escaped length,
the largest plausible reading, and stays under 30,000. If the digest is longer, whole
entries are dropped from the end, lowest score first, and a line says how many, with a
link to the full digest in the repository.

**Not known yet.** None of this has been sent through a real account. What the WeChat
card shows, and whether the API answers GitHub's servers reliably, needs the first real
run.

**To switch to the SDK.** Add `serverchan-sdk==1.0.6` and its dependencies to
`requirements.txt`, and replace the body of `send_wechat` in `digest/deliver.py` with:

```python
from serverchan_sdk import sc_send

reply = sc_send(key, title, body)
if not isinstance(reply, dict) or reply.get("code") != 0:
    raise DeliveryError("ServerChan refused the message")
```

Keep `timeout-minutes` on the workflow job, because that call cannot time out.

## 14. Channel isolation and the exit code

**Decision.** Each enabled channel runs inside its own `try`. The process exits non-zero
only if at least one channel is enabled and all enabled channels failed. A channel whose
settings are missing or empty is not enabled. No enabled channel at all is a warning,
not a failure.

**Why.** One broken channel should not hide a working one, and a red run in GitHub
should mean "you received nothing today". Unset GitHub secrets arrive as empty strings
(**docs**), so empty is treated as missing.

## 15. Order of side effects

**Decision.** Archive, then seen store, then delivery. A second run on the same date
writes `DATE-2.md` instead of overwriting. If nothing is new, the run makes no API call,
writes nothing and sends nothing.

**Why.** The archive is the durable record, so it is written first. The workflow commits
even when the run step failed, so a day with failed delivery is still archived.

## 16. GitHub Actions

**Decision.**

- `daily.yml`: cron `17 12 * * *` (UTC) plus manual trigger; a concurrency group so two
  runs never push at once; a 15-minute timeout; `contents: write`; the commit step runs
  unless the job was cancelled, adds only `digests/` and `data/seen.json`, and skips the
  commit when nothing changed.
- `ci.yml`: `ruff check`, `ruff format --check` and `pytest` on every push and pull
  request, with read-only permissions and no secrets.
- Both use `actions/checkout@v7`, `actions/setup-python@v7` and Python 3.14, the same
  version as local development.

**Why 12:17 and not 12:00.** GitHub's documentation names the start of every hour as
the time when scheduled runs are delayed most (**docs**). 12:17 UTC is seven or eight
hours after arXiv's daily update, in both winter and summer time. An off-peak minute
reduces delays; it does not remove them.

**Why the commit, pull and push are on separate lines.** In a shell script run with
`set -e`, a failure inside an `a && b && c` chain does not stop the script. On separate
lines it does.

**The 60-day rule.** GitHub disables scheduled workflows in public repositories after
60 days without activity (**docs**). What counts as activity is not documented. In
other public repositories, commits pushed by the workflow itself have kept schedules
alive for years (third-party observation). This project relies on that and says so in
the README, with the command to re-enable. No keep-alive trick is used.

**To switch.** The cron expression is one line in `.github/workflows/daily.yml`.

## 17. Configuration

**Decision.** One committed `config.yaml`, an optional gitignored `config.local.yaml`
whose top-level keys replace the committed ones, validated by Pydantic with unknown keys
rejected.

**Why replace, not merge.** "A key in the local file replaces the same key" has no
special cases. A deep merge would need rules for lists.

**Why reject unknown keys.** A typo such as `topk: 5` would otherwise be ignored
silently.

## 18. Dry run

**Decision.** `--dry-run` swaps three things: recorded feeds for the network, a fake
client for Claude, and a fixed clock (the time the fixtures were recorded) for the real
one. Everything else is the real pipeline. Output goes to `out/`; the seen store and
`digests/` are not touched.

**Why a fixed clock.** With the real clock, the lookback window would exclude every
recorded item a day and a half after the fixtures were made.

**Fixtures.** The arXiv files are real snapshots (CC0 metadata), trimmed. The news feeds
are synthetic, with the real feeds' structure and invented content, because their text
is copyrighted.

## 19. Tests

**Decision.** `pytest`, fully offline, one test file per module, plus end-to-end runs on
the fixtures.

- The selection logic is tested with a hand-written fake client: every fallback path
  has a case.
- A second, smaller file runs the real Anthropic SDK against a fake network. It checks
  what the fake client cannot: the request the SDK builds from the Pydantic model, and
  what `parse()` does with a refusal, a cut-off reply and an HTTP error.
- HTTP code is tested with `httpx2.MockTransport`; email with a stand-in for
  `smtplib.SMTP`.
- One test asserts that the ServerChan key never appears in the log, for success,
  refusal and timeout.

## 20. Dependencies

**Decision.** Six direct dependencies, pinned exactly, with their thirteen transitive
dependencies pinned too. `requirements-dev.txt` adds `pytest` and `ruff`. No
`pyproject.toml`: the code runs as `python -m digest` from the repository root.

**Why pin transitive dependencies.** A scheduled job installs its dependencies fresh
every day. With only direct pins, thirteen packages could change version with no commit
in this repository.

**Left out on purpose.** `requests`, `beautifulsoup4`, `Jinja2`, `markdown`, `tenacity`,
`serverchan-sdk`.

## What only a real run can settle

1. Whether the three news feeds answer GitHub's servers.
2. Whether the chosen mail provider accepts a login from GitHub's address range.
3. Whether ServerChan is reachable from there, and what the WeChat card looks like.
4. Real token counts and cost. The estimate is under one cent per run.
5. How often the model's safety classifiers refuse a day's abstracts.
6. Whether the daily bot commit keeps the schedule alive past 60 days.
