"""Metrics with uncertainty. Every headline number gets an interval or a test,
because a 3-point gain on 60 cases is usually noise."""
from __future__ import annotations

import math

import numpy as np
from scipy import stats


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    if n == 0:
        return (float("nan"),) * 3
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def mcnemar(a_ok: list[bool], b_ok: list[bool]) -> dict:
    """Paired exact McNemar test on per-case success of two systems."""
    b01 = sum(1 for a, b in zip(a_ok, b_ok) if not a and b)
    b10 = sum(1 for a, b in zip(a_ok, b_ok) if a and not b)
    n = b01 + b10
    p = stats.binomtest(b01, n, 0.5).pvalue if n else 1.0
    return {"only_b_ok": b01, "only_a_ok": b10, "p_value": float(p)}


def auroc(scores: list[float], labels: list[int]) -> float:
    pos = [s for s, l in zip(scores, labels) if l == 1]
    neg = [s for s, l in zip(scores, labels) if l == 0]
    if not pos or not neg:
        return float("nan")
    u = stats.mannwhitneyu(pos, neg, alternative="two-sided").statistic
    return float(u / (len(pos) * len(neg)))


def bootstrap_ci(fn, *arrays, n: int = 1000, seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    m = len(arrays[0])
    vals = []
    for _ in range(n):
        idx = rng.integers(0, m, m)
        try:
            v = fn(*[[a[i] for i in idx] for a in arrays])
            if not math.isnan(v):
                vals.append(v)
        except Exception:
            pass
    if not vals:
        return float("nan"), float("nan")
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def platt_fit(scores: list[float], labels: list[int]) -> tuple[float, float]:
    """Logistic calibration p = 1/(1+exp(-(a*s+b))). Fit on TRAIN only."""
    from scipy.optimize import minimize

    s = np.asarray(scores, float)
    y = np.asarray(labels, float)

    def nll(w):
        z = w[0] * s + w[1]
        return float(np.sum(np.logaddexp(0, -z) * y + np.logaddexp(0, z) * (1 - y)))

    r = minimize(nll, x0=[0.3, 0.0], method="L-BFGS-B")
    return float(r.x[0]), float(r.x[1])


def platt_apply(scores: list[float], a: float, b: float) -> list[float]:
    return [1 / (1 + math.exp(-(a * s + b))) for s in scores]


def ece(probs: list[float], labels: list[int], bins: int = 10) -> float:
    p = np.asarray(probs)
    y = np.asarray(labels)
    total = 0.0
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        m = (p >= lo) & (p < hi if i < bins - 1 else p <= hi)
        if m.any():
            total += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(total)


def brier(probs: list[float], labels: list[int]) -> float:
    return float(np.mean((np.asarray(probs) - np.asarray(labels)) ** 2))


def coverage_accuracy(rows: list[dict]) -> list[dict]:
    """Selective prediction curve: answer only when |points| >= t."""
    out = []
    for t in range(0, 13):
        answered = [r for r in rows if r.get("pred3") and not r.get("abstained") and abs(r.get("points") or 0) >= t]
        if not rows:
            break
        cov = len(answered) / len(rows)
        acc = sum(r["pred3"] == r["truth"] for r in answered) / len(answered) if answered else float("nan")
        out.append({"min_abs_points": t, "coverage": round(cov, 3), "accuracy": round(acc, 3) if answered else None})
    return out


def pct(xs: list[float], q: float) -> float:
    return float(np.percentile(xs, q)) if xs else float("nan")
