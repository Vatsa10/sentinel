# Re-review — Task 4 fix round 1

e3a54a8 Stop the watchlist prefilter losing genuine alerts

diff --git a/netra/analytics/matching.py b/netra/analytics/matching.py
index 8e8dc5e..a5b21b8 100644
--- a/netra/analytics/matching.py
+++ b/netra/analytics/matching.py
@@ -80,101 +80,182 @@ def plate_similarity(observed: str, target: str) -> tuple[float, str]:
         return 0.0, f"plate mismatch ({agree}/{span} characters)"
     # 60% agreement scores 0; full agreement over a short read approaches 0.9.
     score = 0.9 * (frac - 0.6) / 0.4
     return score, f"{agree}/{span} characters agree ({obs} vs {tgt})"
 
 
 # --- watchlist prefilter -----------------------------------------------------
 # `score_match` is cheap, but a watchlist of 10,000 entries scored against
 # every detection at thousands of detections per minute is tens of millions of
 # comparisons per minute, on the thread that also has to persist detections.
-# The prefilter's only job is to drop entries that cannot possibly match, so
-# full scoring still decides every candidate and the matching behaviour
-# downstream is unchanged.
-INDEX_WINDOW = 4
+# The prefilter's only job is to drop entries that cannot possibly alert, so
+# full scoring still decides every candidate.
+#
+# The hard constraint is that it must never drop a pair `score_match` would
+# alert on: a watchlist hit lost inside an optimisation is the worst failure
+# this platform can have, and unlike a slow scan nothing would ever reveal it.
+# So the window size is derived rather than chosen. `plate_similarity` has
+# three branches, and the awkward one is positional agreement: two plates can
+# agree on 8 of 10 characters with the two mismatches placed so that they share
+# no long common run at all. Indexing on four-character windows loses those -
+# measured at 6.1% of alerting two-error reads.
+#
+# Derivation. An alert needs `fused >= ALERT_THRESHOLD`; appearance can
+# contribute at most `WEIGHTS["appearance"]`, so the plate score must reach
+# `p_min`, which fixes the minimum positional agreement `_MIN_ALERTING_FRAC`.
+# Over a span of n characters that allows at most m mismatches, which cut the
+# agreeing characters into at most m+1 runs, so the longest shared run is at
+# least ceil((n-m)/(m+1)). A shared q-character window therefore exists
+# whenever n > q*m + q - 1. Evaluated over every span, q=2 is safe everywhere
+# and q=3 is safe except for spans of 4, 5, 7, 8, 11 and 14 - both facts are
+# asserted in the self-check rather than trusted.
+INDEX_WINDOW = 3
+#: Provably complete for every span, but far coarser: on a watchlist of
+#: Gujarat plates every entry shares the "GJ" bucket, so this degenerates to a
+#: full scan. Used only for the spans where INDEX_WINDOW is not safe.
+FALLBACK_WINDOW = 2
+#: Below this a read cannot score at all except against an equally short entry
+#: (`plate_similarity` refuses spans under 4), so those entries are held apart.
+MIN_SCORABLE_CHARS = 4
+
+
+def _min_alerting_fraction() -> float:
+    """Least positional agreement that could still clear ALERT_THRESHOLD.
+
+    Derived from the scoring constants rather than written down, so retuning
+    the weights or the threshold cannot silently invalidate the index. The 0.6
+    and 0.9/0.4 are `plate_similarity`'s own positional curve.
+    """
+    p_min = max(0.0, (ALERT_THRESHOLD - WEIGHTS["appearance"]) / WEIGHTS["plate"])
+    if p_min >= 0.9:
+        return 1.0  # no positional score could reach the threshold at all
+    return 0.6 + 0.4 * p_min / 0.9
+
+
+_MIN_ALERTING_FRAC = _min_alerting_fraction()
+_PRIMARY_SAFE: dict[int, bool] = {}
+
+
+def window_guaranteed(span: int, window: int) -> bool:
+    """Must two plates alerting positionally over `span` share a window?"""
+    import math
+    if span < window:
+        return False
+    agree = math.ceil(_MIN_ALERTING_FRAC * span - 1e-9)
+    mismatches = span - agree
+    return span > window * mismatches + window - 1
+
 
+def _primary_safe(length: int) -> bool:
+    """Cached: consulted once per entry at build and once per lookup."""
+    cached = _PRIMARY_SAFE.get(length)
+    if cached is None:
+        cached = _PRIMARY_SAFE[length] = window_guaranteed(length, INDEX_WINDOW)
+    return cached
 
