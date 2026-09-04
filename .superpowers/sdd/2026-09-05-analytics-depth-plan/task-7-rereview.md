# Re-review — Task 7 fix round 1

d76e632 Stop intent words from sinking the entity they qualify

diff --git a/netra/api/assistant.py b/netra/api/assistant.py
index b794346..38d3b90 100644
--- a/netra/api/assistant.py
+++ b/netra/api/assistant.py
@@ -284,31 +284,36 @@ def _unusual(_q: str) -> dict:
                  f"({baseline.MIN_SAMPLES} observations required) for any "
                  f"judgement to be honest.")
     return _answer(text, data, actions)
 
 
 # -- entity resolution ------------------------------------------------------
 #
 # Everything below still reads its facts from SQL. Resolution only chooses
 # which id the SQL runs against; it never contributes a number.
 
-def _resolution_note(m: retrieval.EntityMatch) -> str:
+def _resolution_note(m: retrieval.EntityMatch, label: str | None = None) -> str:
     """The sentence that keeps a scoped answer honest.
 
     A narrowed answer is only trustworthy if the operator can see what it was
     narrowed to. It is never phrased as a certainty: the match is a guess about
     intent, and the wording has to invite correction.
+
+    `label` is the name just read from the database. The index is TTL-cached,
+    so its label can be up to a minute stale, and naming an entity by an old
+    name beside freshly read facts is exactly the kind of quiet inconsistency
+    that costs an operator their trust in the whole answer.
     """
     how = ("closest spelling in the registry" if m.via == "trigram"
            else "closest name match")
