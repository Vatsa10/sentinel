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
    return scored[:top_k]


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

    print("reid self-check passed")


if __name__ == "__main__":
    _self_check()
