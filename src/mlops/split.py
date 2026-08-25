"""Chronological split utilities for model training."""

from __future__ import annotations

import math

from src.contracts.mlops import SalesFeatureRow


def chronological_split(
    rows: list[SalesFeatureRow],
    test_fraction: float = 0.2,
    min_test_rows: int = 500,
) -> tuple[list[SalesFeatureRow], list[SalesFeatureRow]]:
    """Sort by order_date and split without shuffling."""
    if not 0 < test_fraction < 1:
        raise ValueError("test_fraction must be between 0 and 1")
    if min_test_rows <= 0:
        raise ValueError("min_test_rows must be positive")

    ordered_rows = sorted(rows, key=lambda row: row.order_date)
    proposed_test_rows = math.ceil(len(ordered_rows) * test_fraction)
    if proposed_test_rows < min_test_rows:
        raise ValueError(
            "Chronological test set is too small. "
            f"Need at least {min_test_rows} rows but only {proposed_test_rows} "
            f"would fall into the test split with test_fraction={test_fraction}."
        )
    if proposed_test_rows >= len(ordered_rows):
        raise ValueError("Chronological split leaves no training rows.")

    cutoff_index = len(ordered_rows) - proposed_test_rows
    train_rows = ordered_rows[:cutoff_index]
    test_rows = ordered_rows[cutoff_index:]
    return train_rows, test_rows


__all__ = ["chronological_split"]
