"""Multi-frame plate voting.

A plate read from a single frame is a guess: at night, at the sizes these
cameras give us, one character in three can be wrong. But a tracked vehicle
passes through the field of view for many frames, and each frame offers an
independent guess at the same physical plate. Voting per character position
across those guesses is how production ANPR turns a pile of noisy reads into
one usable registration number.

Two decisions are worth explaining:

  length first    - reads of different lengths are misaligned. If one read
                    dropped a character, its position 3 is the true position 4,
                    and voting across the two corrupts every position after the
                    gap. So the modal length wins and shorter or longer reads
                    are set aside rather than blended in.
  fold, then emit - OCR confuses G with 6 and O with 0 constantly, so votes for
                    both must count towards the same candidate or the majority
                    splits and a genuine minority read wins. But the character
                    we emit is the best-supported *raw* observation in that
                    group, never the folded form: a consensus of "6J01A81234"
                    would read as a different vehicle to an operator.

The reported confidence is the mean per-position share held by the winner, so
a unanimous plate scores near 1.0 and one where positions were contested
scores honestly lower. That number travels with the read, as everything
inferred on this platform must.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from netra.analytics.matching import CONFUSIONS

#: Cap per track. A vehicle stopped in view for a minute would otherwise
#: accumulate reads without bound, and the first twenty already settle the
#: vote - the twenty-first read has never changed an outcome in practice.
MAX_OBSERVATIONS_PER_TRACK = 20

#: Below this length a read is too fragmentary to align with anything.
MIN_PLATE_CHARS = 4

#: A vote needs at least two voters; one read has nothing to vote against.
MIN_OBSERVATIONS_FOR_VOTE = 2


@dataclass
class PlateObservation:
    """One OCR read of one tracked vehicle's plate."""
    text: str
    confidence: float
    pts_ms: float


def _fold(ch: str) -> str:
    """Collapse a character onto its confusion class, for grouping only."""
    return CONFUSIONS.get(ch, ch)


class PlateVoter:
    """Accumulates plate reads per track and votes them into a consensus.

    One instance per camera; track ids are only unique within a camera.
    """

    def __init__(self) -> None:
        self._obs: dict[int, list[PlateObservation]] = {}

    def add(self, track_id: int, text: str | None,
            confidence: float, pts_ms: float) -> None:
        """Record one read. Short or empty reads are discarded."""
        if not text:
            return
        cleaned = "".join(ch for ch in text.upper() if ch.isalnum())
        if len(cleaned) < MIN_PLATE_CHARS:
            return
        bucket = self._obs.setdefault(track_id, [])
        bucket.append(PlateObservation(cleaned, float(confidence), float(pts_ms)))
        if len(bucket) > MAX_OBSERVATIONS_PER_TRACK:
            # Drop the oldest: a plate gets larger and clearer as the vehicle
            # approaches, so recent reads are the better evidence anyway.
            del bucket[0]

    def consensus(self, track_id: int) -> tuple[str | None, float, int] | None:
        """Vote this track's reads into one plate.

        Returns (text, confidence, voter_count), or None if the track has never
        produced a usable read. `voter_count` is the number of observations
        that actually contributed to the returned text, not the number held -
        a caller must be able to tell a real vote from a lone read dressed up
        as one, so the two fallback paths below both report 1.
        """
        observations = self._obs.get(track_id)
        if not observations:
            return None

        if len(observations) < MIN_OBSERVATIONS_FOR_VOTE:
            only = observations[0]
            return only.text, only.confidence, 1

        # Length first - see the module docstring. Ties go to the length whose
        # reads OCR was most confident about.
        by_length: dict[int, list[PlateObservation]] = defaultdict(list)
        for obs in observations:
            by_length[len(obs.text)].append(obs)
        winning_length = max(
            by_length,
            key=lambda n: (len(by_length[n]), sum(o.confidence for o in by_length[n])))
        cohort = by_length[winning_length]

        # ponytail: reads that disagree on length are discarded rather than
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
            return best.text, best.confidence, 1

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
        return "".join(chars), round(confidence, 4), len(cohort)

    def forget(self, track_id: int) -> None:
        """Drop a track's reads once the tracker has expired it."""
        self._obs.pop(track_id, None)

    def retain(self, live_track_ids) -> None:
        """Forget every track not in `live_track_ids`.

        The tracker expires stale tracks internally, so this is how the voter
        learns a vehicle has gone - otherwise its reads would outlive it and
        leak memory for the lifetime of the process.
        """
        live = set(live_track_ids)
        for tid in [t for t in self._obs if t not in live]:
            del self._obs[tid]

    def reset(self) -> None:
        self._obs.clear()

    def stats(self) -> dict:
        return {"tracks": len(self._obs),
                "observations": sum(len(v) for v in self._obs.values())}


