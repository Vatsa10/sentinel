# Review package — Task 7

016f82f Resolve fuzzy entity mentions before the assistant queries

 netra/api/assistant.py | 249 ++++++++++++++++++++++++++-
 netra/api/retrieval.py | 446 +++++++++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 692 insertions(+), 3 deletions(-)

diff --git a/netra/api/assistant.py b/netra/api/assistant.py
index e2ea67e..b794346 100644
--- a/netra/api/assistant.py
+++ b/netra/api/assistant.py
@@ -2,36 +2,44 @@
 
 Answers operational questions against live platform state - camera health,
 detections, alerts, watchlist, vehicle traces - so an operator can ask in
 plain language instead of navigating to the right screen and filtering it.
 
 Every answer is produced from a database query and carries the records it was
 derived from. Nothing is generated or inferred: in a policing context an
 assistant that invents a plausible-sounding number is worse than no assistant,
 so unrecognised questions say so and list what can be asked instead.
 
+Entity mentions are resolved lexically (`netra/api/retrieval.py`) before the
+query runs, so "the junagad bypass camera" reaches the right registry row. That
+resolution decides *which* row is queried and never what the row contains, and
+whenever it narrows an answer the answer says which entity was inferred - an
+operator who meant a different camera has to be able to see the substitution.
+
 ponytail: intent matching on keywords rather than a language model. It needs no
 API key, no network call and no per-query cost, and the question space in a
 control room is small and repetitive. `LLM_HINT` below marks where a model
 would slot in if free-form phrasing is ever needed.
 """
 from __future__ import annotations
 
 import re
 from datetime import datetime, timedelta, timezone
 
 from sqlalchemy import func
 from sqlalchemy.orm import joinedload
 
+from netra.api import retrieval
 from netra.core.db import SessionLocal
 from netra.core.geo import TIME_GROUPS
-from netra.core.models import Alert, Camera, Detection, WatchlistEntry
+from netra.core.models import (Alert, Camera, Detection, WatchlistEntry,
+                               ZoneEventRow, ZoneRule)
 
 PLATE_RE = re.compile(r"\b([A-Z]{2}\s?\d{1,2}\s?[A-Z]{0,3}\s?\d{3,4})\b", re.I)
 
 
 def _answer(text: str, data=None, actions=None) -> dict:
     return {"answer": text, "data": data or {}, "actions": actions or []}
 
 
 # -- intents ----------------------------------------------------------------
 
@@ -271,36 +279,241 @@ def _unusual(_q: str) -> dict:
     lead = "; ".join(a.explanation for a in flagged[:3])
     text = (f"{len(flagged)} of {len(latest)} cameras are outside their normal "
             f"range for this hour of the day. {lead}")
     if thin:
         text += (f" A further {len(thin)} camera(s) have too little history "
                  f"({baseline.MIN_SAMPLES} observations required) for any "
                  f"judgement to be honest.")
     return _answer(text, data, actions)
 
 
+# -- entity resolution ------------------------------------------------------
+#
+# Everything below still reads its facts from SQL. Resolution only chooses
+# which id the SQL runs against; it never contributes a number.
+
+def _resolution_note(m: retrieval.EntityMatch) -> str:
+    """The sentence that keeps a scoped answer honest.
+
+    A narrowed answer is only trustworthy if the operator can see what it was
+    narrowed to. It is never phrased as a certainty: the match is a guess about
+    intent, and the wording has to invite correction.
+    """
+    how = ("closest spelling in the registry" if m.via == "trigram"
+           else "closest name match")
+    return (f"I took that to mean {m.kind} {m.id} ({m.label}) - {how}, "
+            f"inferred from your wording and not confirmed. Name the id "
+            f"directly if you meant a different one.")
+
+
+def _camera_facts(camera_id: str):
+    """Everything the database knows about one camera. Nothing inferred."""
+    with SessionLocal() as db:
+        cam = db.get(Camera, camera_id)
+        if cam is None:
+            return None
+        total = db.query(func.count(Detection.id)).filter(
+            Detection.camera_id == camera_id).scalar() or 0
+        plates = db.query(func.count(Detection.id)).filter(
+            Detection.camera_id == camera_id,
+            Detection.plate_text.isnot(None)).scalar() or 0
+        alerts = db.query(func.count(Alert.id)).filter(
+            Alert.camera_id == camera_id).scalar() or 0
+        last = (db.query(func.max(Detection.wall_time))
+                .filter(Detection.camera_id == camera_id).scalar())
+        zones = db.query(ZoneRule).filter(
+            ZoneRule.camera_id == camera_id).all()
+        zone_events = db.query(func.count(ZoneEventRow.id)).filter(
+            ZoneEventRow.camera_id == camera_id).scalar() or 0
+        data = {"id": cam.id, "name": cam.name, "city": cam.city,
+                "district": cam.district, "capability": cam.capability,
+                "capability_note": cam.capability_note, "health": cam.health,
+                "enabled": cam.enabled, "detections": total,
+                "with_plate": plates, "alerts": alerts,
+                "zone_rules": [{"id": z.id, "name": z.name, "rule": z.rule}
+                               for z in zones],
+                "zone_events": zone_events,
+                "last_detection_at": last.isoformat() if last else None}
+        text = (f"Camera {cam.id} - {cam.name} "
+                f"({cam.city or 'location unknown'}). Capability "
+                f"{cam.capability}, health {cam.health}. {total} detections "
+                f"recorded, {plates} with a readable plate, {alerts} watchlist "
+                f"alerts.")
+        if zones:
+            text += (f" {len(zones)} zone rule(s) configured, {zone_events} "
+                     f"event(s) triggered.")
+        if cam.capability == "degraded":
+            text += (f" It cannot deliver analytics: "
+                     f"{cam.capability_note or 'reason unspecified'}.")
+        if not total:
+            text += " Nothing has been detected on it yet."
+    return text, data
+
+
+def _zone_facts(zone_id: str):
+    with SessionLocal() as db:
+        z = db.get(ZoneRule, int(zone_id))
+        if z is None:
+            return None
+        fired = db.query(func.count(ZoneEventRow.id)).filter(
+            ZoneEventRow.zone_rule_id == z.id).scalar() or 0
+        last = (db.query(func.max(ZoneEventRow.at))
+                .filter(ZoneEventRow.zone_rule_id == z.id).scalar())
+        data = {"id": z.id, "name": z.name, "rule": z.rule,
+                "camera_id": z.camera_id, "severity": z.severity,
+                "active": z.active, "events": fired,
+                "last_event_at": last.isoformat() if last else None}
+        text = (f"Zone {z.id} - {z.name}: a {z.rule} rule on {z.camera_id}, "
+                f"severity {z.severity}, "
+                f"{'active' if z.active else 'inactive'}. {fired} event(s) "
+                f"triggered.")
+    return text, data
+
+
+def _watchlist_facts(entry_id: str):
+    with SessionLocal() as db:
+        e = db.get(WatchlistEntry, int(entry_id))
+        if e is None:
+            return None
+        seen = db.query(func.count(Detection.id)).filter(
+            Detection.plate_text == e.plate).scalar() or 0
+        alerts = db.query(func.count(Alert.id)).filter(
+            Alert.watchlist_id == e.id).scalar() or 0
+        data = {"id": e.id, "plate": e.plate, "category": e.category,
+                "severity": e.severity, "case_ref": e.case_ref,
+                "active": e.active, "sightings": seen, "alerts": alerts}
+        case = f", case {e.case_ref}" if e.case_ref else ""
+        text = (f"Watchlist entry {e.id} - {e.plate}, {e.category} "
+                f"({e.severity}){case}. {seen} sighting(s) recorded, "
+                f"{alerts} alert(s) raised.")
+    return text, data
+
+
+_FACTS = {"camera": _camera_facts, "zone": _zone_facts,
+          "watchlist": _watchlist_facts}
+
+
+def _entity_action(m: retrieval.EntityMatch) -> dict:
+    if m.kind == "camera":
+        return {"label": f"Open {m.id}", "view": "registry", "query": m.id}
+    if m.kind == "zone":
+        return {"label": "Open zones", "view": "zones"}
+    return {"label": f"Trace {m.label}", "view": "route", "query": m.label}
+
+
+def _search(q: str) -> dict:
+    """Free-text lookup: resolve what was named, then state the SQL facts.
+
+    Two steps that never blur into one. Retrieval produces ids and a ranking;
+    every figure in the answer comes from a query against those ids.
+    """
+    query = re.sub(r"^\s*(search|look\s*(up|for)|lookup)\b(\s+for)?[:\s]*",
+                   "", q or "", flags=re.I).strip() or (q or "")
+    matches = retrieval.resolve(query, limit=5)
+    if not matches:
+        return _answer(
+            f"Nothing in the camera registry, the zone rules or the watchlist "
+            f"matches '{query}' closely enough for me to be sure what you "
+            f"meant, and guessing would be worse than saying so. A camera id, "
+            f"a place name or a registration number will find it.",
+            {"query": query, "matches": []}, _help("")["actions"])
+
+    found, lines = [], []
+    for m in matches:
+        got = _FACTS[m.kind](m.id)
+        if got is None:
+            # The index is TTL-cached, so a row can be deleted between build
+            # and query. Dropping it is right: an entity whose facts cannot be
+            # read is one that must not be described.
+            continue
+        text, data = got
+        lines.append(text)
+        found.append({**m.as_dict(), "facts": data})
+
+    if not found:
+        return _answer(
+            f"'{query}' matched entries that are no longer in the database. "
+            f"Ask again in a moment.",
+            {"query": query, "matches": []}, _help("")["actions"])
+
+    head = (f"{len(found)} match(es) for '{query}'. The ranking is inferred "
+            f"from how closely the wording matches; every figure below is read "
+            f"from the database. ")
+    return _answer(head + " ".join(lines), {"query": query, "matches": found},
+                   [_entity_action(matches[0])])
+
+
+#: How far ahead of the runner-up a match must be before an estate-wide answer
+#: is replaced by a single-entity one. Five Ahmedabad cameras score almost
+#: identically on "the Ahmedabad camera": narrowing to whichever won by a
+#: rounding error would silently hide four of them, so an ambiguous mention
+#: falls through to the broad answer instead.
+SCOPE_MARGIN = 0.9
+
+#: Intents whose SQL is worth narrowing when the question names one entity.
+#: Deliberately short: an intent is listed only where a per-entity answer is
+#: strictly more useful than the estate-wide one it would replace. The plate
+#: trace and the clone search are absent on purpose - both already scope
+#: themselves, from a registration number the operator typed exactly.
+_SCOPABLE = (_camera_health, _coverage)
+
+
+def _scoped(question: str, handler):
+    """A per-entity answer when the question confidently names an entity.
+
+    ponytail: one entity, chosen by margin over the runner-up. A question that
+    names two ("compare cam06 and cam08") is answered about neither, falling
+    through to the estate-wide handler - which is the safe direction to fail,
+    but its ceiling is comparative questions, which need the resolver to return
+    a set and the handlers to accept one.
+    """
+    if handler not in _SCOPABLE:
+        return None
+    matches = (retrieval.resolve(question, kind="camera", limit=2) or
+               retrieval.resolve(question, kind="zone", limit=2))
+    if not matches:
+        return None
+    if len(matches) > 1 and matches[1].score >= SCOPE_MARGIN * matches[0].score:
+        return None
+    m = matches[0]
+    got = _FACTS[m.kind](m.id)
+    if got is None:
+        return None
+    text, data = got
+    return _answer(f"{_resolution_note(m)} {text}",
+                   {"resolved": m.as_dict(), "facts": data},
+                   [_entity_action(m)])
+
+
 def _help(_q: str) -> dict:
     return _answer(
         "I answer from live platform data. You can ask about camera health and "
         "which cameras are faulty, detection counts, current alerts, the "
         "watchlist, pipeline status, coverage by location, whether any plates look "
         "cloned, whether anything looks unusual against each camera's normal "
-        "traffic, or where a specific registration number has been seen.",
+        "traffic, or where a specific registration number has been seen. You "
+        "can also name a camera, a place or a case reference loosely - 'look "
+        "up the junagadh bypass' - and I will say which entry I took you to "
+        "mean before answering about it.",
         {}, [{"label": "Camera health", "query": "which cameras are down"},
              {"label": "Current alerts", "query": "show me the alerts"},
              {"label": "Detections", "query": "how many detections"},
              {"label": "Anything unusual", "query": "anything unusual?"}])
 
 
 # Ordered: the first intent whose keywords appear wins, so specific
 # intents must precede general ones.
 INTENTS = [
+    # First: an explicit lookup is a request to resolve a name, and its
+    # phrasing ("search for the toll camera") contains words that the camera
+    # and coverage intents would otherwise claim.
+    (("search", "look up", "lookup", "look for"), _search),
     # Ahead of everything else: an operator phrases this question with words
     # that later intents already claim - "which camera is busier than normal"
     # contains "camera", "where is it unusual" contains "where" - so placed
     # lower it would be answered by camera health or the plate trace instead.
     (("unusual", "abnormal", "anomaly", "anomalies", "out of the ordinary",
       "baseline", "baselines", "spike", "quieter than", "busier than"), _unusual),
     # Ahead of the trace intent because "find cloned plates" contains "find";
     # a question naming an actual registration number never reaches here, as
     # `ask` routes those to the trace handler before the keyword loop runs.
     (("clone", "cloned", "cloning", "forged", "forgery", "duplicate plate",
@@ -334,23 +547,33 @@ def route(question: str):
         # A registration number anywhere in the question is unambiguous intent.
         return _find_plate
     q = question.lower().strip()
     for keywords, handler in INTENTS:
         if any(k in q for k in keywords):
             return handler
     return None
 
 
 def ask(question: str) -> dict:
-    """Route a question to a handler and return a grounded answer."""
+    """Route a question to a handler and return a grounded answer.
+
+    Entity resolution happens here rather than in `route`, so routing stays a
+    pure function of the text and can be asserted without a database.
+    """
     handler = route(question)
     if handler is not None:
+        # A question that names an entity gets an answer about that entity.
+        # `_scoped` returns None whenever the mention is not confident enough,
+        # and the estate-wide handler then runs exactly as it always did.
+        scoped = _scoped(question, handler)
+        if scoped is not None:
+            return scoped
         return handler(question)
 
     return _answer(
         "I could not match that to anything I can answer from platform data. "
         "Ask about camera health, detections, alerts, the watchlist, pipeline "
         "status, coverage, cloned plates, or a specific registration number.",
         {}, _help("")["actions"])
 
 
 def _self_check() -> None:
@@ -390,19 +613,39 @@ def _self_check() -> None:
     # ...and the reverse direction: the new intent must not steal questions
     # belonging to the handlers that were already there.
     assert route("which cameras are down?") is _camera_health
     assert route("where has GJ01AB1234 been seen?") is _find_plate
     assert route("find cloned plates") is _cloned_plates
     assert route("show me the alerts") is _alert_summary
     assert route("how many detections") is _detection_summary
     assert route("is the pipeline running") is _pipeline_status
     assert route("what is the weather in Ahmedabad tomorrow") is None
 
+    # The lookup intent must not be stolen by, nor steal from, the intents
+    # that were already there. Routing only, so no database is involved.
+    for q in ("search for the junagadh bypass", "look up cam06",
+              "lookup GJ-AHM-014"):
+        assert route(q) is _search, (q, route(q))
+    assert route("which cameras are down?") is _camera_health
+    assert route("how many detections") is _detection_summary
+
+    # Resolution decides which row is queried, never what it contains: every
+    # fact function reads from the database and none of them is reachable
+    # without an id that the index actually holds.
+    assert set(_FACTS) == {"camera", "zone", "watchlist"}
+
+    # The honesty constraint on a scoped answer: it must name the entity and
+    # mark it as inferred, so a wrong substitution is visible.
+    note = _resolution_note(
+        retrieval.EntityMatch("camera", "cam06", "Timbavadi gate", 3.1))
+    assert "cam06" in note and "Timbavadi gate" in note, note
+    assert "inferred" in note and "not confirmed" in note, note
+
     # Unknown questions must decline rather than invent an answer.
     r = ask("what is the weather in Ahmedabad tomorrow")
     assert "could not match" in r["answer"], r
 
     print("assistant self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/api/retrieval.py b/netra/api/retrieval.py
new file mode 100644
index 0000000..a969382
--- /dev/null
+++ b/netra/api/retrieval.py
@@ -0,0 +1,446 @@
+"""Lexical entity resolution for the control-room assistant.
+
+The assistant answers only from SQL, which is what stops it inventing a count.
+That guarantee costs it flexibility: an operator who types "the Junagadh bypass
+camera", "junagad", or "that toll camera" is naming a real row in the registry,
+but no keyword rule in `assistant.py` knows which one.
+
+This module closes that gap and nothing else. The division is deliberate:
+
+    SQL     owns every fact - counts, timestamps, plates, statuses
+    BM25    owns resolving a fuzzy mention to an entity id, and never a fact
+    vector  owns appearance similarity between vehicles (`analytics/reid.py`)
+
+So BM25 decides *what the operator meant*; SQL still decides *what is true*. A
+match here produces an id and a label, never a number, so a wrong resolution can
+mislead an operator about which camera they are looking at but can never put a
+fabricated figure in front of them - and the assistant is required to say out
+loud which entity it inferred, so the substitution is visible and correctable.
+
+There is deliberately no embedding search over text. The vector half of the
+hybrid already exists as appearance re-identification; a second, semantic,
+text index would add a way for the assistant to be confidently wrong about
+which camera an operator meant, in exchange for phrasing tolerance that BM25
+plus a character fallback already covers on a corpus of a few hundred rows.
+
+ponytail: the index is rebuilt wholesale behind a short TTL rather than
+maintained incrementally. The registry, zone rules and watchlist together are
+hundreds of rows, so a rebuild is a few milliseconds of pure Python. The
+ceiling is roughly a five-figure corpus, at which point the rebuild starts to
+be felt on the request that triggers it and an incremental index earns its
+complexity.
+"""
+from __future__ import annotations
+
+import math
+import re
+import time
+from dataclasses import dataclass, field
+
+#: BM25 term-frequency saturation and length normalisation. The standard
+#: values; nothing about this corpus argues for tuning them.
+K1 = 1.5
+B = 0.75
+
+#: How long a built index is trusted before a rebuild. Short enough that a
+#: camera renamed or a watchlist entry added is findable almost immediately,
+#: long enough that a burst of questions does not rebuild per question.
+TTL_S = 60.0
+
+#: Words that carry no identifying information in this corpus. Kept tiny on
+#: purpose: a long stop list silently removes the very token that distinguishes
+#: two entities. These are only the ones an operator uses as scaffolding.
+STOPWORDS = frozenset({
+    "the", "a", "an", "of", "at", "on", "in", "to", "for", "and", "or",
+    "is", "are", "was", "were", "be", "that", "this", "it", "its",
+    "me", "my", "show", "tell", "give", "please", "what", "which", "who",
+    "any", "all", "from", "with", "by", "about",
+    # Domain scaffolding: an operator says "the toll camera" to name what kind
+    # of thing they mean, never which one. Left in, these words are unmatched
+    # information that drags a perfectly good partial name below the coverage
+    # floor - and matched, they would match every row equally.
+    "camera", "cameras", "cctv", "feed", "feeds", "footage", "cam",
+})
+
+#: Share of the query's information (measured as idf mass, so a rare word
+#: counts for more than a common one) that a document must account for before
+#: the match is called a resolution. Below this the top hit is merely the least
+#: bad row in the corpus, and returning it would be the assistant guessing.
+#: Half is the defensible line: the match must explain more of what was typed
+#: than it leaves unexplained.
+MIN_COVERAGE = 0.5
+
+#: Absolute floor on the idf mass actually matched, in nats. Coverage alone is
+#: a ratio and so is satisfiable by a query made entirely of common words -
+#: "the camera" would match every camera at coverage 1.0. Requiring real
+#: information to have been matched means a hit on generic vocabulary is never
+#: a resolution. One nat is roughly a term appearing in a third of the corpus.
+MIN_EVIDENCE = 1.0
+
+#: Character-fallback threshold: the share of the query's trigrams that must
+#: appear in the label. Containment rather than a symmetric measure because the
+#: query is a fragment ("junagad") and the label is longer ("Junagadh Bypass
+#: ANPR"); a symmetric score would punish the label for its extra words.
+MIN_TRIGRAM_CONTAINMENT = 0.7
+
+#: Below this many characters a trigram comparison is noise - one or two
+#: trigrams will coincide with something in any corpus of a few hundred labels.
+#: Four is the shortest word an operator actually shortens a camera to ("toll"
+#: for "Tollnaka"), and at four the containment threshold demands every trigram
+#: match, which is strict enough to stay safe.
+MIN_TRIGRAM_QUERY_LEN = 4
+
+_TOKEN_RE = re.compile(r"[a-z0-9]+")
+
+
+def tokenise(text: str) -> list[str]:
+    """Lowercase, split on anything that is not a letter or digit.
+
+    Camera ids look like `GJ-AHM-014` and plates like `GJ01AB1234`, so the
+    split has to treat punctuation as a separator while keeping digits as part
+    of the token: "ahm" and "014" are both worth matching on.
+    """
+    return _TOKEN_RE.findall((text or "").lower())
+
+
+def _content_tokens(text: str) -> list[str]:
+    return [t for t in tokenise(text) if t not in STOPWORDS]
+
+
+def normalise(text: str) -> str:
+    """Strip to bare lowercase alphanumerics for character-level comparison.
+
+    Spacing and punctuation are exactly what differs between how an operator
+    types a name and how it is stored ("Junagadh Bypass" / "junagadh-bypass"),
+    so they must not be part of the comparison.
+    """
+    return "".join(_TOKEN_RE.findall((text or "").lower()))
+
+
+def _trigrams(text: str) -> set[str]:
+    n = normalise(text)
+    if len(n) < 3:
+        return {n} if n else set()
+    return {n[i:i + 3] for i in range(len(n) - 2)}
+
+
+@dataclass(frozen=True)
+class EntityMatch:
+    """One resolved entity. Carries no facts - an id and how sure we are."""
+    kind: str            # camera | zone | watchlist
+    id: str
+    label: str
+    score: float
+    #: Which mechanism resolved it, so the assistant can be honest about a
+    #: match that came from a spelling repair rather than a real token hit.
+    via: str = "bm25"
+
+    def as_dict(self) -> dict:
+        return {"kind": self.kind, "id": self.id, "label": self.label,
+                "score": round(self.score, 3), "via": self.via}
+
+
+@dataclass
+class _Doc:
+    kind: str
+    id: str
+    label: str
+    text: str
+    tokens: list[str] = field(default_factory=list)
+    tf: dict[str, int] = field(default_factory=dict)
+
+
+class EntityIndex:
+    """A BM25 index over the searchable text of registry-level entities."""
+
+    def __init__(self, docs: list[_Doc] | None = None):
+        self.docs: list[_Doc] = []
+        self.df: dict[str, int] = {}
+        self.avg_len = 0.0
+        self.built_at = time.monotonic()
+        for d in docs or []:
+            self.add(d.kind, d.id, d.label, d.text)
+        self.finalise()
+
+    # -- construction -------------------------------------------------------
+
+    def add(self, kind: str, entity_id: str, label: str, text: str) -> None:
+        toks = _content_tokens(text)
+        doc = _Doc(kind=kind, id=str(entity_id), label=label, text=text,
+                   tokens=toks)
+        for t in toks:
+            doc.tf[t] = doc.tf.get(t, 0) + 1
+        self.docs.append(doc)
+
+    def finalise(self) -> None:
+        self.df = {}
+        for doc in self.docs:
+            for t in set(doc.tokens):
+                self.df[t] = self.df.get(t, 0) + 1
+        total = sum(len(d.tokens) for d in self.docs)
+        self.avg_len = (total / len(self.docs)) if self.docs else 0.0
+        self.built_at = time.monotonic()
+
+    def __len__(self) -> int:
+        return len(self.docs)
+
+    # -- scoring ------------------------------------------------------------
+
+    def _idf(self, term: str) -> float:
+        """Robertson-Sparck-Jones idf, smoothed so it can never go negative.
+
+        An unseen term gets the maximum idf of the corpus: it is information
+        the operator supplied that no document accounts for, and coverage must
+        be penalised for that rather than quietly ignoring it.
+        """
+        n = len(self.docs) or 1
+        df = self.df.get(term, 0)
+        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))
+
+    def score(self, query_tokens: list[str], doc: _Doc) -> float:
+        dl = len(doc.tokens) or 1
+        norm = K1 * (1 - B + B * dl / (self.avg_len or 1.0))
+        total = 0.0
+        for t in query_tokens:
+            f = doc.tf.get(t, 0)
+            if not f:
+                continue
+            total += self._idf(t) * (f * (K1 + 1)) / (f + norm)
+        return total
+
+    # -- resolution ---------------------------------------------------------
+
+    def resolve(self, query: str, kind: str | None = None,
+                limit: int = 5) -> list[EntityMatch]:
+        """Entities the query plausibly names, best first, or an empty list.
+
+        Returning nothing is a valid and frequent answer. A top hit that
+        explains little of the query is not a resolution, it is the corpus's
+        least bad row, and handing it back would let the assistant answer about
+        an entity the operator never mentioned.
+        """
+        q = _content_tokens(query)
+        if not q or not self.docs:
+            return []
+        pool = [d for d in self.docs if kind is None or d.kind == kind]
+        if not pool:
+            return []
+
+        # Denominator includes tokens absent from the corpus, so a query that
+        # is mostly unknown words cannot reach coverage on the one word it
+        # happens to share with a label.
+        want = {t: self._idf(t) for t in set(q)}
+        total_idf = sum(want.values()) or 1.0
+
+        hits: list[EntityMatch] = []
+        for doc in pool:
+            matched = sum(w for t, w in want.items() if t in doc.tf)
+            if matched < MIN_EVIDENCE or matched / total_idf < MIN_COVERAGE:
+                continue
+            hits.append(EntityMatch(doc.kind, doc.id, doc.label,
+                                    self.score(q, doc), "bm25"))
+        if hits:
+            hits.sort(key=lambda m: (-m.score, m.id))
+            return hits[:limit]
+
+        return self._trigram_fallback(query, pool, limit)
+
+    def _trigram_fallback(self, query: str, pool: list[_Doc],
+                          limit: int) -> list[EntityMatch]:
+        """Character-level rescue for a misspelling that shares no token.
+
+        BM25 is exact-token: "junagad" and "junagadh" are different terms and
+        score zero against each other, so the single most common operator error
+        - a dropped or transposed letter in a place name - defeats it entirely.
+        Comparing normalised trigrams recovers that case without introducing a
+        semantic model, and is only consulted when token matching found nothing,
+        so it can never outrank a genuine lexical hit.
+        """
+        norm_q = normalise(query)
+        if len(norm_q) < MIN_TRIGRAM_QUERY_LEN:
+            return []
+        # The whole phrase, plus any individual word the index has never seen.
+        # Only an unknown word can be the misspelling this fallback exists for;
+        # trying known words individually would let a common one ("bypass")
+        # score a spurious 1.0 against every row that happens to contain it.
+        unknown = [normalise(t) for t in _content_tokens(query)
+                   if t not in self.df]
+        candidates = [norm_q] + unknown
+        candidates = [c for c in dict.fromkeys(candidates)
+                      if len(c) >= MIN_TRIGRAM_QUERY_LEN]
+        if not candidates:
+            return []
+
+        hits: list[EntityMatch] = []
+        for doc in pool:
+            doc_grams = _trigrams(doc.text)
+            best = 0.0
+            for cand in candidates:
+                grams = _trigrams(cand)
+                if not grams:
+                    continue
+                best = max(best, len(grams & doc_grams) / len(grams))
+            if best >= MIN_TRIGRAM_CONTAINMENT:
+                hits.append(EntityMatch(doc.kind, doc.id, doc.label, best,
+                                        "trigram"))
+        hits.sort(key=lambda m: (-m.score, m.id))
+        return hits[:limit]
+
+
+# -- the live index ----------------------------------------------------------
+
+_CACHE: dict[str, object] = {"index": None, "at": 0.0}
+
+
+def _rows_from_db() -> list[tuple[str, str, str, str]]:
+    """(kind, id, label, searchable text) for every resolvable entity."""
+    from netra.core.db import SessionLocal
+    from netra.core.models import Camera, WatchlistEntry, ZoneRule
+
+    out: list[tuple[str, str, str, str]] = []
+    with SessionLocal() as db:
+        for c in db.query(Camera).all():
+            # The id is part of the text because operators say "AHM 14" as
+            # often as they say the camera's name.
+            text = " ".join(x for x in (c.id, c.name, c.city, c.district,
+                                        c.capability) if x)
+            out.append(("camera", c.id, c.name or c.id, text))
+        for z in db.query(ZoneRule).all():
+            text = " ".join(x for x in (z.name, z.rule, z.camera_id) if x)
+            out.append(("zone", str(z.id), z.name or f"zone {z.id}", text))
+        for w in db.query(WatchlistEntry).all():
+            text = " ".join(x for x in (w.plate, w.case_ref, w.notes,
+                                        w.category, w.owner_name,
+                                        w.vehicle_make) if x)
+            out.append(("watchlist", str(w.id), w.plate, text))
+    return out
+
+
+def build_index(rows=None) -> EntityIndex:
+    """Build an index from the given rows, or from the database."""
+    idx = EntityIndex()
+    for kind, entity_id, label, text in (rows if rows is not None
+                                         else _rows_from_db()):
+        idx.add(kind, entity_id, label, text)
+    idx.finalise()
+    return idx
+
+
+def get_index(force: bool = False) -> EntityIndex:
+    """The shared index, rebuilt when the TTL has expired."""
+    now = time.monotonic()
+    idx = _CACHE.get("index")
+    if force or idx is None or now - float(_CACHE["at"]) > TTL_S:
+        idx = build_index()
+        _CACHE["index"] = idx
+        _CACHE["at"] = now
+    return idx  # type: ignore[return-value]
+
+
+def resolve(query: str, kind: str | None = None,
+            limit: int = 5) -> list[EntityMatch]:
+    """Resolve a fuzzy mention against live platform entities."""
+    try:
+        return get_index().resolve(query, kind=kind, limit=limit)
+    except Exception:
+        # Resolution is an assist, never a fact. If the registry cannot be
+        # read the assistant must still answer from SQL rather than fail.
+        return []
+
+
+# -- self-check --------------------------------------------------------------
+
+_SYNTHETIC = [
+    ("camera", "GJ-JUN-004", "Junagadh Bypass ANPR",
+     "GJ-JUN-004 Junagadh Bypass ANPR Junagadh Junagadh anpr"),
+    ("camera", "GJ-AHM-014", "Ahmedabad SG Highway Tollnaka",
+     "GJ-AHM-014 Ahmedabad SG Highway Tollnaka Ahmedabad Ahmedabad anpr"),
+    ("camera", "GJ-RAJ-002", "Rajkot Ring Road North",
+     "GJ-RAJ-002 Rajkot Ring Road North Rajkot Rajkot vehicle"),
+    ("camera", "GJ-SUR-009", "Surat Ring Road South",
+     "GJ-SUR-009 Surat Ring Road South Surat Surat degraded"),
+    ("zone", "3", "Bypass hard shoulder",
+     "Bypass hard shoulder intrusion GJ-JUN-004"),
+    ("watchlist", "7", "GJ01AB1234",
+     "GJ01AB1234 FIR-2026-118 reported stolen from Vadodara stolen"),
+]
+
+
+def _self_check() -> None:
+    """Resolution never touches the database here: the failure worth pinning
+    down is what the scorer does with a corpus, not what the registry holds."""
+    idx = build_index(_SYNTHETIC)
+    assert len(idx) == len(_SYNTHETIC)
+
+    # An exact name resolves, and resolves to the right row.
+    m = idx.resolve("Junagadh Bypass ANPR")
+    assert m and m[0].id == "GJ-JUN-004", m
+
+    # A partial name resolves.
+    m = idx.resolve("the Junagadh bypass camera")
+    assert m and m[0].id == "GJ-JUN-004", m
+
+    # A misspelling shares no token with the target, so BM25 alone scores it
+    # zero; the trigram fallback must recover it.
+    m = idx.resolve("junagad")
+    assert m and m[0].id == "GJ-JUN-004", m
+    assert m[0].via == "trigram", m
+
+    # An unrelated string must resolve to nothing rather than to whichever row
+    # happens to score least badly. This is the whole confidence floor.
+    for junk in ("what is the weather tomorrow", "quantum entanglement",
+                 "zzzzzzzz", "how many detections", ""):
+        assert idx.resolve(junk) == [], (junk, idx.resolve(junk))
+
+    # Generic corpus vocabulary is not evidence: every camera contains "camera"
+    # -ish words, so a query of nothing but those must resolve to nothing.
+    assert idx.resolve("the camera") == [], idx.resolve("the camera")
+
+    # BM25 ranks the better lexical match higher: both Ring Road cameras match
+    # "ring road", but only one is in Rajkot.
+    m = idx.resolve("rajkot ring road")
+    assert m and m[0].id == "GJ-RAJ-002", m
+    ids = [x.id for x in m]
+    assert "GJ-SUR-009" not in ids or ids.index("GJ-RAJ-002") < ids.index("GJ-SUR-009"), m
+
+    # Term saturation and idf together: a rare term must outweigh a repeated
+    # common one.
+    assert idx._idf("junagadh") > idx._idf("road")
+
+    # Kind filtering, and the promise that a resolution is always a real row.
+    known = {(k, i) for k, i, _, _ in _SYNTHETIC}
+    for q in ("junagadh bypass", "surat", "FIR-2026-118", "hard shoulder",
+              "GJ01AB1234", "toll", "ahmedabad sg highway"):
+        for hit in idx.resolve(q):
+            assert (hit.kind, hit.id) in known, (q, hit)
+
+    m = idx.resolve("bypass", kind="zone")
+    assert m and all(x.kind == "zone" for x in m), m
+    assert idx.resolve("junagadh bypass", kind="watchlist") == []
+
+    # A watchlist entry resolves by its case reference, not only its plate.
+    m = idx.resolve("FIR-2026-118")
+    assert m and m[0].kind == "watchlist" and m[0].id == "7", m
+
+    # An empty corpus must be answerable, not an exception.
+    assert build_index([]).resolve("anything") == []
+
+    # A short fragment of a longer word resolves, because an operator says
+    # "toll" and the registry says "Tollnaka".
+    m = idx.resolve("toll")
+    assert m and m[0].id == "GJ-AHM-014" and m[0].via == "trigram", m
+
+    # The fallback is consulted only when token matching found nothing, so it
+    # can never displace a genuine lexical hit.
+    m = idx.resolve("surat ring road")
+    assert m and m[0].via == "bm25" and m[0].id == "GJ-SUR-009", m
+
+    # Normalisation is what makes the character fallback work at all.
+    assert normalise("Junagadh-Bypass ANPR") == "junagadhbypassanpr"
+    assert tokenise("GJ-AHM-014") == ["gj", "ahm", "014"]
+
+    print("retrieval self-check passed")
+
+
+if __name__ == "__main__":
+    _self_check()
