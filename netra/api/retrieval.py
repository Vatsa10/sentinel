"""Lexical entity resolution for the control-room assistant.

The assistant answers only from SQL, which is what stops it inventing a count.
That guarantee costs it flexibility: an operator who types "the Junagadh bypass
camera", "junagad", or "that toll camera" is naming a real row in the registry,
but no keyword rule in `assistant.py` knows which one.

This module closes that gap and nothing else. The division is deliberate:

    SQL     owns every fact - counts, timestamps, plates, statuses
    BM25    owns resolving a fuzzy mention to an entity id, and never a fact
    vector  owns appearance similarity between vehicles (`analytics/reid.py`)

So BM25 decides *what the operator meant*; SQL still decides *what is true*. A
match here produces an id and a label, never a number, so a wrong resolution can
mislead an operator about which camera they are looking at but can never put a
fabricated figure in front of them - and the assistant is required to say out
loud which entity it inferred, so the substitution is visible and correctable.

There is deliberately no embedding search over text. The vector half of the
hybrid already exists as appearance re-identification; a second, semantic,
text index would add a way for the assistant to be confidently wrong about
which camera an operator meant, in exchange for phrasing tolerance that BM25
plus a character fallback already covers on a corpus of a few hundred rows.

ponytail: the index is rebuilt wholesale behind a short TTL rather than
maintained incrementally. The registry, zone rules and watchlist together are
hundreds of rows, so a rebuild is a few milliseconds of pure Python. The
ceiling is roughly a five-figure corpus, at which point the rebuild starts to
be felt on the request that triggers it and an incremental index earns its
complexity.
"""
from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass, field

#: BM25 term-frequency saturation and length normalisation. The standard
#: values; nothing about this corpus argues for tuning them.
K1 = 1.5
B = 0.75

#: How long a built index is trusted before a rebuild. Short enough that a
#: camera renamed or a watchlist entry added is findable almost immediately,
#: long enough that a burst of questions does not rebuild per question.
TTL_S = 60.0

#: Words that carry no identifying information in this corpus. Kept tiny on
#: purpose: a long stop list silently removes the very token that distinguishes
#: two entities. These are only the ones an operator uses as scaffolding.
STOPWORDS = frozenset({
    "the", "a", "an", "of", "at", "on", "in", "to", "for", "and", "or",
    "is", "are", "was", "were", "be", "that", "this", "it", "its",
    "me", "my", "show", "tell", "give", "please", "what", "which", "who",
    "any", "all", "from", "with", "by", "about",
    # Domain scaffolding: an operator says "the toll camera" to name what kind
    # of thing they mean, never which one. Left in, these words are unmatched
    # information that drags a perfectly good partial name below the coverage
    # floor - and matched, they would match every row equally.
    "camera", "cameras", "cctv", "feed", "feeds", "footage", "cam",
})

#: Share of the query's information (measured as idf mass, so a rare word
#: counts for more than a common one) that a document must account for before
#: the match is called a resolution. Below this the top hit is merely the least
#: bad row in the corpus, and returning it would be the assistant guessing.
#: Half is the defensible line: the match must explain more of what was typed
#: than it leaves unexplained.
MIN_COVERAGE = 0.5

#: Absolute floor on the idf mass actually matched, in nats. Coverage alone is
#: a ratio and so is satisfiable by a query made entirely of common words -
#: "the camera" would match every camera at coverage 1.0. Requiring real
#: information to have been matched means a hit on generic vocabulary is never
#: a resolution. One nat is roughly a term appearing in a third of the corpus.
MIN_EVIDENCE = 1.0

#: Character-fallback threshold: the share of the query's trigrams that must
#: appear in the label. Containment rather than a symmetric measure because the
#: query is a fragment ("junagad") and the label is longer ("Junagadh Bypass
#: ANPR"); a symmetric score would punish the label for its extra words.
MIN_TRIGRAM_CONTAINMENT = 0.7

#: Below this many characters a trigram comparison is noise - one or two
#: trigrams will coincide with something in any corpus of a few hundred labels.
#: Four is the shortest word an operator actually shortens a camera to ("toll"
#: for "Tollnaka"), and at four the containment threshold demands every trigram
#: match, which is strict enough to stay safe.
MIN_TRIGRAM_QUERY_LEN = 4

#: How many described vehicles the index carries, newest first. Cameras, zones
#: and the watchlist are a few hundred rows between them and are indexed whole;
#: descriptions accumulate one per described detection and would eventually
#: dominate a rebuild the TTL performs on an operator's own request. Newest
#: first because a search for a vehicle is nearly always a search for a recent
#: one. ponytail: its ceiling is exactly that - an older described vehicle is
#: unfindable by description, and stays findable only by camera, time or plate.
VEHICLE_INDEX_LIMIT = 5000

