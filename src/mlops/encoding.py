"""Custom encoders for linear-model preprocessing."""

from __future__ import annotations

from collections import Counter
from typing import Self, cast

import numpy as np
from numpy.typing import NDArray
from sklearn.base import BaseEstimator, TransformerMixin


class FrequencyRareBucketEncoder(BaseEstimator, TransformerMixin):  # type: ignore[misc]
    """Encode high-cardinality categories into deterministic frequency columns."""

    def __init__(self, min_frequency: float = 0.001) -> None:
        self.min_frequency = min_frequency
        self.frequencies_: list[dict[str, float]] = []
        self.other_frequency_: list[float] = []
        self.seen_categories_: list[set[str]] = []

    def fit(self, X: object, y: object = None) -> Self:
        del y
        matrix = self._to_matrix(X)
        row_count = len(matrix)
        self.frequencies_ = []
        self.other_frequency_ = []
        self.seen_categories_ = []
        if row_count == 0:
            return self

        threshold = max(self.min_frequency, 0.0)
        for column_values in zip(*matrix):
            string_values = [str(value) for value in column_values]
            counts = Counter(string_values)
            frequencies: dict[str, float] = {}
            other_frequency = 0.0
            for category, count in counts.items():
                relative_frequency = count / row_count
                if relative_frequency < threshold:
                    other_frequency += relative_frequency
                else:
                    frequencies[category] = relative_frequency
            self.frequencies_.append(frequencies)
            self.other_frequency_.append(other_frequency)
            self.seen_categories_.append(set(counts))
        return self

    def transform(self, X: object) -> NDArray[np.float64]:
        matrix = self._to_matrix(X)
        if not self.frequencies_ and matrix:
            raise ValueError("FrequencyRareBucketEncoder must be fitted before transform().")
        transformed: list[list[float]] = []
        for row in matrix:
            encoded_row: list[float] = []
            for index, value in enumerate(row):
                key = str(value)
                encoded_row.append(
                    self.frequencies_[index].get(key, self.other_frequency_[index])
                )
            transformed.append(encoded_row)
        return np.asarray(transformed, dtype=np.float64)

    def get_feature_names_out(self, input_features: list[str] | None = None) -> NDArray[np.str_]:
        if input_features is None:
            names = [f"feature_{index}" for index in range(len(self.frequencies_))]
        else:
            names = [f"{name}_frequency" for name in input_features]
        return np.asarray(names, dtype=np.str_)

    def _to_matrix(self, X: object) -> list[list[object]]:
        if isinstance(X, np.ndarray):
            if X.ndim == 1:
                return [[cast(object, value)] for value in X.tolist()]
            return [list(cast(list[object], row)) for row in X.tolist()]
        if isinstance(X, list):
            return [list(cast(list[object], row)) for row in X]
        raise TypeError(f"Unsupported matrix type for FrequencyRareBucketEncoder: {type(X)!r}")


__all__ = ["FrequencyRareBucketEncoder"]
