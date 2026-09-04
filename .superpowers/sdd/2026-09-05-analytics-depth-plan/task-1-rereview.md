# Re-review — Task 1 fix round 1

00d2a08 Report how many reads actually voted, not how many were held

diff --git a/netra/analytics/inference.py b/netra/analytics/inference.py
index e6de86a..bec80f8 100644
--- a/netra/analytics/inference.py
+++ b/netra/analytics/inference.py
@@ -392,24 +392,26 @@ class InferenceEngine:
 
         for det in detections:
             if det.track_id is None:
                 continue
             if det.plate_text:
                 voter.add(det.track_id, det.plate_text,
                           det.plate_conf or 0.0, frame.pts_ms)
             result = voter.consensus(det.track_id)
             if result is None:
                 continue
-            text, conf, count = result
-            if count < 2:
-                # Nothing was voted on, so leave this frame's own read alone
-                # rather than restating it as a consensus it is not.
+            text, conf, voters = result
+            if voters < 2:
+                # One voter is not a vote. Either the track has a single read,
+                # or the reads disagreed on length and one was passed through
+                # unvoted. Leave this frame's own read alone rather than
+                # presenting a lone OCR guess as a consensus.
                 continue
             det.plate_text = text
             det.plate_conf = conf
             det.plate_chars = len(text)
             self.stats["plate_votes"] += 1
 
         # The tracker expires stale tracks internally; without this the voter
         # would hold reads for vehicles that left the frame long ago.
         voter.retain(tracker.tracks.keys())
 
diff --git a/netra/analytics/plate_vote.py b/netra/analytics/plate_vote.py
index eebf8fc..ac5bc61 100644
--- a/netra/analytics/plate_vote.py
+++ b/netra/analytics/plate_vote.py
@@ -78,22 +78,25 @@ class PlateVoter:
         bucket = self._obs.setdefault(track_id, [])
         bucket.append(PlateObservation(cleaned, float(confidence), float(pts_ms)))
         if len(bucket) > MAX_OBSERVATIONS_PER_TRACK:
             # Drop the oldest: a plate gets larger and clearer as the vehicle
             # approaches, so recent reads are the better evidence anyway.
             del bucket[0]
 
     def consensus(self, track_id: int) -> tuple[str | None, float, int] | None:
         """Vote this track's reads into one plate.
 
-        Returns (text, confidence, observation_count), or None if the track has
-        never produced a usable read.
+        Returns (text, confidence, voter_count), or None if the track has never
+        produced a usable read. `voter_count` is the number of observations
+        that actually contributed to the returned text, not the number held -
+        a caller must be able to tell a real vote from a lone read dressed up
+        as one, so the two fallback paths below both report 1.
         """
         observations = self._obs.get(track_id)
         if not observations:
             return None
 
         if len(observations) < MIN_OBSERVATIONS_FOR_VOTE:
             only = observations[0]
             return only.text, only.confidence, 1
 
         # Length first - see the module docstring. Ties go to the length whose
@@ -110,42 +113,42 @@ class PlateVoter:
         # realigned. A proper implementation would align them by edit distance
         # and let a dropped-character read still vote on the characters it did
         # get right. Ceiling: on a track where every read lost a different
         # character, we fall back to the single most confident read below and
         # gain nothing from voting.
         if len(cohort) < MIN_OBSERVATIONS_FOR_VOTE:
             # Every read disagreed on length, so there is no majority to speak
             # of. Return the single most confident read rather than inventing a
             # consensus out of reads that never agreed.
             best = max(observations, key=lambda o: o.confidence)
-            return best.text, best.confidence, len(observations)
+            return best.text, best.confidence, 1
 
         chars: list[str] = []
         shares: list[float] = []
         for pos in range(winning_length):
             # group -> total confidence, and group -> best raw char seen.
             group_conf: dict[str, float] = defaultdict(float)
             group_best: dict[str, tuple[float, str]] = {}
             for obs in cohort:
                 ch = obs.text[pos]
                 key = _fold(ch)
                 group_conf[key] += obs.confidence
                 prior = group_best.get(key)
                 if prior is None or obs.confidence > prior[0]:
                     group_best[key] = (obs.confidence, ch)
             total = sum(group_conf.values())
             winner = max(group_conf, key=lambda k: group_conf[k])
             chars.append(group_best[winner][1])
             shares.append(group_conf[winner] / total if total > 0 else 0.0)
 
         confidence = sum(shares) / len(shares) if shares else 0.0
-        return "".join(chars), round(confidence, 4), len(observations)
+        return "".join(chars), round(confidence, 4), len(cohort)
 
     def forget(self, track_id: int) -> None:
         """Drop a track's reads once the tracker has expired it."""
         self._obs.pop(track_id, None)
 
     def retain(self, live_track_ids) -> None:
         """Forget every track not in `live_track_ids`.
 
         The tracker expires stale tracks internally, so this is how the voter
         learns a vehicle has gone - otherwise its reads would outlive it and
@@ -193,21 +196,32 @@ def _self_check() -> None:
 
     # Differing lengths must not corrupt each other: the short read is set
     # aside entirely rather than shifting positions in the majority.
     v = PlateVoter()
     v.add(4, "GJ01AB1234", 0.8, 0.0)
     v.add(4, "GJ01AB1234", 0.8, 100.0)
     v.add(4, "J01AB1234", 0.9, 200.0)
     text, conf, n = v.consensus(4)
     assert text == "GJ01AB1234", text
     assert len(text) == 10, text
-    assert n == 3, n  # all reads counted as observations, only some as voters
+    assert n == 2, n  # only the two same-length reads voted, not all three
+
+    # Two reads of differing lengths mean no vote happened at all. The result
+    # is one read passed through, and it must not be reportable as a
+    # 2-observation consensus - a caller gating on "did enough voters agree"
+    # would otherwise write a lone raw OCR guess out as a voted plate.
+    v = PlateVoter()
+    v.add(41, "GJ01AB1234", 0.6, 0.0)
+    v.add(41, "J01AB1234", 0.9, 100.0)
+    text, conf, n = v.consensus(41)
+    assert n == 1, (text, conf, n)
+    assert text == "J01AB1234" and conf == 0.9, (text, conf)
 
     # Confusion folding groups votes: O and 0 are the same vote, G and 6 too.
     # Two reads of "GJO1AB1234" plus one of "GJ01AB1234" agree everywhere once
     # folded, and the emitted text must remain a plausible plate rather than
     # the folded form "6J01A81234".
     v = PlateVoter()
     v.add(5, "GJO1AB1234", 0.7, 0.0)
     v.add(5, "GJ01AB1234", 0.9, 100.0)
     v.add(5, "GJ01AB1234", 0.9, 200.0)
     text, conf, n = v.consensus(5)