_TOKEN_RE = re.compile(r"[a-z0-9]+")

log = logging.getLogger(__name__)


def tokenise(text: str) -> list[str]:
    """Lowercase, split on anything that is not a letter or digit.

    Camera ids look like `GJ-AHM-014` and plates like `GJ01AB1234`, so the
    split has to treat punctuation as a separator while keeping digits as part
    of the token: "ahm" and "014" are both worth matching on.
    """
    return _TOKEN_RE.findall((text or "").lower())


def _content_tokens(text: str) -> list[str]:
    return [t for t in tokenise(text) if t not in STOPWORDS]


def normalise(text: str) -> str:
    """Strip to bare lowercase alphanumerics for character-level comparison.

    Spacing and punctuation are exactly what differs between how an operator
    types a name and how it is stored ("Junagadh Bypass" / "junagadh-bypass"),
    so they must not be part of the comparison.
    """
    return "".join(_TOKEN_RE.findall((text or "").lower()))


def _trigrams(text: str) -> set[str]:
    n = normalise(text)
    if len(n) < 3:
        return {n} if n else set()
    return {n[i:i + 3] for i in range(len(n) - 2)}


@dataclass(frozen=True)
class EntityMatch:
    """One resolved entity. Carries no facts - an id and how sure we are."""
    kind: str            # camera | zone | watchlist | vehicle
    id: str
    label: str
    score: float
    #: Which mechanism resolved it, so the assistant can be honest about a
    #: match that came from a spelling repair rather than a real token hit.
    via: str = "bm25"

    def as_dict(self) -> dict:
        return {"kind": self.kind, "id": self.id, "label": self.label,
                "score": round(self.score, 3), "via": self.via}


@dataclass
class _Doc:
    kind: str
    id: str
    label: str
    text: str
    tokens: list[str] = field(default_factory=list)
    tf: dict[str, int] = field(default_factory=dict)