-    return (f"I took that to mean {m.kind} {m.id} ({m.label}) - {how}, "
-            f"inferred from your wording and not confirmed. Name the id "
+    return (f"I took that to mean {m.kind} {m.id} ({label or m.label}) - "
+            f"{how}, inferred from your wording and not confirmed. Name the id "
             f"directly if you meant a different one.")
 
 
 def _camera_facts(camera_id: str):
     """Everything the database knows about one camera. Nothing inferred."""
     with SessionLocal() as db:
         cam = db.get(Camera, camera_id)
         if cam is None:
             return None
         total = db.query(func.count(Detection.id)).filter(
@@ -401,21 +406,21 @@ def _entity_action(m: retrieval.EntityMatch) -> dict:
 
 
 def _search(q: str) -> dict:
     """Free-text lookup: resolve what was named, then state the SQL facts.
 
     Two steps that never blur into one. Retrieval produces ids and a ranking;
     every figure in the answer comes from a query against those ids.
     """
     query = re.sub(r"^\s*(search|look\s*(up|for)|lookup)\b(\s+for)?[:\s]*",
                    "", q or "", flags=re.I).strip() or (q or "")
-    matches = retrieval.resolve(query, limit=5)
+    matches = retrieval.resolve(query, limit=5, ignore=INTENT_VOCAB)
     if not matches:
         return _answer(
             f"Nothing in the camera registry, the zone rules or the watchlist "
             f"matches '{query}' closely enough for me to be sure what you "
             f"meant, and guessing would be worse than saying so. A camera id, "
             f"a place name or a registration number will find it.",
             {"query": query, "matches": []}, _help("")["actions"])
 
     found, lines = [], []
     for m in matches:
@@ -461,32 +466,35 @@ def _scoped(question: str, handler):
     """A per-entity answer when the question confidently names an entity.
 
     ponytail: one entity, chosen by margin over the runner-up. A question that
     names two ("compare cam06 and cam08") is answered about neither, falling
     through to the estate-wide handler - which is the safe direction to fail,
     but its ceiling is comparative questions, which need the resolver to return
     a set and the handlers to accept one.
     """
     if handler not in _SCOPABLE:
         return None
-    matches = (retrieval.resolve(question, kind="camera", limit=2) or
-               retrieval.resolve(question, kind="zone", limit=2))
+    matches = (retrieval.resolve(question, kind="camera", limit=2,
+                                 ignore=INTENT_VOCAB) or
+               retrieval.resolve(question, kind="zone", limit=2,
+                                 ignore=INTENT_VOCAB))
     if not matches:
         return None
     if len(matches) > 1 and matches[1].score >= SCOPE_MARGIN * matches[0].score:
         return None
     m = matches[0]
     got = _FACTS[m.kind](m.id)
     if got is None:
         return None
     text, data = got
-    return _answer(f"{_resolution_note(m)} {text}",
+    fresh = data.get("name") or data.get("plate")
+    return _answer(f"{_resolution_note(m, fresh)} {text}",
                    {"resolved": m.as_dict(), "facts": data},
                    [_entity_action(m)])
 
 
 def _help(_q: str) -> dict:
     return _answer(
         "I answer from live platform data. You can ask about camera health and "
         "which cameras are faulty, detection counts, current alerts, the "
         "watchlist, pipeline status, coverage by location, whether any plates look "
         "cloned, whether anything looks unusual against each camera's normal "
@@ -521,20 +529,31 @@ INTENTS = [
     (("where", "seen", "trace", "track", "find", "locate"), _find_plate),
     (("camera", "cameras", "down", "faulty", "degraded", "health", "broken"), _camera_health),
     (("alert", "alerts", "hit", "match", "matches"), _alert_summary),
     (("watchlist", "stolen", "wanted", "suspect", "blacklist"), _watchlist_summary),
     (("detection", "detections", "vehicles", "cars", "count", "how many"), _detection_summary),
     (("pipeline", "running", "status", "system"), _pipeline_status),
     (("coverage", "map", "location", "city", "where are"), _coverage),
     (("help", "what can you", "commands", "hello", "hi"), _help),
 ]
 
+#: Every word that appears in an intent's keywords. These are the words that
+#: told the router *what* is being asked; they say nothing about *of what*, and
+#: an entity resolver that counts them as unexplained information will sink the
+#: mention standing next to them - "is cam11 down" would resolve nothing while
+#: "cam11" resolves cleanly. Derived from INTENTS rather than written out, so a
+#: keyword added to an intent cannot be forgotten here. The resolver drops one
+#: only when the corpus has never seen it, so a real camera name is safe.
+INTENT_VOCAB = frozenset(
+    w for keywords, _ in INTENTS for k in keywords
+    for w in retrieval.tokenise(k))
+
 # LLM_HINT: to support free-form phrasing, classify the question to one of the
 # intent names above with a model and dispatch here. The handlers must remain
 # the only source of facts - the model chooses the query, never the answer.
 
 
 def route(question: str):
     """Which handler a question resolves to, or None for "I cannot answer".
 
     Split out from `ask` so routing can be checked without running a handler,
     and therefore without a database: a wrong route is the failure mode that
@@ -562,20 +581,27 @@ def ask(question: str) -> dict:
     handler = route(question)
     if handler is not None:
         # A question that names an entity gets an answer about that entity.
         # `_scoped` returns None whenever the mention is not confident enough,
         # and the estate-wide handler then runs exactly as it always did.
         scoped = _scoped(question, handler)
         if scoped is not None:
             return scoped
         return handler(question)
 
+    # No intent keyword at all, but an operator who types a bare "cam11" or
+    # "majewadi" has named something precisely. Resolution is tried last, so
+    # it can never divert a question an intent already claimed, and it still
+    # declines when nothing resolves.
+    if retrieval.resolve(question, limit=1, ignore=INTENT_VOCAB):
+        return _search(question)
+
     return _answer(
         "I could not match that to anything I can answer from platform data. "
         "Ask about camera health, detections, alerts, the watchlist, pipeline "
         "status, coverage, cloned plates, or a specific registration number.",
         {}, _help("")["actions"])
 
 
 def _self_check() -> None:
     """Routing decides which query runs; a wrong route gives a confident wrong
     answer, so the mapping is worth pinning down."""
@@ -633,19 +659,47 @@ def _self_check() -> None:
     # without an id that the index actually holds.
     assert set(_FACTS) == {"camera", "zone", "watchlist"}
 
     # The honesty constraint on a scoped answer: it must name the entity and
     # mark it as inferred, so a wrong substitution is visible.
     note = _resolution_note(
         retrieval.EntityMatch("camera", "cam06", "Timbavadi gate", 3.1))
     assert "cam06" in note and "Timbavadi gate" in note, note
     assert "inferred" in note and "not confirmed" in note, note
 
+    # The words an intent routes on must never be counted against the mention
+    # standing beside them. Checked on a synthetic corpus, so no database.
+    assert {"down", "health", "coverage", "faulty", "status"} <= INTENT_VOCAB
+    idx = retrieval.build_index(retrieval._SYNTHETIC)
+
+    def _res(q):
+        return idx.resolve(q, ignore=INTENT_VOCAB)
+
+    # A bare id, and an id buried in intent words, are the least ambiguous
+    # things an operator can type and must be the most reliable path.
+    for q in ("GJ-JUN-004", "is GJ-JUN-004 down", "camera health GJ-JUN-004",
+              "what is the status of GJ-JUN-004"):
+        m = _res(q)
+        assert m and m[0].id == "GJ-JUN-004", (q, m)
+
+    # A place name mixed with intent words resolves just as well.
+    for q, want in (("is the junagadh bypass camera down", "GJ-JUN-004"),
+                    ("camera health for rajkot ring road", "GJ-RAJ-002"),
+                    ("coverage in surat", "GJ-SUR-009")):
+        m = _res(q)
+        assert m and m[0].id == want, (q, m)
+
+    # ...and none of that opens a route to a spurious scope.
+    for q in ("the weather tomorrow", "banana", "xyzzy", "please",
+              "the camera", "a zone", "show me", "which cameras are down?",
+              "how many detections", "is the pipeline running"):
+        assert _res(q) == [], (q, _res(q))
+
     # Unknown questions must decline rather than invent an answer.
     r = ask("what is the weather in Ahmedabad tomorrow")
     assert "could not match" in r["answer"], r
 
     print("assistant self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/api/retrieval.py b/netra/api/retrieval.py
index a969382..8409d6a 100644
--- a/netra/api/retrieval.py
+++ b/netra/api/retrieval.py
@@ -25,20 +25,21 @@ plus a character fallback already covers on a corpus of a few hundred rows.
 
 ponytail: the index is rebuilt wholesale behind a short TTL rather than
 maintained incrementally. The registry, zone rules and watchlist together are
 hundreds of rows, so a rebuild is a few milliseconds of pure Python. The
 ceiling is roughly a five-figure corpus, at which point the rebuild starts to
 be felt on the request that triggers it and an incremental index earns its
 complexity.
 """
 from __future__ import annotations
 
+import logging
 import math
 import re
 import time
 from dataclasses import dataclass, field
 
 #: BM25 term-frequency saturation and length normalisation. The standard
 #: values; nothing about this corpus argues for tuning them.
 K1 = 1.5
 B = 0.75
 
@@ -85,20 +86,22 @@ MIN_TRIGRAM_CONTAINMENT = 0.7
 
 #: Below this many characters a trigram comparison is noise - one or two
 #: trigrams will coincide with something in any corpus of a few hundred labels.
 #: Four is the shortest word an operator actually shortens a camera to ("toll"
 #: for "Tollnaka"), and at four the containment threshold demands every trigram
 #: match, which is strict enough to stay safe.
 MIN_TRIGRAM_QUERY_LEN = 4
 
 _TOKEN_RE = re.compile(r"[a-z0-9]+")
 
+log = logging.getLogger(__name__)
+
 
 def tokenise(text: str) -> list[str]:
     """Lowercase, split on anything that is not a letter or digit.
 
     Camera ids look like `GJ-AHM-014` and plates like `GJ01AB1234`, so the
     split has to treat punctuation as a separator while keeping digits as part
     of the token: "ahm" and "014" are both worth matching on.
     """
     return _TOKEN_RE.findall((text or "").lower())
 
@@ -203,54 +206,73 @@ class EntityIndex:
         total = 0.0
         for t in query_tokens:
             f = doc.tf.get(t, 0)
             if not f:
                 continue
             total += self._idf(t) * (f * (K1 + 1)) / (f + norm)
         return total
 
     # -- resolution ---------------------------------------------------------
 
-    def resolve(self, query: str, kind: str | None = None,
-                limit: int = 5) -> list[EntityMatch]:
+    def resolve(self, query: str, kind: str | None = None, limit: int = 5,
+                ignore: frozenset[str] | set[str] | None = None
+                ) -> list[EntityMatch]:
         """Entities the query plausibly names, best first, or an empty list.
 
         Returning nothing is a valid and frequent answer. A top hit that
         explains little of the query is not a resolution, it is the corpus's
         least bad row, and handing it back would let the assistant answer about
         an entity the operator never mentioned.
+
+        `ignore` is the caller's own intent vocabulary - the words that told it
+        *what* was being asked rather than *of what*. Unmatched, those words
+        are information the query supplied that no entity accounts for, so they
+        drive coverage down and sink the mention beside them: "is cam11 down"
+        would resolve nothing while "cam11" resolves cleanly. An ignored word
+        is dropped only when the corpus has never seen it, so a camera actually
+        called "Highway Junction" is never made unfindable by a word that also
+        happens to be an intent keyword.
         """
         q = _content_tokens(query)
+        if ignore:
+            q = [t for t in q if t not in ignore or t in self.df]
         if not q or not self.docs:
             return []
         pool = [d for d in self.docs if kind is None or d.kind == kind]
         if not pool:
             return []
 
         # Denominator includes tokens absent from the corpus, so a query that
         # is mostly unknown words cannot reach coverage on the one word it
         # happens to share with a label.
         want = {t: self._idf(t) for t in set(q)}
         total_idf = sum(want.values()) or 1.0
 
-        hits: list[EntityMatch] = []
+        qset = set(q)
+        hits: list[tuple[int, EntityMatch]] = []
         for doc in pool:
             matched = sum(w for t, w in want.items() if t in doc.tf)
             if matched < MIN_EVIDENCE or matched / total_idf < MIN_COVERAGE:
                 continue
-            hits.append(EntityMatch(doc.kind, doc.id, doc.label,
-                                    self.score(q, doc), "bm25"))
+            # An operator who types an entity's own id means that entity, not
+            # a document that merely mentions it - a zone rule names the camera
+            # it sits on, and being short, outscores that camera on the
+            # camera's own id. Identity beats term statistics.
+            own = set(tokenise(doc.id))
+            named = 1 if own and own <= qset else 0
+            hits.append((named, EntityMatch(doc.kind, doc.id, doc.label,
+                                            self.score(q, doc), "bm25")))
         if hits:
-            hits.sort(key=lambda m: (-m.score, m.id))
-            return hits[:limit]
+            hits.sort(key=lambda h: (-h[0], -h[1].score, h[1].id))
+            return [m for _, m in hits[:limit]]
 
-        return self._trigram_fallback(query, pool, limit)
+        return self._trigram_fallback(" ".join(q), pool, limit)
 
     def _trigram_fallback(self, query: str, pool: list[_Doc],
                           limit: int) -> list[EntityMatch]:
         """Character-level rescue for a misspelling that shares no token.
 
         BM25 is exact-token: "junagad" and "junagadh" are different terms and
         score zero against each other, so the single most common operator error
         - a dropped or transposed letter in a place name - defeats it entirely.
         Comparing normalised trigrams recovers that case without introducing a
         semantic model, and is only consulted when token matching found nothing,
@@ -330,28 +352,35 @@ def get_index(force: bool = False) -> EntityIndex:
     """The shared index, rebuilt when the TTL has expired."""
     now = time.monotonic()
     idx = _CACHE.get("index")
     if force or idx is None or now - float(_CACHE["at"]) > TTL_S:
         idx = build_index()
         _CACHE["index"] = idx
         _CACHE["at"] = now
     return idx  # type: ignore[return-value]
 
 
-def resolve(query: str, kind: str | None = None,
-            limit: int = 5) -> list[EntityMatch]:
+def resolve(query: str, kind: str | None = None, limit: int = 5,
+            ignore: frozenset[str] | set[str] | None = None
+            ) -> list[EntityMatch]:
     """Resolve a fuzzy mention against live platform entities."""
     try:
-        return get_index().resolve(query, kind=kind, limit=limit)
-    except Exception:
+        return get_index().resolve(query, kind=kind, limit=limit,
+                                   ignore=ignore)
+    except Exception as exc:
         # Resolution is an assist, never a fact. If the registry cannot be
-        # read the assistant must still answer from SQL rather than fail.
+        # read the assistant must still answer from SQL rather than fail - but
+        # a schema or connection fault that silently costs every operator
+        # their fuzzy lookups has to be findable in the log, not inferred from
+        # the feature quietly never working.
+        log.warning("entity resolution unavailable, answering unscoped: %r",
+                    exc)
         return []
 
 
 # -- self-check --------------------------------------------------------------
 
 _SYNTHETIC = [
     ("camera", "GJ-JUN-004", "Junagadh Bypass ANPR",
      "GJ-JUN-004 Junagadh Bypass ANPR Junagadh Junagadh anpr"),
     ("camera", "GJ-AHM-014", "Ahmedabad SG Highway Tollnaka",
      "GJ-AHM-014 Ahmedabad SG Highway Tollnaka Ahmedabad Ahmedabad anpr"),
@@ -428,19 +457,46 @@ def _self_check() -> None:
     # A short fragment of a longer word resolves, because an operator says
     # "toll" and the registry says "Tollnaka".
     m = idx.resolve("toll")
     assert m and m[0].id == "GJ-AHM-014" and m[0].via == "trigram", m
 
     # The fallback is consulted only when token matching found nothing, so it
     # can never displace a genuine lexical hit.
     m = idx.resolve("surat ring road")
     assert m and m[0].via == "bm25" and m[0].id == "GJ-SUR-009", m
 
+    # Intent vocabulary must not sink the mention beside it. "down", "health"
+    # and "coverage" appear in no camera name, so unfiltered they carry maximum
+    # idf into the denominator and defeat a perfectly clear reference.
+    intent = frozenset({"down", "health", "coverage", "faulty", "status",
+                        "detections", "how", "many", "is", "in", "for"})
+    m = idx.resolve("is GJ-JUN-004 down", ignore=intent)
+    assert m and m[0].id == "GJ-JUN-004", m
+    m = idx.resolve("camera health for junagadh bypass", ignore=intent)
+    assert m and m[0].id == "GJ-JUN-004", m
+    m = idx.resolve("coverage in rajkot", ignore=intent)
+    assert m and m[0].id == "GJ-RAJ-002", m
+    m = idx.resolve("how many detections on junagad", ignore=intent)
+    assert m and m[0].id == "GJ-JUN-004" and m[0].via == "trigram", m
+
+    # ...and dropping intent words must not open a route to a spurious match.
+    for junk in ("the weather tomorrow", "banana", "xyzzy", "please",
+                 "the camera", "a zone", "show me", "how many detections",
+                 "is it down", "status"):
+        assert idx.resolve(junk, ignore=intent) == [], (
+            junk, idx.resolve(junk, ignore=intent))
+
+    # An ignored word the corpus does know is still matchable, so a camera is
+    # never made unfindable by a word that is also an intent keyword.
+    assert "rajkot" not in intent
+    m = idx.resolve("north ring road", ignore=frozenset({"north"}))
+    assert m and m[0].id in ("GJ-RAJ-002", "GJ-SUR-009"), m
+
     # Normalisation is what makes the character fallback work at all.
     assert normalise("Junagadh-Bypass ANPR") == "junagadhbypassanpr"
     assert tokenise("GJ-AHM-014") == ["gj", "ahm", "014"]
 
     print("retrieval self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
