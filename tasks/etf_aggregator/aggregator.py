from __future__ import annotations

import pandas as pd

from .constants import RATING_THRESHOLDS


def weighted_average(values: pd.Series, weights: pd.Series) -> float:
    """Calculate a normalized weighted average, ignoring rows with missing values."""
    numeric_values = pd.to_numeric(values, errors="coerce")
    numeric_weights = pd.to_numeric(weights, errors="coerce")

    valid = numeric_values.notna() & numeric_weights.notna()
    if not valid.any():
        return float("nan")

    v, w = numeric_values[valid], numeric_weights[valid]
    denominator = w.sum()

    if denominator == 0 or pd.isna(denominator):
        return float("nan")

    return float((v * w).sum() / denominator)


def weighted_harmonic_mean(values: pd.Series, weights: pd.Series) -> float:
    """Weighted harmonic mean -- the correct way to aggregate ratio metrics
    like P/E across holdings (a plain weighted average of P/E ratios
    over-weights high-multiple stocks).

    Implemented as the inverse of the current-method weighted average of
    1/x: invert each value, weighted_average() the inverted values, then
    invert the result back.
    """
    numeric_values = pd.to_numeric(values, errors="coerce")
    numeric_weights = pd.to_numeric(weights, errors="coerce")

    valid = numeric_values.notna() & numeric_weights.notna() & (numeric_values > 0)
    if not valid.any():
        return float("nan")

    inverted_values = 1.0 / numeric_values[valid]
    avg_of_inverted = weighted_average(inverted_values, numeric_weights[valid])

    if pd.isna(avg_of_inverted) or avg_of_inverted == 0:
        return float("nan")

    return float(1.0 / avg_of_inverted)


def rating_label(score: float) -> str:
    """Map a numerical rating score to its threshold label."""
    if pd.isna(score):
        return ""

    for upper_bound, label in RATING_THRESHOLDS:
        if score <= upper_bound:
            return label

    return ""
