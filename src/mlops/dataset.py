"""Dataset extraction for sales-model training."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from src.data_access.sql_validator import validate_sql
from src.contracts.data_access import OrderRow, TableDef
from src.contracts.mlops import FeatureSet
from src.data_access.interfaces import QueryProvider
from src.mlops.features import (
    derive_training_row,
    stable_row_payload,
    stable_row_sort_key,
)


class DatasetExtractionError(RuntimeError):
    """Raised when training data cannot be extracted safely."""


_ORDERS_EXTRACTION_SQL = (
    'SELECT * FROM "Orders" ORDER BY "Order Date" ASC, "Order ID" ASC, "Row ID" ASC'
)


def extract_feature_set(
    query_provider: QueryProvider, orders_table_def: TableDef
) -> FeatureSet:
    """Extract Orders rows through QueryProvider and map them to FeatureSet."""
    validation = validate_sql(_ORDERS_EXTRACTION_SQL, orders_table_def)
    if not validation.accepted:
        raise DatasetExtractionError(
            f"Orders extraction SQL failed validation: {validation.reason}"
        )

    try:
        query_rows = query_provider.execute_readonly_query(
            _ORDERS_EXTRACTION_SQL, orders_table_def
        )
    except Exception as exc:  # pragma: no cover - exercised via integration test
        raise DatasetExtractionError(
            f"Could not extract Orders training data via QueryProvider: {exc}"
        ) from exc

    rows = [
        derive_training_row(OrderRow.model_validate(query_row.data))
        for query_row in query_rows
    ]
    sorted_rows = sorted(rows, key=stable_row_sort_key)
    payload = [stable_row_payload(row) for row in sorted_rows]
    data_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return FeatureSet(
        rows=sorted_rows,
        data_hash=data_hash,
        extracted_at=datetime.now(timezone.utc),
        source_table="Orders",
        row_count=len(sorted_rows),
    )


__all__ = ["DatasetExtractionError", "extract_feature_set"]