class EntityIndex:
    """A BM25 index over the searchable text of registry-level entities."""

    def __init__(self, docs: list[_Doc] | None = None):
        self.docs: list[_Doc] = []
        self.df: dict[str, int] = {}
        self.avg_len = 0.0
        self.built_at = time.monotonic()
        for d in docs or []:
            self.add(d.kind, d.id, d.label, d.text)
        self.finalise()

    # -- construction -------------------------------------------------------

    def add(self, kind: str, entity_id: str, label: str, text: str) -> None:
        toks = _content_tokens(text)
        doc = _Doc(kind=kind, id=str(entity_id), label=label, text=text,
                   tokens=toks)
        for t in toks:
            doc.tf[t] = doc.tf.get(t, 0) + 1
        self.docs.append(doc)

    def finalise(self) -> None:
        self.df = {}
        for doc in self.docs:
            for t in set(doc.tokens):
                self.df[t] = self.df.get(t, 0) + 1
        total = sum(len(d.tokens) for d in self.docs)
        self.avg_len = (total / len(self.docs)) if self.docs else 0.0
        self.built_at = time.monotonic()

    def __len__(self) -> int:
        return len(self.docs)

    # -- scoring ------------------------------------------------------------

    def _idf(self, term: str) -> float:
        """Robertson-Sparck-Jones idf, smoothed so it can never go negative.

        An unseen term gets the maximum idf of the corpus: it is information
        the operator supplied that no document accounts for, and coverage must
        be penalised for that rather than quietly ignoring it.
        """
        n = len(self.docs) or 1
        df = self.df.get(term, 0)
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    def score(self, query_tokens: list[str], doc: _Doc) -> float:
        dl = len(doc.tokens) or 1
        norm = K1 * (1 - B + B * dl / (self.avg_len or 1.0))
        total = 0.0
        for t in query_tokens:
            f = doc.tf.get(t, 0)
            if not f:
                continue
            total += self._idf(t) * (f * (K1 + 1)) / (f + norm)
        return total

    # -- resolution ---------------------------------------------------------

    def resolve(self, query: str, kind: str | None = None, limit: int = 5,
                ignore: frozenset[str] | set[str] | None = None
                ) -> list[EntityMatch]:
        """Entities the query plausibly names, best first, or an empty list.

        Returning nothing is a valid and frequent answer. A top hit that
        explains little of the query is not a resolution, it is the corpus's
        least bad row, and handing it back would let the assistant answer about
        an entity the operator never mentioned.

        `ignore` is the caller's own intent vocabulary - the words that told it
        *what* was being asked rather than *of what*. Unmatched, those words
        are information the query supplied that no entity accounts for, so they
        drive coverage down and sink the mention beside them: "is cam11 down"
        would resolve nothing while "cam11" resolves cleanly. An ignored word
        is dropped only when the corpus has never seen it, so a camera actually
        called "Highway Junction" is never made unfindable by a word that also
        happens to be an intent keyword.
        """
        q = _content_tokens(query)
        if ignore:
            q = [t for t in q if t not in ignore or t in self.df]
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

        qset = set(q)
        hits: list[tuple[int, EntityMatch]] = []
        for doc in pool:
            matched = sum(w for t, w in want.items() if t in doc.tf)
            if matched < MIN_EVIDENCE or matched / total_idf < MIN_COVERAGE:
                continue
            # An operator who types an entity's own id means that entity, not
            # a document that merely mentions it - a zone rule names the camera
            # it sits on, and being short, outscores that camera on the
            # camera's own id. Identity beats term statistics.
            own = set(tokenise(doc.id))
            named = 1 if own and own <= qset else 0
            hits.append((named, EntityMatch(doc.kind, doc.id, doc.label,
                                            self.score(q, doc), "bm25")))
        if hits:
            hits.sort(key=lambda h: (-h[0], -h[1].score, h[1].id))
            return [m for _, m in hits[:limit]]

        return self._trigram_fallback(" ".join(q), pool, limit)

    def _trigram_fallback(self, query: str, pool: list[_Doc],
                          limit: int) -> list[EntityMatch]:
        """Character-level rescue for a misspelling that shares no token.

        BM25 is exact-token: "junagad" and "junagadh" are different terms and
        score zero against each other, so the single most common operator error
        - a dropped or transposed letter in a place name - defeats it entirely.
        Comparing normalised trigrams recovers that case without introducing a
        semantic model, and is only consulted when token matching found nothing,
        so it can never outrank a genuine lexical hit.
        """
        norm_q = normalise(query)
        if len(norm_q) < MIN_TRIGRAM_QUERY_LEN:
            return []
        # The whole phrase, plus any individual word the index has never seen.
        # Only an unknown word can be the misspelling this fallback exists for;
        # trying known words individually would let a common one ("bypass")
        # score a spurious 1.0 against every row that happens to contain it.
        unknown = [normalise(t) for t in _content_tokens(query)
                   if t not in self.df]
        candidates = [norm_q] + unknown
        candidates = [c for c in dict.fromkeys(candidates)
                      if len(c) >= MIN_TRIGRAM_QUERY_LEN]
        if not candidates:
            return []

        hits: list[EntityMatch] = []
        for doc in pool:
            doc_grams = _trigrams(doc.text)
            best = 0.0
            for cand in candidates:
                grams = _trigrams(cand)
                if not grams:
                    continue
                best = max(best, len(grams & doc_grams) / len(grams))
            if best >= MIN_TRIGRAM_CONTAINMENT:
                hits.append(EntityMatch(doc.kind, doc.id, doc.label, best,
                                        "trigram"))
        hits.sort(key=lambda m: (-m.score, m.id))
        return hits[:limit]


# -- the live index ----------------------------------------------------------

_CACHE: dict[str, object] = {"index": None, "at": 0.0}


def _rows_from_db() -> list[tuple[str, str, str, str]]:
    """(kind, id, label, searchable text) for every resolvable entity."""
    from netra.core.db import SessionLocal
    from netra.core.models import (Camera, VehicleAttributeRow,
                                   WatchlistEntry, ZoneRule)

    out: list[tuple[str, str, str, str]] = []
    with SessionLocal() as db:
        for c in db.query(Camera).all():
            # The id is part of the text because operators say "AHM 14" as
            # often as they say the camera's name.
            text = " ".join(x for x in (c.id, c.name, c.city, c.district,
                                        c.capability) if x)
            out.append(("camera", c.id, c.name or c.id, text))
        for z in db.query(ZoneRule).all():
            text = " ".join(x for x in (z.name, z.rule, z.camera_id) if x)
            out.append(("zone", str(z.id), z.name or f"zone {z.id}", text))
        for w in db.query(WatchlistEntry).all():
            text = " ".join(x for x in (w.plate, w.case_ref, w.notes,
                                        w.category, w.owner_name,
                                        w.vehicle_make) if x)
            out.append(("watchlist", str(w.id), w.plate, text))
        # Vision-language descriptions, so "the black SUV with a roof rack"
        # resolves to the detections that were described that way. This is the
        # only kind whose text is model-written rather than operator-entered,
        # which changes nothing about the division of labour: it resolves a
        # phrase to a detection id, and every fact about that detection is then
        # read from the detections table.
        for v in (db.query(VehicleAttributeRow)
                  .order_by(VehicleAttributeRow.id.desc())
                  .limit(VEHICLE_INDEX_LIMIT).all()):
            marks = " ".join(str(m) for m in (v.markings or []))
            text = " ".join(x for x in (v.description, marks) if x)
            if not text.strip():
                continue
            out.append(("vehicle", str(v.detection_id), v.description, text))
    return out


