# Task 7 report — Hybrid retrieval for the assistant

## What was built

`netra/api/retrieval.py` (new) — lexical entity resolution over stdlib only.

- **BM25** (`k1=1.5`, `b=0.75`, smoothed RSJ idf) over an `EntityIndex` built
  from camera ids/names/city/district/capability, zone rule names, and
  watchlist plates, case references, notes, category, owner and make.
- `resolve(query, kind=None, limit=5) -> list[EntityMatch]`, with
  `EntityMatch(kind, id, label, score, via)`. `via` is `bm25` or `trigram` so
  the assistant can say *how* it guessed.
- **Confidence floor, two parts.** A hit must account for at least
  `MIN_COVERAGE = 0.5` of the query's idf mass (tokens absent from the corpus
  count in the denominator at maximum idf, so a query of mostly unknown words
  cannot pass on the one word it shares with a label), *and* the matched idf
  mass must reach `MIN_EVIDENCE = 1.0` nats. Coverage alone is a ratio and so
  is satisfiable by generic vocabulary — "the camera" would match every camera
  at coverage 1.0; the absolute floor makes a hit on common words never a
  resolution. An unrelated string therefore resolves to nothing.
- **Trigram fallback**, consulted only when BM25 returns nothing. Normalises to
  bare alphanumerics and requires 0.7 containment of the query's trigrams in
  the entity text. Candidates are the whole normalised phrase plus any word the
  index has never seen — only an unknown word can be the misspelling this
  exists for, and trying known words individually let a common one ("bypass")
  score a spurious 1.0 against every row containing it.
- Index rebuilt wholesale behind a 60 s TTL (`get_index`). Marked `ponytail:`
  with its ceiling (a five-figure corpus).

`netra/api/assistant.py` (wired)

- New `_search` intent, first in `INTENTS`: resolves the free text, then emits
  the SQL facts for each match (`_camera_facts`, `_zone_facts`,
  `_watchlist_facts`), each of which reads only from the database.
- `_scoped()` in `ask()` — not in `route()`, so routing stays a pure function
  of the text and its self-check stays database-free. When a question routed to
  `_camera_health` or `_coverage` confidently names one camera or zone, the
  answer is narrowed to it and **opens with `_resolution_note()`**: "I took
  that to mean camera cam11 (11 dolatpara-junagadh) — closest name match,
  inferred from your wording and not confirmed. Name the id directly if you
  meant a different one."
- `SCOPE_MARGIN = 0.9`: if the runner-up is within 10 % of the top score the
  mention is ambiguous and the estate-wide handler runs unchanged. Five
  Ahmedabad cameras score near-identically on "the Ahmedabad camera"; narrowing
  to whichever won by a rounding error would silently hide four of them.

## Division of responsibility

SQL produces every number in every answer. BM25 produces only an id, a label
and a rank. No embedding index over text was added — the vector half of the
hybrid is appearance re-identification in `analytics/reid.py`.

## Verification

All three commands pass:

```
.venv/Scripts/python.exe -m netra.api.retrieval   -> retrieval self-check passed
.venv/Scripts/python.exe -m netra.api.assistant   -> assistant self-check passed
.venv/Scripts/python.exe -c "from netra.api.app import app; print('app ok')"
```

`retrieval._self_check()` builds from a synthetic six-row corpus, no database.
It covers: exact name, partial name, misspelling via trigram, unrelated strings
(five of them) resolving to nothing, generic vocabulary resolving to nothing,
BM25 ranking the better lexical match higher, idf ordering, `kind` filtering,
resolution only ever returning corpus ids, an empty corpus, a short fragment
("toll" → "Tollnaka"), and the fallback never displacing a lexical hit.

`assistant._self_check()` keeps all pre-existing assertions and adds
route-level pins for `_search` in both directions, plus an assertion that
`_resolution_note` names the entity and marks it inferred.

Smoke-tested against the live database (31 cameras, 19 736 detections):
"look up the junagad bypass camera", "search for majewadi", "which camera is
dolatpara", "look for FIR", "search for zzzqqq" (declines), and every existing
intent unchanged.

## Concerns

- `ask("anything unusual?")` raises `sqlite3.OperationalError: no such column:
  traffic_stats.cumulative_total` against the local `data/netra.db`. **This is
  pre-existing** — confirmed by stashing this task's changes and reproducing —
  and is stale local data against a newer model, not a code fault. Not touched,
  as `data/` is out of scope.
- The trigram minimum query length is 4 characters. At four, the 0.7
  containment threshold demands every trigram match, which is strict, but a
  three-letter fragment would be noise in any corpus of this size.
- Scoping handles one entity per question. "Compare cam06 and cam08" resolves
  ambiguously and falls through to the estate-wide answer — the safe direction
  to fail, but comparative questions are the ceiling.

---

# Fix round 1/5 — intent vocabulary defeated resolution

