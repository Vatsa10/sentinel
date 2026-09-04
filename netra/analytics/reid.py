"""Appearance-based vehicle re-identification.

Plate recognition is not achievable on most of the Sentinel grid: measured over
2,691 frames across the three best-positioned cameras, more than two hundred
detected vehicles yielded no readable plate at all. See
docs/feed-recon-findings.md.

So the platform must be able to follow a vehicle across cameras on appearance
alone. Each detected vehicle is reduced to a 512-dimension embedding from an
ImageNet-pretrained ResNet-18 backbone, L2-normalised so that cosine similarity
is a dot product. Two sightings are candidates for being the same vehicle when
their embeddings agree, their coarse attributes agree, and the distance between
their cameras is compatible with the elapsed time.

This is deliberately not presented as identification. It produces ranked
candidates for an operator to confirm, which is the honest use of appearance
evidence and the only defensible one in a policing context.

ponytail: a generic ImageNet backbone rather than a vehicle-specific ReID model.
It needs no training data and no extra download beyond torchvision. If
cross-camera precision proves insufficient, swap the backbone for a VeRi-776
trained model - the interface below does not change.
"""
from __future__ import annotations

import logging
import threading

import numpy as np

from netra import config

log = logging.getLogger(__name__)

EMBED_DIM = 512
#: Cosine similarity above which two crops are considered a plausible match.
#: Tuned to be permissive: this produces candidates for review, not verdicts.
SIMILARITY_THRESHOLD = 0.80
#: When the runner-up scores within this of the top match, the two cannot be
#: told apart on appearance and neither may be presented as the answer.
AMBIGUITY_MARGIN = 0.02

_AMBIGUITY_NOTE = (
    "Near-identical appearance scores: other candidates are within "
    f"{AMBIGUITY_MARGIN:.2f} of the top match, so appearance alone cannot "
    "separate them. Confirm against another signal before acting.")


def flag_ambiguity(scored: list[dict]) -> list[dict]:
    """Mark results the appearance evidence cannot actually separate.

    Two silver hatchbacks embed almost identically, so a ranked list whose top
    scores are nearly equal has picked a winner the evidence does not support.
    The ambiguous candidates are kept rather than dropped - an operator shown
    "three near-identical candidates" is better served than one shown a single
    confident wrong answer - but every result carries the flag so the console
    can never render the top hit as if it stood alone.

    Mutates and returns the list in place; it is expected to be sorted with the
    highest similarity first.

    ponytail: ambiguity is judged against the top score only, so a tight
    cluster further down the list is not flagged. That cluster is not competing
    to be the answer, so it does not mislead in the same way.
    """
    # An epsilon, because a gap of exactly the margin must land on the
    # cautious side rather than on whichever side binary floats round it to.
    limit = AMBIGUITY_MARGIN + 1e-9
    # Both keys are set on every result, including a lone one, so no consumer
    # has to distinguish "unambiguous" from "never checked".
    top = scored[0]["similarity"] if scored else 0.0
    ambiguous = len(scored) >= 2 and (top - scored[1]["similarity"]) <= limit
    for row in scored:
        row["ambiguous"] = ambiguous and (top - row["similarity"]) <= limit
        row["ambiguity_note"] = _AMBIGUITY_NOTE if row["ambiguous"] else None
    return scored