def build_index(rows=None) -> EntityIndex:
    """Build an index from the given rows, or from the database."""
    idx = EntityIndex()
    for kind, entity_id, label, text in (rows if rows is not None
                                         else _rows_from_db()):
        idx.add(kind, entity_id, label, text)
    idx.finalise()
    return idx


def get_index(force: bool = False) -> EntityIndex:
    """The shared index, rebuilt when the TTL has expired."""
    now = time.monotonic()
    idx = _CACHE.get("index")
    if force or idx is None or now - float(_CACHE["at"]) > TTL_S:
        idx = build_index()
        _CACHE["index"] = idx
        _CACHE["at"] = now
    return idx  # type: ignore[return-value]


def resolve(query: str, kind: str | None = None, limit: int = 5,
            ignore: frozenset[str] | set[str] | None = None
            ) -> list[EntityMatch]:
    """Resolve a fuzzy mention against live platform entities."""
    try:
        return get_index().resolve(query, kind=kind, limit=limit,
                                   ignore=ignore)
    except Exception as exc:
        # Resolution is an assist, never a fact. If the registry cannot be
        # read the assistant must still answer from SQL rather than fail - but
        # a schema or connection fault that silently costs every operator
        # their fuzzy lookups has to be findable in the log, not inferred from
        # the feature quietly never working.
        log.warning("entity resolution unavailable, answering unscoped: %r",
                    exc)
        return []


# -- self-check --------------------------------------------------------------

_SYNTHETIC = [
    ("camera", "GJ-JUN-004", "Junagadh Bypass ANPR",
     "GJ-JUN-004 Junagadh Bypass ANPR Junagadh Junagadh anpr"),
    ("camera", "GJ-AHM-014", "Ahmedabad SG Highway Tollnaka",
     "GJ-AHM-014 Ahmedabad SG Highway Tollnaka Ahmedabad Ahmedabad anpr"),
    ("camera", "GJ-RAJ-002", "Rajkot Ring Road North",
     "GJ-RAJ-002 Rajkot Ring Road North Rajkot Rajkot vehicle"),
    ("camera", "GJ-SUR-009", "Surat Ring Road South",
     "GJ-SUR-009 Surat Ring Road South Surat Surat degraded"),
    ("zone", "3", "Bypass hard shoulder",
     "Bypass hard shoulder intrusion GJ-JUN-004"),
    ("watchlist", "7", "GJ01AB1234",
     "GJ01AB1234 FIR-2026-118 reported stolen from Vadodara stolen"),
    ("vehicle", "9182", "black suv; tinted windows; alloy wheels; roof rack",
     "black suv tinted windows alloy wheels roof rack"),
    ("vehicle", "9200", "white van; markings: Om Travels",
     "white van markings Om Travels"),
]