-def plate_windows(plate: str | None) -> set[str]:
-    """Every INDEX_WINDOW-character window of the confusion-folded plate.
+
+def plate_windows(plate: str | None, window: int = INDEX_WINDOW) -> set[str]:
+    """Every `window`-character window of the confusion-folded plate.
 
     Windows, not a prefix: `plate_similarity` matches an observed read that is
     a *substring* of the watchlist plate, so "AB1234" must find "GJ01AB1234"
-    even though their first four characters have nothing in common. Folding
-    first is equally load-bearing - comparison happens on folded text, so an
-    index built on raw text would miss every OCR confusion the matcher is
-    specifically designed to absorb.
+    even though their first characters have nothing in common. Folding first is
+    equally load-bearing - comparison happens on folded text, so an index built
+    on raw text would miss every OCR confusion the matcher exists to absorb.
     """
     folded = normalise_plate(plate)
-    if len(folded) < INDEX_WINDOW:
+    if len(folded) < window:
         return set()
-    return {folded[i:i + INDEX_WINDOW]
-            for i in range(len(folded) - INDEX_WINDOW + 1)}
+    return {folded[i:i + window] for i in range(len(folded) - window + 1)}
 
 
 class WatchlistIndex:
     """Entries bucketed by the windows of their plate, for candidate lookup.
 
     Built once per watchlist reload and thrown away with it; nothing here is
     incremental, because a rebuild over 10,000 entries is a few milliseconds
     every thirty seconds.
 
-    ponytail: an entry whose plate folds to fewer than INDEX_WINDOW characters
-    goes in a bucket that is always considered, since no window can be formed
-    from it and `plate_similarity` may still score it positionally. If a
-    watchlist were mostly two-character stubs the prefilter would degrade to
-    the full scan it replaces - which is correct, just not fast.
+    Both window sizes are indexed up front. The fallback is needed only for
+    short reads, but building it lazily would mean building it on the inference
+    thread at the moment a short read arrives, which is exactly when there is
+    no time for it.
+
+    ponytail: the fallback index is a full scan in disguise on a watchlist of
+    same-region plates, because every Gujarat plate shares the "GJ" bucket. A
+    read of 4, 5, 7 or 8 characters therefore costs what the prefilter was
+    written to avoid. That is deliberate - those are the spans where a
+    3-character window is not provably complete, and a slow correct answer
+    beats a fast one that loses a watchlist hit. The ceiling is that a grid
+    producing mostly short partial reads gets little benefit from any of this.
     """
 
     def __init__(self, entries: list[dict] | None = None):
         self.entries: list[dict] = list(entries or [])
         self._buckets: dict[str, list[dict]] = {}
+        self._fallback: dict[str, list[dict]] = {}
+        #: entries too short for any window to constrain
         self._short: list[dict] = []
+        #: entries whose own length makes the primary window unsafe whatever
+        #: the read is, because the span is capped by the shorter of the two
+        self._forced: list[dict] = []
         for entry in self.entries:
-            windows = plate_windows(entry.get("plate"))
-            if not windows:
+            folded = normalise_plate(entry.get("plate"))
+            if len(folded) < MIN_SCORABLE_CHARS:
                 self._short.append(entry)
                 continue
-            for key in windows:
+            if not _primary_safe(len(folded)):
+                self._forced.append(entry)
+            for key in plate_windows(folded, INDEX_WINDOW):
                 self._buckets.setdefault(key, []).append(entry)
+            for key in plate_windows(folded, FALLBACK_WINDOW):
+                self._fallback.setdefault(key, []).append(entry)
 
-    def candidates(self, plate_text: str | None) -> list[dict]:
-        """Entries worth scoring against this observed plate.
-
-        A detection is tested against the buckets of every window of its own
-        folded plate, so a partial read matches wherever it sits inside the
-        target. Order is stable so alert ordering does not change with the
-        prefilter's internals.
-        """
-        windows = plate_windows(plate_text)
-        if not windows:
-            # Too little recovered to index on. `plate_similarity` refuses to
-            # score reads this short anyway, but the short bucket is returned
-            # rather than nothing so the filter never invents a rule the
-            # scorer does not have.
-            return list(self._short)
-
+    @staticmethod
+    def _gather(buckets: dict, windows: set[str], extra: list) -> list[dict]:
         seen: set[int] = set()
         out: list[dict] = []
-        for key in windows:
-            for entry in self._buckets.get(key, ()):
+        for source in [buckets.get(k, ()) for k in windows] + [extra]:
+            for entry in source:
                 marker = id(entry)
                 if marker not in seen:
                     seen.add(marker)
                     out.append(entry)
-        for entry in self._short:
-            if id(entry) not in seen:
-                out.append(entry)
         return out
 
+    def candidates(self, plate_text: str | None) -> list[dict]:
+        """Entries worth scoring against this observed plate.
+
+        A superset of everything `score_match` could alert on. Order is stable
+        so alert ordering does not change with the prefilter's internals.
+        """
+        folded = normalise_plate(plate_text)
+        if len(folded) < MIN_SCORABLE_CHARS:
+            # Nothing this short can score against a longer plate; only an
+            # equally short entry could match it, and only exactly.
+            return list(self._short)
+
+        if _primary_safe(len(folded)):
+            return self._gather(self._buckets,
+                                plate_windows(folded, INDEX_WINDOW),
+                                self._short + self._forced)
+        # This read's span is one the primary window cannot prove. Fall back to
+        # the window that is complete for every span, and pay for it.
+        return self._gather(self._fallback,
+                            plate_windows(folded, FALLBACK_WINDOW),
+                            self._short)
+
     def stats(self) -> dict:
-        return {"entries": len(self.entries), "buckets": len(self._buckets),
-                "unindexed": len(self._short),
+        return {"entries": len(self.entries),
+                "buckets": len(self._buckets),
+                "window": INDEX_WINDOW,
+                "unindexed": len(self._short) + len(self._forced),
                 "largest_bucket": max((len(v) for v in self._buckets.values()),
                                       default=0)}
 
 
 def appearance_similarity(det_class: str | None, det_colour: str | None,
                           wl_class: str | None, wl_colour: str | None) -> tuple[float, str]:
     """Score vehicle class and colour agreement.
 
     Deliberately coarse. Colour under sodium and LED street lighting is not
     reliable enough to carry more weight than this, and pretending otherwise
