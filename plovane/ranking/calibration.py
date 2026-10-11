"""Isotonic calibration.

Calibration is what makes a relevance cutoff trustworthy. A ranker's raw score
orders candidates correctly and means nothing in absolute terms, so a cutoff
placed on it is arbitrary -- and the spec's whole "return nothing rather than a
poor match" behaviour depends on the cutoff being meaningful. After
calibration, a score of 0.8 means roughly an 80% chance the user accepts it,
and the cutoff becomes a decision anyone can reason about.

Isotonic regression is the right tool because it assumes only that higher
scores should map to higher probabilities -- it never reverses the ranker's
order -- and fits any monotone shape, including the flat tails a ranker
typically produces. The cost is that it needs a reasonable amount of held-out
data and will happily overfit a small set, so it is fitted on a dedicated
held-out split, never on the data the rankers trained on.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def pool_adjacent_violators(
    x: np.ndarray, y: np.ndarray, weights: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Fit a non-decreasing step function to ``(x, y)`` by PAVA.

    Returns ``(thresholds, values)``: the sorted x positions and the fitted
    value at each. Ties in x are averaged before pooling, so duplicate scores
    cannot produce a function that steps in two directions at one point.
    """
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    y = np.asarray(y, dtype=np.float64).reshape(-1)
    if x.shape != y.shape:
        raise ValueError(f"x and y must align, got {x.shape} and {y.shape}")
    if x.size == 0:
        raise ValueError("nothing to fit")
    w = np.ones_like(x) if weights is None else np.asarray(weights, dtype=np.float64).reshape(-1)
    if w.shape != x.shape:
        raise ValueError(f"weights must align with x, got {w.shape} and {x.shape}")

    order = np.argsort(x, kind="stable")
    x, y, w = x[order], y[order], w[order]

    # Average duplicate x positions into a single point.
    uniq_x, inverse = np.unique(x, return_inverse=True)
    sum_wy = np.bincount(inverse, weights=w * y, minlength=uniq_x.size)
    sum_w = np.bincount(inverse, weights=w, minlength=uniq_x.size)
    values = sum_wy / sum_w

    # Pool adjacent violators: merge any block whose value dips below its
    # predecessor, repeatedly, until the sequence is non-decreasing.
    block_values: list[float] = []
    block_weights: list[float] = []
    block_ends: list[int] = []
    for i in range(uniq_x.size):
        block_values.append(float(values[i]))
        block_weights.append(float(sum_w[i]))
        block_ends.append(i)
        while len(block_values) > 1 and block_values[-2] > block_values[-1]:
            v2, w2 = block_values.pop(), block_weights.pop()
            end = block_ends.pop()
            v1, w1 = block_values.pop(), block_weights.pop()
            block_ends.pop()
            total = w1 + w2
            block_values.append((v1 * w1 + v2 * w2) / total)
            block_weights.append(total)
            block_ends.append(end)

    fitted = np.empty(uniq_x.size, dtype=np.float64)
    start = 0
    for value, end in zip(block_values, block_ends, strict=True):
        fitted[start : end + 1] = value
        start = end + 1

    return uniq_x, fitted


class IsotonicCalibrator:
    """Maps a ranker score to a calibrated acceptance probability."""

    def __init__(self, *, clip: tuple[float, float] = (0.0, 1.0)) -> None:
        self.clip = clip
        self.thresholds: np.ndarray | None = None
        self.values: np.ndarray | None = None

    @property
    def fitted(self) -> bool:
        return self.thresholds is not None

    def fit(
        self, scores: np.ndarray, accepted: np.ndarray, *, weights: np.ndarray | None = None
    ) -> IsotonicCalibrator:
        """Fit on a held-out set of ``(score, accepted)`` observations.

        ``accepted`` is the live outcome -- 1 if the user kept the insert, 0 if
        they never took it or reverted it. Fitting on anything the rankers
        trained on would give scores that look well calibrated on paper and
        are overconfident in production.
        """
        thresholds, values = pool_adjacent_violators(scores, accepted, weights)
        self.thresholds = thresholds
        self.values = np.clip(values, *self.clip)
        return self

    def predict(self, scores: np.ndarray) -> np.ndarray:
        """Interpolate between fitted points; clamp outside the fitted range.

        Linear interpolation rather than a bare step function, so two scores
        that differ slightly do not collapse to the same probability and lose
        the ranker's ordering within a step.
        """
        if self.thresholds is None or self.values is None:
            raise RuntimeError("IsotonicCalibrator is not fitted; call fit() or load()")
        scores = np.asarray(scores, dtype=np.float64).reshape(-1)
        if scores.size == 0:
            return np.zeros(0, dtype=np.float32)
        if self.thresholds.size == 1:
            # One distinct training score says nothing about the shape of the
            # curve, so the single fitted rate is the only honest answer.
            return np.full(scores.shape, float(self.values[0]), dtype=np.float32)
        out = np.interp(scores, self.thresholds, self.values)
        return np.clip(out, *self.clip).astype(np.float32)

    def save(self, path: str | Path) -> None:
        if self.thresholds is None or self.values is None:
            raise RuntimeError("IsotonicCalibrator is not fitted")
        np.savez(str(path), thresholds=self.thresholds, values=self.values, clip=np.asarray(self.clip))

    def load(self, path: str | Path) -> IsotonicCalibrator:
        data = np.load(str(path))
        self.thresholds = data["thresholds"]
        self.values = data["values"]
        self.clip = (float(data["clip"][0]), float(data["clip"][1]))
        return self