def _self_check() -> None:
    """Resolution never touches the database here: the failure worth pinning
    down is what the scorer does with a corpus, not what the registry holds."""
    idx = build_index(_SYNTHETIC)
    assert len(idx) == len(_SYNTHETIC)

    # An exact name resolves, and resolves to the right row.
    m = idx.resolve("Junagadh Bypass ANPR")
    assert m and m[0].id == "GJ-JUN-004", m

    # A partial name resolves.
    m = idx.resolve("the Junagadh bypass camera")
    assert m and m[0].id == "GJ-JUN-004", m

    # A misspelling shares no token with the target, so BM25 alone scores it
    # zero; the trigram fallback must recover it.
    m = idx.resolve("junagad")
    assert m and m[0].id == "GJ-JUN-004", m
    assert m[0].via == "trigram", m

    # An unrelated string must resolve to nothing rather than to whichever row
    # happens to score least badly. This is the whole confidence floor.
    for junk in ("what is the weather tomorrow", "quantum entanglement",
                 "zzzzzzzz", "how many detections", ""):
        assert idx.resolve(junk) == [], (junk, idx.resolve(junk))

    # Generic corpus vocabulary is not evidence: every camera contains "camera"
    # -ish words, so a query of nothing but those must resolve to nothing.
    assert idx.resolve("the camera") == [], idx.resolve("the camera")

    # BM25 ranks the better lexical match higher: both Ring Road cameras match
    # "ring road", but only one is in Rajkot.
    m = idx.resolve("rajkot ring road")
    assert m and m[0].id == "GJ-RAJ-002", m
    ids = [x.id for x in m]
    assert "GJ-SUR-009" not in ids or ids.index("GJ-RAJ-002") < ids.index("GJ-SUR-009"), m

    # Term saturation and idf together: a rare term must outweigh a repeated
    # common one.
    assert idx._idf("junagadh") > idx._idf("road")

    # Kind filtering, and the promise that a resolution is always a real row.
    known = {(k, i) for k, i, _, _ in _SYNTHETIC}
    for q in ("junagadh bypass", "surat", "FIR-2026-118", "hard shoulder",
              "GJ01AB1234", "toll", "ahmedabad sg highway"):
        for hit in idx.resolve(q):
            assert (hit.kind, hit.id) in known, (q, hit)

    m = idx.resolve("bypass", kind="zone")
    assert m and all(x.kind == "zone" for x in m), m
    assert idx.resolve("junagadh bypass", kind="watchlist") == []

    # A watchlist entry resolves by its case reference, not only its plate.
    m = idx.resolve("FIR-2026-118")
    assert m and m[0].kind == "watchlist" and m[0].id == "7", m

    # A vision-language description resolves to its detection, and the id it
    # returns is the detection id the facts are then read from.
    m = idx.resolve("the black SUV with a roof rack")
    assert m and m[0].kind == "vehicle" and m[0].id == "9182", m
    m = idx.resolve("Om Travels")
    assert m and m[0].kind == "vehicle" and m[0].id == "9200", m
    # ...and the description index must not answer questions about cameras.
    m = idx.resolve("rajkot ring road")
    assert m and m[0].kind == "camera", m

    # An empty corpus must be answerable, not an exception.
    assert build_index([]).resolve("anything") == []

    # A short fragment of a longer word resolves, because an operator says
    # "toll" and the registry says "Tollnaka".
    m = idx.resolve("toll")
    assert m and m[0].id == "GJ-AHM-014" and m[0].via == "trigram", m

    # The fallback is consulted only when token matching found nothing, so it
    # can never displace a genuine lexical hit.
    m = idx.resolve("surat ring road")
    assert m and m[0].via == "bm25" and m[0].id == "GJ-SUR-009", m

    # Intent vocabulary must not sink the mention beside it. "down", "health"
    # and "coverage" appear in no camera name, so unfiltered they carry maximum
    # idf into the denominator and defeat a perfectly clear reference.
    intent = frozenset({"down", "health", "coverage", "faulty", "status",
                        "detections", "how", "many", "is", "in", "for"})
    m = idx.resolve("is GJ-JUN-004 down", ignore=intent)
    assert m and m[0].id == "GJ-JUN-004", m
    m = idx.resolve("camera health for junagadh bypass", ignore=intent)
    assert m and m[0].id == "GJ-JUN-004", m
    m = idx.resolve("coverage in rajkot", ignore=intent)
    assert m and m[0].id == "GJ-RAJ-002", m
    m = idx.resolve("how many detections on junagad", ignore=intent)
    assert m and m[0].id == "GJ-JUN-004" and m[0].via == "trigram", m

    # ...and dropping intent words must not open a route to a spurious match.
    for junk in ("the weather tomorrow", "banana", "xyzzy", "please",
                 "the camera", "a zone", "show me", "how many detections",
                 "is it down", "status"):
        assert idx.resolve(junk, ignore=intent) == [], (
            junk, idx.resolve(junk, ignore=intent))

    # An ignored word the corpus does know is still matchable, so a camera is
    # never made unfindable by a word that is also an intent keyword.
    assert "rajkot" not in intent
    m = idx.resolve("north ring road", ignore=frozenset({"north"}))
    assert m and m[0].id in ("GJ-RAJ-002", "GJ-SUR-009"), m

    # Normalisation is what makes the character fallback work at all.
    assert normalise("Junagadh-Bypass ANPR") == "junagadhbypassanpr"
    assert tokenise("GJ-AHM-014") == ["gj", "ahm", "014"]

    print("retrieval self-check passed")


if __name__ == "__main__":
    _self_check()
