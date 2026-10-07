# Fixtures

Recorded and hand-written feeds. `python -m digest run --dry-run` and the tests read
these instead of the network. `digest/dryrun.py` pretends the time is
2026-10-07 20:00 UTC, shortly after they were recorded.

| File | What it is |
|---|---|
| `arxiv_quant-ph_optics.xml` | Real snapshot of `rss.arxiv.org/rss/quant-ph+physics.optics` from 2026-10-07, trimmed from 212 items to 26: 12 new, 5 cross, 6 replace, 3 replace-cross. Kept on purpose: an abstract containing `$1<q<3/2$`, two author lists over 1,000 characters, an author list with affiliations in parentheses, and one with TeX accents. |
| `arxiv_q-fin.xml` | Real, complete snapshot of `rss.arxiv.org/rss/q-fin.PM+q-fin.ST` from the same day (5 items). |
| `aps_physics.xml` | Synthetic. Same structure as the APS Physics Magazine feed (RSS 1.0 / RDF, `dc:date` only, "Author(s):" prefix, citation tail). |
| `physics_world.xml` | Synthetic. Same structure as the Physics World feed (WordPress footer, tracking parameters). One item is a prompt-injection attempt. |
| `quanta.xml` | Synthetic. Same structure as the Quanta Magazine feed. One title is a near-duplicate of a Physics World item. |

The arXiv files are real because arXiv releases its metadata, abstracts included, under
CC0. The news feeds are synthetic because their text is copyrighted; only the XML
structure was copied, and all links point to `example.org`.