class ReIdEncoder:
    """Turns vehicle crops into comparable appearance vectors."""

    def __init__(self):
        self._model = None
        self._transform = None
        self._lock = threading.Lock()

    def load(self) -> None:
        import torch
        import torchvision
        from torchvision.models import ResNet18_Weights

        weights = ResNet18_Weights.IMAGENET1K_V1
        model = torchvision.models.resnet18(weights=weights)
        model.fc = torch.nn.Identity()   # keep the pooled features, drop the classifier
        model.eval().to(config.DEVICE)
        self._model = model
        self._transform = weights.transforms()
        log.info("re-identification encoder ready (%d-d)", EMBED_DIM)

    @property
    def ready(self) -> bool:
        return self._model is not None

    def encode(self, crops: list) -> np.ndarray:
        """Embed a batch of BGR crops. Returns (n, 512) L2-normalised rows."""
        if not crops or self._model is None:
            return np.zeros((0, EMBED_DIM), dtype=np.float32)

        import cv2
        import torch

        tensors = []
        for crop in crops:
            if crop is None or crop.size == 0:
                tensors.append(torch.zeros(3, 224, 224))
                continue
            rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
            rgb = cv2.resize(rgb, (224, 224), interpolation=cv2.INTER_LINEAR)
            t = torch.from_numpy(rgb).permute(2, 0, 1).float() / 255.0
            # ImageNet normalisation, matching the pretrained weights
            t = (t - torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)) / \
                torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
            tensors.append(t)

        batch = torch.stack(tensors).to(config.DEVICE)
        with self._lock, torch.no_grad():
            feats = self._model(batch)
        feats = torch.nn.functional.normalize(feats, p=2, dim=1)
        return feats.cpu().numpy().astype(np.float32)


def similarity(a, b) -> float:
    """Cosine similarity between two L2-normalised embeddings."""
    if a is None or b is None:
        return 0.0
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    if va.size == 0 or vb.size == 0 or va.shape != vb.shape:
        return 0.0
    return float(np.dot(va, vb))


def rank_candidates(query_embedding, detections: list, top_k: int = 25) -> list[dict]:
    """Rank stored detections by appearance similarity to a query vehicle.

    `detections` are ORM rows carrying `.embedding`. Results are sorted most
    similar first and carry the score so the console can show it rather than
    presenting a match as fact.
    """
    scored = []
    for det in detections:
        if not det.embedding:
            continue
        s = similarity(query_embedding, det.embedding)
        if s >= SIMILARITY_THRESHOLD:
            scored.append({"detection": det, "similarity": round(s, 4)})
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    # Truncate first: ambiguity is about what the caller is shown, so it is
    # judged over the returned list rather than over candidates it never sees.
    return flag_ambiguity(scored[:top_k])


def _self_check() -> None:
    """Check the similarity maths without requiring the model or a GPU."""
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    c = np.array([0.0, 1.0, 0.0], dtype=np.float32)

    assert abs(similarity(a, b) - 1.0) < 1e-6, similarity(a, b)
    assert abs(similarity(a, c)) < 1e-6, similarity(a, c)
    assert similarity(a, None) == 0.0
    assert similarity(a, np.array([1.0, 0.0], dtype=np.float32)) == 0.0  # shape guard

    class FakeDet:
        def __init__(self, emb, name):
            self.embedding, self.name = emb, name

    dets = [FakeDet(list(a), "same"), FakeDet(list(c), "orthogonal"),
            FakeDet(None, "no-embedding")]
    ranked = rank_candidates(a, dets)
    assert len(ranked) == 1 and ranked[0]["detection"].name == "same", ranked
    # A lone result has nothing to be confused with.
    assert ranked[0]["ambiguous"] is False and ranked[0]["ambiguity_note"] is None

    # Two candidates scoring within the margin are both flagged: this is the
    # two-silver-hatchbacks case, where presenting the top hit implies a
    # confidence the evidence does not support.
    close = [{"similarity": 0.91}, {"similarity": 0.90}, {"similarity": 0.82}]
    flag_ambiguity(close)
    assert close[0]["ambiguous"] and close[1]["ambiguous"], close
    assert close[0]["ambiguity_note"], close[0]
    # The distant third is not part of the confusion and is not flagged.
    assert close[2]["ambiguous"] is False and close[2]["ambiguity_note"] is None

    # A clear winner is reported as one.
    clear = [{"similarity": 0.95}, {"similarity": 0.84}]
    flag_ambiguity(clear)
    assert not any(r["ambiguous"] for r in clear), clear

    # Exactly on the margin counts as ambiguous: the boundary should not be
    # resolved in favour of false confidence.
    edge = [{"similarity": 0.90}, {"similarity": 0.88}]
    flag_ambiguity(edge)
    assert edge[0]["ambiguous"] and edge[1]["ambiguous"], edge

    print("reid self-check passed")


if __name__ == "__main__":
    _self_check()