@@ -326,80 +407,124 @@ def _self_check() -> None:
                      "vehicle_colour": "white"})
     assert not r.is_alert, r
 
     # Space-time veto.
     ok, why = spacetime_plausible(distance_km=300.0, elapsed_s=60)
     assert not ok, why
     ok, why = spacetime_plausible(distance_km=2.0, elapsed_s=180)
     assert ok, why
 
     # --- watchlist prefilter ------------------------------------------------
+    # The window size is derived from the scoring constants, so check the
+    # derivation itself before checking anything built on it.
+    assert abs(_MIN_ALERTING_FRAC - 0.7111111) < 1e-6, _MIN_ALERTING_FRAC
+    unsafe = [n for n in range(4, 200) if not window_guaranteed(n, INDEX_WINDOW)]
+    assert unsafe == [4, 5, 7, 8, 11, 14], unsafe
+    assert all(window_guaranteed(n, FALLBACK_WINDOW) for n in range(4, 200))
+    # And the four-character window the first cut of this used is unsafe at
+    # every span, which is why it lost alerts.
+    assert not any(window_guaranteed(n, 4) for n in range(4, 200))
+
     entries = [{"id": 1, "plate": "GJ01AB1234"},
                {"id": 2, "plate": "MH12XY9999"},
                {"id": 3, "plate": "GJ18CD5678"},
                {"id": 4, "plate": "XY9"}]          # too short to index
     index = WatchlistIndex(entries)
 
-    # The property the prefilter exists to preserve: a partial read that full
-    # scoring would match must survive it. A naive first-four-characters index
-    # would bucket entry 1 under "GJ01" and never consider it for "AB1234",
-    # silently losing an alert - which is why the index is built on windows.
+    # A partial read that full scoring matches must survive the prefilter. A
+    # naive first-characters index would bucket entry 1 under "GJ0" and never
+    # consider it for "AB1234", silently losing an alert.
     got = {e["id"] for e in index.candidates("AB1234")}
     assert 1 in got, got
     assert score_match({"plate_text": "AB1234"}, entries[0]).reasons["plate"]["score"] > 0.5
 
     # Confusion folding happens before bucketing, so an OCR read of "AB1Z34"
     # still finds the entry written "AB1234".
     assert 1 in {e["id"] for e in index.candidates("GJ0IAB1Z34")}
 
-    # An exact read reaches its own entry and skips the unrelated ones.
-    got = {e["id"] for e in index.candidates("GJ01AB1234")}
-    assert got == {1, 4}, got          # 4 is the always-considered short bucket
-    assert 2 not in got and 3 not in got, got
+    # The two-error positional reads that the four-character window dropped.
+    for observed in ("GJW1ABW234", "GJ0WABW234", "GJ0WAB1W34"):
+        assert 1 in {e["id"] for e in index.candidates(observed)}, observed
 