def _self_check() -> None:
    """Runnable check on the voting that decides what plate police are shown."""
    # Unanimous reads: consensus is the read, confidence near 1.0.
    v = PlateVoter()
    for i in range(3):
        v.add(1, "GJ01AB1234", 0.8, i * 100.0)
    text, conf, n = v.consensus(1)
    assert text == "GJ01AB1234", text
    assert conf > 0.99, conf
    assert n == 3, n

    # A minority misread is outvoted by the majority.
    v = PlateVoter()
    v.add(2, "GJ01AB1234", 0.8, 0.0)
    v.add(2, "GJ01AB1234", 0.8, 100.0)
    v.add(2, "GJ01AX1234", 0.8, 200.0)
    text, conf, n = v.consensus(2)
    assert text == "GJ01AB1234", text
    assert conf < 1.0, conf  # the disagreement must show in the confidence

    # A confident minority still loses to a consistent majority - two reads at
    # 0.8 outweigh one at 0.95, which is the point of voting.
    v = PlateVoter()
    v.add(3, "GJ01AB1234", 0.8, 0.0)
    v.add(3, "GJ01AB1234", 0.8, 100.0)
    v.add(3, "GJ01AB9234", 0.95, 200.0)
    assert v.consensus(3)[0] == "GJ01AB1234", v.consensus(3)

    # Differing lengths must not corrupt each other: the short read is set
    # aside entirely rather than shifting positions in the majority.
    v = PlateVoter()
    v.add(4, "GJ01AB1234", 0.8, 0.0)
    v.add(4, "GJ01AB1234", 0.8, 100.0)
    v.add(4, "J01AB1234", 0.9, 200.0)
    text, conf, n = v.consensus(4)
    assert text == "GJ01AB1234", text
    assert len(text) == 10, text
    assert n == 2, n  # only the two same-length reads voted, not all three

    # Two reads of differing lengths mean no vote happened at all. The result
    # is one read passed through, and it must not be reportable as a
    # 2-observation consensus - a caller gating on "did enough voters agree"
    # would otherwise write a lone raw OCR guess out as a voted plate.
    v = PlateVoter()
    v.add(41, "GJ01AB1234", 0.6, 0.0)
    v.add(41, "J01AB1234", 0.9, 100.0)
    text, conf, n = v.consensus(41)
    assert n == 1, (text, conf, n)
    assert text == "J01AB1234" and conf == 0.9, (text, conf)

    # Confusion folding groups votes: O and 0 are the same vote, G and 6 too.
    # Two reads of "GJO1AB1234" plus one of "GJ01AB1234" agree everywhere once
    # folded, and the emitted text must remain a plausible plate rather than
    # the folded form "6J01A81234".
    v = PlateVoter()
    v.add(5, "GJO1AB1234", 0.7, 0.0)
    v.add(5, "GJ01AB1234", 0.9, 100.0)
    v.add(5, "GJ01AB1234", 0.9, 200.0)
    text, conf, n = v.consensus(5)
    assert text == "GJ01AB1234", text
    assert conf > 0.99, conf  # folded agreement is unanimity, not a dispute
    assert "6" not in text and "8" not in text, text

    # Folding must not silently rewrite a genuinely letter-dominant position.
    v = PlateVoter()
    v.add(6, "GJ01AB1234", 0.9, 0.0)
    v.add(6, "6J01AB1234", 0.3, 100.0)
    assert v.consensus(6)[0].startswith("GJ"), v.consensus(6)

    # A single observation is returned as-is with its own confidence.
    v = PlateVoter()
    v.add(7, "GJ01AB1234", 0.42, 0.0)
    assert v.consensus(7) == ("GJ01AB1234", 0.42, 1), v.consensus(7)

    # Reads shorter than four characters carry no constraint and are ignored.
    v = PlateVoter()
    v.add(8, "GJ1", 0.9, 0.0)
    v.add(8, "", 0.9, 100.0)
    v.add(8, None, 0.9, 200.0)
    assert v.consensus(8) is None, v.consensus(8)

    # Separators and case are normalised before voting.
    v = PlateVoter()
    v.add(9, "gj-01 ab 1234", 0.8, 0.0)
    v.add(9, "GJ01AB1234", 0.8, 100.0)
    assert v.consensus(9)[0] == "GJ01AB1234", v.consensus(9)

    # The observation cap is enforced, oldest first.
    v = PlateVoter()
    for i in range(MAX_OBSERVATIONS_PER_TRACK + 5):
        v.add(10, "GJ01AB1234", 0.8, i * 10.0)
    assert v.consensus(10)[2] == MAX_OBSERVATIONS_PER_TRACK, v.consensus(10)
    assert v._obs[10][0].pts_ms == 50.0, v._obs[10][0]

    # An unknown track has no consensus to offer.
    assert PlateVoter().consensus(999) is None

    # Forgetting and retaining both clear state.
    v = PlateVoter()
    v.add(11, "GJ01AB1234", 0.8, 0.0)
    v.add(12, "GJ02CD5678", 0.8, 0.0)
    v.forget(11)
    assert v.consensus(11) is None
    v.retain([99])
    assert v.consensus(12) is None
    assert v.stats()["tracks"] == 0, v.stats()

    v = PlateVoter()
    v.add(13, "GJ01AB1234", 0.8, 0.0)
    v.reset()
    assert v.consensus(13) is None

    print("plate_vote self-check passed")


if __name__ == "__main__":
    _self_check()
