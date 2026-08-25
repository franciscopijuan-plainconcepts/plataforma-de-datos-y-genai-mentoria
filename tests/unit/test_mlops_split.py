"""Unit tests for chronological train/test splitting."""

from __future__ import annotations

import pytest

from src.mlops.split import chronological_split
from tests.unit._mlops_support import sample_training_rows


def test_chronological_split_keeps_rows_in_time_order() -> None:
    rows = list(reversed(sample_training_rows()))
    train_rows, test_rows = chronological_split(rows, test_fraction=0.25, min_test_rows=5)
    assert max(row.order_date for row in train_rows) < min(row.order_date for row in test_rows)
    assert len(train_rows) == 15
    assert len(test_rows) == 5


def test_chronological_split_raises_for_too_small_test_set() -> None:
    rows = sample_training_rows()[:10]
    with pytest.raises(ValueError, match="Chronological test set is too small"):
        chronological_split(rows, test_fraction=0.2, min_test_rows=5)