-    # A read too short to index still sees the short bucket rather than
-    # nothing, so the prefilter never enforces a rule the scorer does not.
+    # A read too short to score at all sees only the short bucket.
     assert {e["id"] for e in index.candidates("G1")} == {4}
 
-    # Superset property over realistic degradations: contiguous partial reads
-    # and OCR confusions, which is what this grid actually produces.
+    # It must still actually filter, or it is a scan with extra steps.
+    got = {e["id"] for e in index.candidates("GJ01AB1234")}
+    assert 2 not in got, got
+
+    # --- brute force: every alerting two-error read must be returned ---------
+    # Not a hand-picked example. Every position pair, every substitution, over
+    # a watchlist large enough that the buckets are doing real work. Appearance
+    # attributes agree, which is the case that lets a weak positional plate
+    # score reach the threshold - and the case the first cut got wrong.
+    import itertools
     import random
-    rng = random.Random(7)
-    plates = [f"GJ{rng.randint(1, 38):02d}{chr(65 + rng.randrange(26))}"
-              f"{chr(65 + rng.randrange(26))}{rng.randint(0, 9999):04d}"
-              for _ in range(200)]
-    corpus = [{"id": i, "plate": p} for i, p in enumerate(plates)]
+
+    rng = random.Random(11)
+    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
+    plates = set()
+    while len(plates) < 300:
+        plates.add(f"GJ{rng.randint(1, 38):02d}{rng.choice(letters)}"
+                   f"{rng.choice(letters)}{rng.randint(0, 9999):04d}")
+    corpus = [{"id": i, "plate": p, "vehicle_class": "car",
+               "vehicle_colour": "white"} for i, p in enumerate(sorted(plates))]
+    # A short entry and an awkward-length one, so the short and forced buckets
+    # are exercised by the sweep rather than only by the cases above.
+    corpus.append({"id": -1, "plate": "GJ1", "vehicle_class": "car",
+                   "vehicle_colour": "white"})
+    corpus.append({"id": -2, "plate": "GJ01AB12", "vehicle_class": "car",
+                   "vehicle_colour": "white"})
     big = WatchlistIndex(corpus)
-    scanned = 0
-    for target in plates[:40]:
-        start = rng.randrange(0, len(target) - 4)
-        observed = target[start:start + rng.randint(4, len(target) - start)]
-        observed = "".join(
-            {"0": "O", "1": "I", "5": "S"}.get(ch, ch) for ch in observed)
-        candidates = big.candidates(observed)
-        scanned += len(candidates)
-        keep = {id(e) for e in candidates}
+    observed_base = {"vehicle_class": "car", "colour": "white"}
+
+    def _variants(plate: str, errors: int):
+        folded = normalise_plate(plate)
+        for positions in itertools.combinations(range(len(folded)), errors):
+            for subs in itertools.product("WV5X", repeat=errors):
+                candidate = list(folded)
+                if any(candidate[i] == c for i, c in zip(positions, subs)):
+                    continue
+                for i, c in zip(positions, subs):
+                    candidate[i] = c
+                yield "".join(candidate)
+
+    # Bounded so the check stays a few seconds: the full one- and two-error
+    # spaces of one plate, plus a sample of the three-error space, each scored
+    # against every entry. That is the space the four-character window lost
+    # alerts in.
+    sweep = list(_variants(corpus[0]["plate"], 1))
+    sweep += list(_variants(corpus[0]["plate"], 2))
+    three = list(_variants(corpus[0]["plate"], 3))
+    sweep += rng.sample(three, 150)
+    sweep += list(_variants(corpus[-1]["plate"], 2))
+
+    alerting = lookups = 0
+    for observed in sweep:
+        keep = {id(e) for e in big.candidates(observed)}
+        lookups += 1
         for entry in corpus:
-            if score_match({"plate_text": observed}, entry).is_alert:
-                assert id(entry) in keep, (observed, entry)
-    # It must actually filter, or it is a scan with extra steps.
-    assert scanned < 40 * len(corpus) * 0.5, scanned
-
-    # The honest ceiling. `plate_similarity` also scores positional agreement,
-    # and two plates can agree on 70% of their characters with the mismatches
-    # spread out so that they share no 4-character window at all. Such a pair
-    # is not a candidate. It needs three scattered OCR errors in one read -
-    # degraded enough that the fused score rarely clears the alert threshold -
-    # and the alternative, a 2-character index, buckets 10,000 entries into
-    # 1,296 keys and prefilters almost nothing. Pinned here so the trade-off
-    # is visible rather than discovered.
-    missed_obs, missed_tgt = "GJX1AX12X4", "GJ01AB1234"
-    assert plate_similarity(missed_obs, missed_tgt)[0] > 0
-    assert not (plate_windows(missed_obs) & plate_windows(missed_tgt))
-    assert not score_match({"plate_text": missed_obs},
-                           {"plate": missed_tgt}).is_alert
+            if score_match({**observed_base, "plate_text": observed},
+                           entry).is_alert:
+                alerting += 1
+                # The whole point of the prefilter, asserted directly: zero
+                # losses, not "few".
+                assert id(entry) in keep, (observed, entry["plate"])
+    assert alerting > 300, alerting          # the sweep must reach real alerts
+    assert lookups > 900, lookups
+    # Partial reads longer than the truncated variants also matter: a
+    # contiguous substring is the other way OCR degrades on this grid.
+    for target in corpus[:40]:
+        folded = normalise_plate(target["plate"])
+        for begin in range(0, len(folded) - 4):
+            observed = folded[begin:begin + rng.randint(4, len(folded) - begin)]
+            keep = {id(e) for e in big.candidates(observed)}
+            for entry in corpus:
+                if score_match({**observed_base, "plate_text": observed},
+                               entry).is_alert:
+                    assert id(entry) in keep, (observed, entry["plate"])
 
     print("matching self-check passed")
 
 
 if __name__ == "__main__":
     _self_check()
diff --git a/netra/core/retention.py b/netra/core/retention.py
index c1fe06a..0604d64 100644
--- a/netra/core/retention.py
+++ b/netra/core/retention.py
@@ -106,26 +106,32 @@ def prune_evidence(max_bytes: int | None = None, max_age_days: int | None = None
         "scanned": len(files), "bytes_before": total,
         "deleted_expired": 0, "deleted_over_budget": 0,
         "bytes_freed": 0, "retained_protected": 0,
         "retained_protected_bytes": 0, "failed": 0,
         "max_bytes": max_bytes, "max_age_days": max_age_days,
         "dry_run": dry_run,
     }
 
     cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).timestamp()
     remaining: list[tuple[float, int, Path]] = []
+    # A protected file can be reached by both rules in one call - expired
+    # *and* over budget - and it must be reported once, not twice. This figure
+    # is audited into storage.prune, so a doubled count is a doubled claim.
+    counted_protected: set[str] = set()
 
     def _remove(item, reason: str) -> None:
-        mtime, size, path = item
+        _mtime, size, path = item
         if path.name in keep:
-            report["retained_protected"] += 1
-            report["retained_protected_bytes"] += size
+            if path.name not in counted_protected:
+                counted_protected.add(path.name)
+                report["retained_protected"] += 1
+                report["retained_protected_bytes"] += size
             remaining.append(item)
             return
         if not dry_run:
             try:
                 path.unlink()
             except OSError:
                 report["failed"] += 1
                 remaining.append(item)
                 return
         report[reason] += 1
@@ -364,20 +370,31 @@ def _check_body(evidence, sf) -> None:
                            evidence_dir=evidence, session_factory=sf)
         left = {p.name for p in evidence.iterdir()}
         # Protected files count against the budget but cannot be removed, so
         # the budget is honoured only as far as the protection allows.
         assert "open.jpg" in left and "zone_open.jpg" in left, left
         assert "recent4.jpg" not in left, left   # oldest unprotected went first
         assert "recent0.jpg" in left, left       # newest survived
         assert r["deleted_over_budget"] == 3, r
         assert r["retained_protected"] == 2, r
 
+        # Both rules in one call. A protected file is expired *and* over
+        # budget, so it passes through the removal path twice; it must still
+        # be reported once. Nothing was covering this, which is how a doubled
+        # count reached an audit record.
+        r = prune_evidence(max_bytes=0, max_age_days=0, evidence_dir=evidence,
+                           session_factory=sf, dry_run=True)
+        assert r["retained_protected"] == 2, r
+        assert r["retained_protected_bytes"] == 2000, r
+        assert r["deleted_expired"] == r["scanned"] - 2, r
+        assert r["deleted_over_budget"] == 0, r   # age already took them all
+
         # A dry run must report the same intent without touching the disk.
         before = sorted(p.name for p in evidence.iterdir())
         r = prune_evidence(max_bytes=0, max_age_days=365, evidence_dir=evidence,
                            session_factory=sf, dry_run=True)
         assert sorted(p.name for p in evidence.iterdir()) == before
         assert r["deleted"] >= 1 and r["retained_protected"] == 2, r
 
         # --- detection rows -------------------------------------------------
         # Cap of 1 against 3 rows: two are alert-referenced and must survive,
         # so exactly one row goes and the table stays above its cap. Reporting