## The fault

`resolve()` received the whole question. Intent words the corpus has never
seen — "down", "health", "coverage", "status" — entered the coverage
denominator at maximum idf and sank the mention standing beside them, so
`"is cam11 down"` resolved nothing while `"cam11"` resolved cleanly. The
resolution note tells the operator to *"Name the id directly"*; following that
instruction did not work.

## The fix

**`resolve(..., ignore=)`** — the caller passes its own intent vocabulary and
those tokens are dropped from the query. A token is dropped **only when the
corpus has never seen it** (`t not in self.df`), so a camera genuinely called
"Highway Junction" is never made unfindable by a word that also happens to be
an intent keyword. Both floors are otherwise untouched, so the words that
remain must still clear `MIN_COVERAGE` and `MIN_EVIDENCE`.

**`INTENT_VOCAB` in `assistant.py`** is derived from `INTENTS` itself rather
than written out, so a keyword added to an intent cannot be forgotten here.

**Exact-id priority.** `"GJ-JUN-004"` matched the zone rule sitting on that
camera ahead of the camera, because the zone's text names the camera id and,
being shorter, wins on length normalisation. A document whose *own id* is
fully named by the query now sorts ahead of one that merely mentions it:
identity beats term statistics.

**Bare mentions now route.** A question with no intent keyword at all
(`"cam11"`, `"majewadi"`) previously fell through to the decline. Resolution is
now tried *last* in `ask()` — after every intent, so it can never divert a
question an intent already claimed — and still declines when nothing resolves.

Both Minor items fixed: `_resolution_note(m, label)` is now given the name just
read from the database, so a camera renamed within the TTL is not named by its
stale index label beside fresh facts; and `resolve()` logs
`log.warning("entity resolution unavailable, answering unscoped: %r", exc)`
instead of swallowing the fault silently.

## Covering assertions (all database-free)

`retrieval._self_check()`, on the synthetic corpus:

```
resolve("is GJ-JUN-004 down", ignore=intent)               -> GJ-JUN-004
resolve("camera health for junagadh bypass", ignore=intent)-> GJ-JUN-004
resolve("coverage in rajkot", ignore=intent)               -> GJ-RAJ-002
resolve("how many detections on junagad", ignore=intent)   -> GJ-JUN-004 via trigram
resolve(x, ignore=intent) == [] for "the weather tomorrow", "banana",
    "xyzzy", "please", "the camera", "a zone", "show me",
    "how many detections", "is it down", "status"
resolve("north ring road", ignore={"north"})               -> still resolves
    (an ignored word the corpus knows stays matchable)
```

`assistant._self_check()`, using `retrieval.build_index(retrieval._SYNTHETIC)`
and the real `INTENT_VOCAB`:

```
{"down","health","coverage","faulty","status"} <= INTENT_VOCAB
"GJ-JUN-004" / "is GJ-JUN-004 down" / "camera health GJ-JUN-004"
    / "what is the status of GJ-JUN-004"        -> all GJ-JUN-004
"is the junagadh bypass camera down"            -> GJ-JUN-004
"camera health for rajkot ring road"            -> GJ-RAJ-002
"coverage in surat"                             -> GJ-SUR-009
[] for "the weather tomorrow", "banana", "xyzzy", "please", "the camera",
   "a zone", "show me", "which cameras are down?", "how many detections",
   "is the pipeline running"
```

## Commands run

```
.venv/Scripts/python.exe -m netra.api.retrieval   -> retrieval self-check passed
.venv/Scripts/python.exe -m netra.api.assistant   -> assistant self-check passed
.venv/Scripts/python.exe -c "from netra.api.app import app; print('app ok')"  -> app ok
git status --porcelain data/                      -> (empty)
```

The four reported failures, live, after `init_db()`:

```
is cam11 down               -> I took that to mean camera cam11 (11 dolatpara-junagadh) - closest
                               name match, inferred from your wording and not confirmed. ...
is the dolatpara camera down-> cam11, same note
camera health for majewadi  -> cam08 (08 majewadi-gate-junagadh), same note
coverage in bilimora        -> resolves cam27/cam28/cam29 at an identical 3.78, so SCOPE_MARGIN
                               declares it ambiguous and the estate-wide coverage answer runs.
                               Working as designed: three Bilimora cameras must not be reduced
                               to whichever one sorted first.
cam11                       -> now answered (1 match) instead of declined
```

Every pre-existing intent re-exercised live against the database and unchanged:
camera health, alerts, watchlist, detections, pipeline status, cloned plates
(both phrasings), plate trace (both phrasings), unusual/baseline, coverage,
map, help, empty question, and the weather question still declining.

`ask("anything unusual?")` now answers correctly — the
`traffic_stats.cumulative_total` error reported previously was stale local
data, and `init_db()` resolves it. That concern is withdrawn.
