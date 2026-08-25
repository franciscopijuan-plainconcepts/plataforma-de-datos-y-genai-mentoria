"""Unit tests for the high-cardinality frequency encoder."""

from __future__ import annotations

import numpy as np

from src.mlops.encoding import FrequencyRareBucketEncoder


def test_frequency_encoder_learns_known_frequencies_and_rare_bucket() -> None:
    encoder = FrequencyRareBucketEncoder(min_frequency=0.3)
    transformed = encoder.fit_transform(
        [["a"], ["a"], ["a"], ["b"], ["c"]]
    )
    assert np.isclose(transformed[0, 0], 0.6)
    assert np.isclose(transformed[3, 0], 0.4)
    assert np.isclose(transformed[4, 0], 0.4)


def test_frequency_encoder_maps_unseen_category_to_other_bucket() -> None:
    encoder = FrequencyRareBucketEncoder(min_frequency=0.3)
    encoder.fit([["a"], ["a"], ["a"], ["b"], ["c"]])
    transformed = encoder.transform([["never-seen"]])
    assert np.isclose(transformed[0, 0], 0.4)
