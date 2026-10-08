# Design notes

The main decisions in this project: what was chosen, why, what the alternatives were,
and how to switch. Read [`digest/pipeline.py`](digest/pipeline.py) alongside this file;
sections 3 to 15 follow the order of that function. Unfamiliar terms are explained in
the [glossary](#glossary) at the end.

Facts are labelled by where they come from:

- **measured**: observed directly on 2026-10-07, by fetching a feed or a page, reading
  a library's source, or running the code offline.
- **docs**: stated in official documentation on that date.
- **judgment**: a design opinion. These are the ones most worth challenging.

Nothing here has run on GitHub yet. Everything was tested offline; the last section
lists what only a real run can settle.

## Where this differs from the original specification

The project was built from a written specification. These points were changed after
checking it against reality.

| Specification | What was built | Section |
|---|---|---|
| Cap at 60 candidates, newest first | Per-feed quotas, 150 in total, arXiv replacements dropped | [7](#7-the-candidate-cap) |
| Fetch feeds with `httpx` | `httpx2` | [3](#3-http-client-httpx2) |
| WeChat through the `serverchan-sdk` package | The same HTTP request, written out with a timeout | [13](#13-wechat-delivery) |
| Cron `0 12 * * *` | Cron `17 12 * * *` | [16](#16-github-actions) |
| Merge titles with `token_set_ratio >= 90` | The same test, only across feeds and only for titles of nearly equal length | [6](#near-duplicate-titles) |
| Watched authors always make the shortlist | They also always make the digest | [8](#8-watched-authors) |
| Fallback: the newest `top_k` items with their own summaries | Ordered by keyword match, then newest; own summaries only for feeds whose text may be republished | [10](#10-the-unranked-fallback) |
| Strip HTML from summaries | Not for feeds marked `plain_text` (arXiv) | [5](#5-normalizing) |
| Markdown for WeChat grouped by tag | One flat list, best first | [11](#11-rendering) |
| Exit non-zero only if every enabled channel fails | Also when every feed fails | [14](#14-channel-isolation-and-the-exit-code) |
| Archive `digests/YYYY-MM-DD.md` | A second run the same day writes `-2`; a day with nothing new writes no file | [15](#15-order-of-side-effects) |
| Dry run on recorded fixtures | arXiv fixtures are recorded; news fixtures are synthetic | [18](#18-dry-run) |

Smaller differences are noted in the section where they occur.

## 1. Shape of the program

**Decision.** One linear pipeline of plain functions, one module per step, and a single
`run()` function that calls them in order. Few classes: the seen store, the data and
configuration models, two exception types, one HTML parser subclass, and the fake client
of the dry run. No plugin system, no async.

**Why.** The job runs once a day over a few hundred items. A reader can follow
`pipeline.py` from top to bottom and find each step in a file of 60 to 200 lines.

**Alternatives.** An async fetcher: most of the fetch time is the deliberate
three-second pause between feeds, so there is little to gain. A workflow framework
(Airflow, Prefect): far more than a five-step script needs.

## 2. Build or reuse

**Decision.** Written new for this project, not forked. Ideas were borrowed from
existing projects; no code was copied.

**What was looked at.** Eight projects were read in some depth on 2026-10-07
(**measured**: repository pages, licences and source files as of that day):

| Project | Licence | How it uses the model | Delivery |
|---|---|---|---|
| [zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily) | AGPL-3.0 | OpenAI-compatible API, two calls per paper; ranks by similarity to a Zotero library | email |
| [TrendRadar](https://github.com/sansan0/TrendRadar) | GPL-3.0 | LiteLLM, many providers | many chat apps |
| [daily-arXiv-ai-enhanced](https://github.com/dw-dengwei/daily-arXiv-ai-enhanced) | modified Apache-2.0 | one structured call per paper, through LangChain | static web page |
| [gpt_paper_assistant](https://github.com/tatsu-lab/gpt_paper_assistant) | Apache-2.0 | OpenAI, batches, parses JSON lines from free text | Slack, web page |
| [ArxivDigest](https://github.com/AutoLLM/ArxivDigest) | MIT | OpenAI, scores parsed from text | email |
| [RSS-GPT](https://github.com/yinan-c/RSS-GPT) | MIT | OpenAI, one call per item | rewritten RSS feed |
| [ai-daily-digest](https://github.com/OliverZ-dot/ai-daily-digest) | MIT | DeepSeek, OpenAI or Anthropic, one call per item | ServerChan |
| [gw-digest](https://github.com/WendyDang/gw-digest) | no licence file | Anthropic SDK, free-text JSON | email |

**Why not reuse one.** The two largest are under copyleft licences, which would force
this project to adopt the same licence if their code were copied. The closest matches in
shape are small single-file projects; adapting one would have meant rewriting the model
call, the deduplication, the second delivery channel and all tests. What this project
set out to do differently: one structured-output call per run through the Anthropic SDK,
a fallback for each way that call can fail, and a dry run that needs neither the network
nor an API key.

**Alternatives.** (a) Use zotero-arxiv-daily as it is: working email digests within an
hour or two, but it needs a Zotero library, has no WeChat channel and does not call the
Claude API. (b) Fork a small project and rework it: the fork label stays on the
repository, and most of the code is still new.

## 3. HTTP client: httpx2

**Decision.** Feeds are fetched with `httpx2`, not `httpx`.

**Why.** The Anthropic SDK moved from `httpx` to `httpx2` in version 1.0 (**measured**:
`anthropic==1.12.0` requires `httpx2<3,>=2.0.0`). `httpx2` is the maintained fork of
`httpx`, published by Pydantic, with the same API. Using it adds no package. Using
`httpx` would install a second, nearly identical HTTP stack whose latest release dates
from December 2024, with class names that are easy to confuse with the SDK's.

**Alternative.** `httpx`, as the specification said.

**To switch.** In `digest/fetch.py` and `digest/deliver.py` replace `import httpx2` with
`import httpx` and rename the references; in `deliver.py` the logger to silence becomes
`"httpx"`. Add `httpx`, `httpcore` and `certifi` to `requirements.txt`. Rename `httpx2`
in `tests/test_fetch.py` and `tests/test_deliver.py`, but not in
`tests/test_selection.py` or `tests/test_selection_wire.py`: those build objects for the
Anthropic SDK, which only accepts `httpx2`.

## 4. Fetching

**Decision.** For each feed: a 20-second timeout and up to three attempts, waiting 3 and
then 6 seconds in between. Only temporary failures are retried: network errors and HTTP
429, 500, 502, 503 and 504. A 3-second pause between feeds. A descriptive `User-Agent`.
A response only counts as a feed if it is HTTP 200 and starts like XML. A feed that
fails in any way, while downloading or while parsing, is logged, listed in the digest
footer and skipped. If every feed fails, the run exits with an error.

**Why.**

- Retrying a 404, a 403 or a redirect loop cannot help, so those fail at once.
- arXiv asks for at most one request every three seconds (**docs**), so no wait is
  shorter than that.
- The retry loop is 15 lines. A retry library would hide the same logic behind a
  decorator.
- With Python's built-in user agent, Physics World answers HTTP 403 and Phys.org 400,
  and a Nature feed returned an HTML page with status 200. With this project's own
  `User-Agent` all three answered normally (**measured**, from a home connection). That
  is why the header is set, and why the body is checked and not only the status.

**Feeds chosen.** APS Physics Magazine, Physics World and Quanta Magazine: news written
for physicists, with usable summaries and a few items a day. Looked at and left out
(**measured**): Phys.org (its `robots.txt` asks AI crawlers to stay out of its news
section, the feed has no authors, and its two relevant sections overlap heavily), the
Nature journal feeds (tables of contents rather than news, eight items, dates without
times), and several feeds whose window is too short for a once-a-day job. Change the
list in `config.yaml`.

**To switch.** The numbers are constants at the top of `digest/fetch.py`.

**Not known yet.** Whether Physics World and Quanta, both behind Cloudflare, answer
GitHub's servers the way they answered a home connection.

## 5. Normalizing

**Decision.** `feedparser` parses; the code then builds an `Item` with a canonical URL,
a plain-text title and summary, one author string and a UTC timestamp.

- **Canonical URL.** `https`, lowercase host, no default port, no fragment, no trailing
  slash, and no tracking parameters (`utm_*`, `fbclid`, `gclid` and a few more). Other
  query parameters are kept exactly as the site wrote them. The same URL is the link
  shown in the digest. For the five default feeds that is harmless; a site that needs
  its trailing slash would be the exception.
- **Item id.** The seen store is keyed by the full `sha256` of the canonical URL, as
  specified. The id shown to the model is the first 12 hex characters of the same hash:
  fewer tokens, and easier for a model to copy exactly. Twelve hex characters are 48
  bits, so a collision among a few hundred items is practically impossible.
- **Authors as one string.** arXiv puts all authors in one comma-separated field.
  Affiliations in parentheses contain commas too, and one paper in the sample listed
  128 names (**measured**). Splitting on commas would be wrong, so the string is kept
  whole.
- **HTML stripping.** Done with the standard library's `html.parser`, skipping
  `<script>` and `<style>`, for titles and summaries. feedparser's own sanitizer is
  switched off because it treats arXiv's mathematics as markup: it turned `$1<q<3/2$`
  into `$1<q><3/2$` in one of 212 abstracts (**measured**). Feeds marked
  `plain_text: true` (arXiv) skip stripping entirely, which is a deviation from the
  specification's "strip HTML from summaries". Nothing is lost by turning the sanitizer
  off: all text is escaped again when it is rendered.
- **Boilerplate.** Three small patterns remove text that feeds wrap around the summary:
  the arXiv `arXiv:... Announce Type: ... Abstract:` prefix, the WordPress "The post ...
  appeared first on ..." footer (only when it is the very end of the text), and the APS
  byline and citation line.
- **Bad entries.** An entry without a title, or whose link is not a well-formed `http`
  or `https` URL with a host, is skipped.
- **Dates.** `published`, or `updated` when a feed has no publication date (APS). An
  entry with no date at all is treated as published now. It is shown once, and again
  only if it is still in the feed after the seen store has forgotten it.
- **Summary length.** Cut to `max_summary_chars` (600) at a word boundary. This bounds
  the size, and so the cost, of the prompt.

**Alternatives.** `beautifulsoup4` for stripping: one more dependency for a job the
standard library does in 20 lines. Parsing the XML by hand: possible for arXiv, but APS
uses a different format (RSS 1.0) and `feedparser` hides that difference.

## 6. Deduplication

### Across runs: the seen store

**Decision.** `data/seen.json` maps `sha256(canonical URL)` to the date first seen.
Entries older than 60 days are removed. It is updated only after the archive file has
been written:

- after a ranked run, with every item that was eligible, whether or not it reached the
  model or the digest;
- after an unranked run, only with the items that were shown.

**Why.** After a ranked run, "each item gets exactly one chance" is a rule that can be
stated in one sentence. An unranked run is different: the model never saw the items, so
using them all up would waste the day. Keeping them means that a second run on the same
day, after the cause is fixed, can still rank them. Writing the store only after the
archive exists means that a run that crashes earlier has marked nothing, so it can
simply be run again. That only helps on the same day: by tomorrow the arXiv feed has
moved on. Recording before delivery means a failed email does not cause the same digest
to be built and sent again.

**Why 60 days.** It only has to be longer than any feed keeps an item. The longest
window measured was Physics World's, about eight weeks.

**Cost of this choice.** The file grows by roughly 200 entries a day, to about 1 MB
after 60 days, and is committed daily. Keys are sorted and written one per line, so each
day's diff is small.

**Alternative.** Record only what was shown to the model. The file would be smaller, but
a story merged away as a duplicate today would come back as "new" tomorrow.

### Within a run

**Decision.** Two items are the same if their canonical URLs match, or if they come from
different feeds and their titles are nearly equal.

### Near-duplicate titles

**Decision.** Titles are lowercased and stripped of punctuation, then compared with
rapidfuzz `token_set_ratio`. A score of 90 or more is a match, as specified, with two
extra conditions: the items must come from different feeds, and the shorter title must
be at least 80% as long as the longer one. The earliest item is kept and the other
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
`is_duplicate` that compare `a.source` and `b.source`, and the test
`test_similar_titles_inside_one_feed_are_not_merged`.

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
data that rule does not do what it intends (**measured**):

- On 2026-10-07 the quant-ph + optics feed had 212 entries. 105 of them were
  replacements: old papers with a new version. That left 107.
- arXiv's listing pages for the five most recent announcement days show 86 to 257 new
  and cross-listed papers a day in quant-ph alone, and 20 to 37 in physics.optics. The
  day measured was the lightest of the five.
- Every arXiv entry carries the same timestamp, midnight US Eastern. "Newest first"
  among them is a tie, so a cap of 60 would have kept whichever entries happened to be
  listed first.
- News items published after midnight Eastern are "newer" than every paper. Depending
  on the tie-break, either the news or the papers would have been crowded out.

Quotas give every feed a guaranteed share. Cost is not the constraint: a full day's
snapshot gave 114 candidates and a prompt of about 91,000 characters (**measured**),
which stays well under the 100,000-token threshold above which the default model charges
five times more (**docs**).

**What `priority_keywords` are not.** They never remove an item. They only decide the
order when a feed is over quota, and in the fallback of section 10. The hard filters are
`include_keywords` and `exclude_keywords`, which are empty by default.

**Alternatives.** Keep the cap at 60 and maintain an `include_keywords` list by hand.
Or split quant-ph and physics.optics into two feeds with separate quotas, so that optics
is never crowded out by quant-ph.

**To switch.** All of it is in `config.yaml`: `max_candidates`, each feed's `quota` and
`announce_types`. Remove `announce_types` from a feed to keep replacements. Setting
`max_candidates: 60` and deleting the quotas reproduces the original rule.

## 8. Watched authors

**Decision.** An item is "watched" when its author string contains a name from the
watch list as whole words, ignoring case. A watched item:

- passes the keyword filters,
- is exempt from its feed's quota and sorts first under the global limit,
- is marked `"must_include": true` in the prompt,
- is kept even if the model ranks it below the `top_k` cut, and
- is appended to the digest by the code if the model leaves it out.

The specification only asked for the shortlist; the last two points go further, because
"always" should not depend on a language model following an instruction (**judgment**).

**Whole names.** "Wei Li" does not match "Wei Liang" or "Xiaowei Liu". Two different
people with exactly the same name cannot be told apart. A name with a TeX accent
(`Hud\'ak`) must be written the way arXiv writes it.

**Privacy, stated honestly.** The output never names the watch list. But an entry the
code had to append has no score and no model text and sits under its feed's name, so it
looks different from the others. Someone who reads the public archive for long enough
could infer who is on the list. If that matters, keep the repository private or leave
the list empty.

**The gap this closes.** The specification put the real list in `config.local.yaml`,
which is gitignored and so never reaches GitHub Actions. The optional `WATCH_AUTHORS`
secret (names separated by semicolons) carries it there.

**To switch.** To follow the specification literally, delete the block in
`selection.select` that appends missing watched items, and the part of the line above it
that keeps watched picks below the cut.

## 9. Selecting with Claude

**Decision.** One call to `client.messages.parse()` per run, with a Pydantic model
(`Selection`: a list of picks, each with `id`, `score`, `why_it_matters`, `summary`,
`tags`). Default model `claude-haiku-5-5`. `max_tokens` 16,000, timeout 120 seconds,
two SDK retries. No sampling, effort or thinking parameters.

**Why one call.** One call means one thing to pay for, log and handle failures of, and
the model can compare candidates with each other, which should rank them better than
scoring each alone (**judgment**; not tested). Most of the projects in section 2 call
the model once per paper.

**Why this model.** It is the cheapest current Claude model, and it supports structured
outputs (**docs**). Ranking abstracts against a paragraph of interests is not a hard
task (**judgment**). The model is one line in `config.yaml`.

**Why structured output.** The API constrains the reply to the JSON schema, so there is
no parsing of free text. `score` is declared as `Literal[1, 2, 3, 4, 5]`, which the SDK
sends as an `enum`; the API enforces enums, but not numeric ranges such as "between 1
and 5" (**docs**, and **measured** on the request the SDK builds).

**Why `max_tokens` is 16,000.** The default model thinks before it answers, and
thinking tokens count toward the limit (**docs**). A small limit could end the reply
before the JSON is complete.

**Why 120 seconds.** The SDK's default timeout is ten minutes. A daily job should not
hang that long. With two retries the worst case is six minutes, inside the workflow's
15-minute limit.

**Why no extra parameters.** Parameters such as `effort` are not accepted by every
model. The call uses only what all structured-output models accept, so changing `model`
in `config.yaml` is enough.

**Checks on the reply.** In order: the call did not raise; `stop_reason` is neither
`refusal` nor `max_tokens`; there is a parsed object; it contains at least one pick; no
id appears twice; every id is one of the candidates. Any failure leads to the fallback
in section 10. Then picks are sorted by score and cut to `top_k` (watched authors are
kept past the cut).

**What the model's text may do.** Nothing but be displayed. `http`, `https` and `www`
addresses written by the model are removed, each field is cut to a maximum length, and
tags are limited to three. Titles and links always come from the feed entry with the
matching id.

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

**Where this stops short of the official guidance.** Anthropic's guidance says
third-party content should be delivered as a `tool_result` block and not in the user's
own message (**docs**). This project puts it in the user message, inside tags, as the
specification asked. Following the guidance fully would mean adding a made-up tool call
to the conversation. For a call that has no tools and whose output is only displayed,
that was judged not worth the extra machinery (**judgment**).

**Cost logging.** Each run that receives a reply logs input tokens, output tokens and
the estimated cost, and prints them in the digest footer. One case cannot be logged:
when the reply is cut off mid-JSON, the SDK raises an error instead of returning the
response, so the tokens billed for that reply never reach this code.

**Not known yet.** The default model runs safety classifiers that can refuse a request,
and the documentation says ordinary machine-learning, biology or security text can
trigger them. How often a day's abstracts do so is unknown. A refusal produces an
unranked digest, not a failed run.

## 10. The unranked fallback

**Decision.** If the selection step cannot produce a usable answer, the digest contains
`top_k` candidates chosen without the model, is marked "Unranked" with the reason, and
is delivered and archived as usual. The order is: watched authors, then items that
mention the most `priority_keywords`, then the newest.

**Triggers.** No API key; any exception from the call; `stop_reason` of `refusal` or
`max_tokens`; no parsed output; no picks; a repeated id; an id that is not a candidate.

**Why not simply the newest.** The specification said "the newest `top_k`". By the
argument of section 7, on a normal day those are the morning's news headlines, and news
entries show no summary here (see below). Keyword matches first gives a fallback digest
that still contains papers (**judgment**).

**Why the `except` is broad.** The call can fail in ways that are not Anthropic errors.
Two were found by testing against the real SDK with the network replaced (**measured**):
a reply cut off mid-JSON raises `pydantic.ValidationError`, and a missing key raises
`TypeError` on the first request. The rule is that no failure in this step may cost the
day's digest. The reason is printed in the digest, so a persistent problem is visible
rather than hidden.

**Own summaries, with one restriction.** The archive is public and permanent. arXiv
releases its metadata, abstracts included, under CC0, so arXiv entries keep their
summary. APS marks its feed "Personal use only", and the other news teasers are
copyrighted, so those entries show title and link only. The per-feed switch is
`republish_summary` in `config.yaml`.

**To switch.** Set `republish_summary: true` on a feed to show its text in fallback
digests. For "newest only", remove `-hits` from the sort key in `selection.fallback`
and the test `test_fallback_prefers_priority_keywords_over_recency`.

## 11. Rendering

**Decision.** Hand-written renderers fed by the same `Digest` object: Markdown for the
archive, HTML for the email, and a WeChat body built from the same Markdown entry
blocks. In the archive and the email, entries are grouped by their first tag, and groups
and entries are ordered by score. The WeChat body is one flat list, best first. A footer
reports how many candidates were considered, how many were left out, which feeds failed,
and the token counts and estimated cost.

**Why the WeChat body is flat.** It may have to be shortened (section 13). In a flat
list ordered by score, cutting from the end removes the weakest entries. To group it as
well, call `grouped()` in `render_wechat` the way `render_markdown` does.

**Why no template engine.** Jinja2 does not escape by default, and the `markdown`
package passes raw HTML through. Both would add a dependency and a subtler security
story. With f-strings every interpolated value visibly passes through an escape
function.

**Escaping.**

- HTML: `html.escape` on every value, including link targets.
- Markdown: HTML special characters, square brackets and backslashes become character
  codes, so untrusted text cannot add a tag, Markdown link or image syntax, or cancel
  the escaping next to it. Link targets are percent-encoded.
- Untrusted text never starts a line, where `#`, `-` or three backticks would turn it
  into a heading, a list or a code block: summaries follow a bold "Summary:" label.
- What is not prevented: a bare URL or `www.` address inside feed text is shown as text,
  and GitHub turns such text into a link. arXiv abstracts legitimately contain URLs.

The two Markdown rules about line starts and backslashes were checked against GitHub's
own Markdown renderer (**measured**, through its public API).

**Cost of this choice.** The raw Markdown shows `&#91;` where a title contains `[`.
GitHub displays it as `[`.

## 12. Email delivery

**Decision.** `smtplib` with STARTTLS, a 30-second timeout, and a message with a
plain-text part and an HTML part. Settings come from `SMTP_HOST`, `SMTP_PORT` (587 if
unset), `SMTP_USER`, `SMTP_PASS` and `MAIL_TO` (one address, or several separated by
commas or semicolons); optional `MAIL_FROM`.

**Why.** It is the standard library, and port 587 with STARTTLS is what mail providers
offer for authenticated sending. GitHub's servers run on Azure, which leaves port 587
open and blocks port 25 (**docs**, Microsoft).

**To switch to port 465.** That port expects encryption from the first byte. In
`send_email`, use `smtplib.SMTP_SSL(host, port, timeout=..., context=...)` and delete
the `starttls` line.

## 13. WeChat delivery

**Decision.** One `POST` to `https://sctapi.ftqq.com/<key>.send` with a title and a
Markdown body, written directly with `httpx2` (connect timeout 5 s, 20 s otherwise).
Success means the reply is JSON with `code` equal to 0. One retry, after 10 seconds, and
only if the connection could not be established.

**Why not the `serverchan-sdk` package.** The package is one 25-line function around
`requests.post` (**measured**, read from the published wheel). It sets no timeout, so a
stalled connection would hang the job until GitHub's limit. It pulls in `requests` and
its dependencies. And it switches silently between two different products depending on
the key's prefix. Writing the request out takes about 20 lines and adds a timeout.

**Turbo keys only.** ServerChan has two products. Turbo keys start with `SCT` and
deliver to WeChat through a service account. Keys starting with `sctp` belong to a
different product that delivers to ServerChan's own app (**docs**). A `sctp` key is
refused early with a clear message.

**Keeping the key secret.** The key is part of the URL, which creates three ways to
leak it into a public log. All three are closed and tested:

- `httpx2` logs every request URL at INFO level (**measured**). `send_wechat` raises
  that logger's level to WARNING at the moment of sending. Doing it there, rather than
  once at start-up, matters: the Anthropic SDK lowers the same logger again when the
  `ANTHROPIC_LOG` debugging variable is set.
- Exceptions from HTTP libraries can carry the URL. Only the exception's type name is
  logged, and the connection error is re-raised without its cause.
- `raise_for_status()` puts the URL in its message, so the code checks the JSON instead.

The reply is not logged either: on success it contains ids that open the message's
detail page.

**Length limits.** Title at most 32 characters (**docs**). The body limit is documented
as 32 KB without saying how it is counted, so the code measures the JSON-escaped length,
the largest plausible reading, and stays under 30,000. If the digest is longer, whole
entries are dropped from the end and a line says how many, with a link to the full
digest in the repository.

**Not known yet.** None of this has been sent through a real account. What the WeChat
card shows, and whether the API answers GitHub's servers reliably, needs the first real
run.

**To switch to the SDK.** Add `serverchan-sdk==1.0.6` and its dependencies to
`requirements.txt`. In `send_wechat`, keep the lines that silence the logger, read the
key and refuse `sctp` keys, and replace the rest with:

```python
from serverchan_sdk import sc_send

reply = sc_send(key, title, body)
if not isinstance(reply, dict) or reply.get("code") != 0:
    raise DeliveryError("ServerChan refused the message")
```

The WeChat tests in `tests/test_deliver.py` pass a fake network to `send_wechat`, which
the SDK would ignore; they would have to patch `sc_send` instead. Keep `timeout-minutes`
on the workflow job, because that call cannot time out.

## 14. Channel isolation and the exit code

**Decision.** Each enabled channel runs inside its own `try`. The process exits non-zero
in two cases: every feed failed (section 4), or at least one channel is enabled and all
enabled channels failed. A channel whose settings are missing or empty is not enabled.
No enabled channel at all is a warning, not a failure.

**Why.** One broken channel should not hide a working one, and a red run in GitHub
should mean "you received nothing today". Unset GitHub secrets arrive as empty strings
(**docs**), so empty is treated as missing.

**To switch.** The two `return` lines at the end of `pipeline.run` decide the exit code.

## 15. Order of side effects

**Decision.** Archive, then seen store, then delivery. The digest is dated with the UTC
date of the run. A second run on the same date writes `DATE-2.md` instead of
overwriting. If nothing is new, the run makes no API call, writes nothing and sends
nothing.

**Why.** The archive is the durable record, so it is written first. The workflow commits
even when the run step failed, so a day with failed delivery is still archived. UTC
because the scheduled job runs in UTC; at 12:17 UTC the date is the same in every
timezone the reader is likely to be in.

## 16. GitHub Actions

**Decision.**

- `daily.yml`: cron `17 12 * * *` (UTC) plus manual trigger; checkout of the newest
  commit on the branch; a concurrency group so two runs never push at once; a 15-minute
  timeout; `contents: write`; the commit step runs unless the job was cancelled, adds
  only `digests/` and `data/seen.json`, and skips the commit when nothing changed.
- `ci.yml`: `ruff check`, `ruff format --check` and `pytest` on every push and pull
  request, with read-only permissions and no secrets.
- Both use `actions/checkout@v7`, `actions/setup-python@v7` and Python 3.14, the same
  version as local development.

**Why 12:17 and not 12:00.** GitHub's documentation names the start of every hour as
the time when scheduled runs are delayed most (**docs**). 12:17 UTC is seven or eight
hours after arXiv's daily update, in both winter and summer time. An off-peak minute
reduces delays; it does not remove them.

**Why check out the branch, not the triggering commit.** By default a run checks out the
commit that existed when the run was created. A run that waited in the queue, or was
re-run later, would start from an old `data/seen.json`, build and send the same digest
again, and then fail to push.

**Why the commit, pull and push are on separate lines.** In a shell script run with
`set -e`, a failure inside an `a && b && c` chain does not stop the script. On separate
lines it does (**measured**).

**Why the state lives in the repository.** The seen store and the archive are committed
by the job itself. The alternatives, an Actions cache or build artifacts, expire and are
not visible. A commit a day is also the public record that the tool runs.

**Why a public repository.** That record is the point. It has consequences that are
handled elsewhere in this file: news text stays out of the archive (section 10), the
watch list can leak by inference (section 8), and GitHub's 60-day rule applies.

**The 60-day rule.** GitHub disables scheduled workflows in public repositories after
60 days without activity (**docs**). What counts as activity is not documented. In
other public repositories, commits pushed by the workflow itself have kept schedules
alive for years (third-party observation). This project relies on that and says so in
the README, with the command to re-enable. No keep-alive trick is used.

**To switch.** The cron expression is one line in `.github/workflows/daily.yml`;
`tests/test_workflows.py` checks it and needs the same edit.

## 17. Configuration

**Decision.** One committed `config.yaml`, an optional gitignored `config.local.yaml`
whose top-level keys replace the committed ones, validated by Pydantic. Unknown keys,
out-of-range numbers (`top_k: 0`) and duplicate feed names are errors.

**Why replace, not merge.** "A key in the local file replaces the same key" has no
special cases. A deep merge would need rules for lists.

**Why reject instead of ignore.** A typo such as `topk: 5` would otherwise do nothing,
silently.

## 18. Dry run

**Decision.** `--dry-run` swaps three things: recorded feeds for the network, a fake
client for Claude, and a fixed clock (the time the fixtures were recorded) for the real
one. It switches two things off: the seen store and delivery. It reads `config.yaml`
only, ignoring `config.local.yaml` and the environment, so it gives the same result on
every machine. Output goes to `out/`.

**What it does not exercise.** `fetch.py`, the seen store and `deliver.py`. Those are
covered by the tests.

**Why a fixed clock.** With the real clock, the lookback window would exclude every
recorded item a day and a half after the fixtures were made.

**Fixtures.** The arXiv files are real snapshots (CC0 metadata), trimmed. The news feeds
are synthetic, with the real feeds' structure and invented content, because their text
is copyrighted.

## 19. Tests

**Decision.** `pytest`, fully offline, roughly one test file per module, plus end-to-end
runs on the fixtures.

- The selection logic is tested with a hand-written fake client: every fallback path
  has a case.
- A second, smaller file runs the real Anthropic SDK against a fake network. It checks
  what the fake client cannot: the request the SDK builds from the Pydantic model, and
  what `parse()` does with a refusal, a cut-off reply and an HTTP error.
- HTTP code is tested with `httpx2.MockTransport`; email with a stand-in for
  `smtplib.SMTP`.
- One test asserts that the ServerChan key never appears in the log, with request
  logging switched on, for success, refusal and timeout.
- The workflow files cannot be run locally; a test checks that they parse and contain
  what this document says.

## 20. Dependencies and Python version

**Decision.** Six direct dependencies, pinned exactly, with their thirteen transitive
dependencies pinned too. `requirements-dev.txt` adds `pytest` and `ruff`. No
`pyproject.toml`: the code runs as `python -m digest` from the repository root. Python
3.14 locally and on GitHub.

**Why pin transitive dependencies.** A scheduled job installs its dependencies fresh
every day. With only direct pins, thirteen packages could change version with no commit
in this repository.

**Why one Python version.** The code has only ever been run on 3.14, so that is the
only version the README promises. The linter is set one version lower so that it keeps
the widely known `except (A, B):` spelling.

**Left out on purpose.** `requests`, `beautifulsoup4`, `Jinja2`, `markdown`, `tenacity`,
`serverchan-sdk`.

## 21. Smaller numbers

| Setting | Value | Why |
|---|---|---|
| `lookback_hours` | 36 | A day, plus slack for a late run and for feeds that publish after the run. APS items carry a nominal 10:00 UTC stamp but appear in the afternoon (**measured**), so they are first seen about 26 hours "old". |
| `top_k` | 10 | What the specification asked for; about what one reads over coffee. |
| `max_summary_chars` | 600 | Roughly half an arXiv abstract (median 1,261 characters, **measured**), enough to judge relevance. |
| Seen-store age | 60 days | Longer than any feed keeps an item. |
| Score | 1 to 5 | Coarse on purpose: the model's score orders entries, nothing depends on its exact value. |

## What only a real run can settle

1. Whether the three news feeds answer GitHub's servers.
2. Whether the chosen mail provider accepts a login from GitHub's address range.
3. Whether ServerChan is reachable from there, and what the WeChat card looks like.
4. Real token counts and cost. The estimate is under one US cent per run.
5. How often the model's safety classifiers refuse a day's abstracts.
6. Whether the daily bot commit keeps the schedule alive past 60 days.

## Glossary

- **Canonical URL.** One agreed spelling of an address, so that two spellings of the
  same page compare equal.
- **Cloudflare.** A service that sits in front of many websites and may block requests
  that look automated.
- **Concurrency group** (GitHub Actions). Runs in the same group wait for each other
  instead of running at the same time.
- **`contents: write`** (GitHub Actions). Permission for the job to push commits.
- **Copyleft licence** (GPL, AGPL). Code copied from such a project forces the copying
  project to use the same licence.
- **Enum.** A value restricted to a fixed list, here the integers 1 to 5.
- **Prompt injection.** Text inside the data that tries to give the model instructions.
- **Pydantic model.** A Python class that describes the shape of some data and checks
  it.
- **RSS 1.0 / RDF, RSS 2.0.** Two XML formats for feeds. `feedparser` reads both.
- **`set -e`** (shell). Stop the script when a command fails.
- **sha256.** A function that turns any text into a fixed-length fingerprint.
- **STARTTLS.** The mail connection starts unencrypted and is upgraded to encrypted
  before the login. "Implicit TLS" (port 465) is encrypted from the first byte.
- **`stop_reason`.** The API's statement of why the model stopped: finished, hit the
  token limit, refused.
- **Structured output.** The API forces the reply to match a given JSON schema.
- **Token.** The unit in which the API measures and bills text, roughly three to four
  characters of English.
- **`tool_result`.** The message type in which a program hands the model the output of
  a tool it called.
- **Transitive dependency.** A package that one of our six packages needs in turn.
