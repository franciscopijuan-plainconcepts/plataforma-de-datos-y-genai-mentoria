"""Physical test schema, canonical model loading and a pinned historical reference."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from src.contracts.data_access import ColumnDef, ForeignKeyDef, LogicalType, TableDef
from src.contracts.dictionary import (
    DataDictionaryDocument, DictionaryEntry, RelationshipEntry, TableDictionary,
)
from src.contracts.semantic_layer import SemanticLayerDocument
from src.data_engineering.semantic_layer.ossie import import_ossie, read_ossie_document


LEGACY_REVISION = "f465e4832291422f396ef1794101c91965a1e395"
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _build_test_table_defs() -> list[TableDef]:
    orders_columns = [
        ColumnDef(name="Row ID", logical_type=LogicalType.INTEGER, nullable=False, is_primary_key=True),
        ColumnDef(name="Order ID", logical_type=LogicalType.STRING, max_length=50, nullable=False),
        ColumnDef(name="Order Date", logical_type=LogicalType.TIMESTAMP, nullable=False),
        ColumnDef(name="Ship Date", logical_type=LogicalType.TIMESTAMP, nullable=False),
        ColumnDef(name="Region", logical_type=LogicalType.STRING, max_length=50, nullable=False),
        ColumnDef(name="Country", logical_type=LogicalType.STRING, max_length=100, nullable=False),
        ColumnDef(name="City", logical_type=LogicalType.STRING, max_length=100, nullable=False),
        ColumnDef(name="State", logical_type=LogicalType.STRING, max_length=100, nullable=False),
        ColumnDef(name="Postal Code", logical_type=LogicalType.STRING, max_length=20, nullable=True),
        ColumnDef(name="Customer ID", logical_type=LogicalType.STRING, max_length=50, nullable=False),
        ColumnDef(name="Product ID", logical_type=LogicalType.STRING, max_length=50, nullable=False),
        ColumnDef(name="Market", logical_type=LogicalType.STRING, max_length=20, nullable=False),
        ColumnDef(name="Segment", logical_type=LogicalType.STRING, max_length=20, nullable=False),
        ColumnDef(name="Category", logical_type=LogicalType.STRING, max_length=30, nullable=False),
        ColumnDef(name="Sub-Category", logical_type=LogicalType.STRING, max_length=30, nullable=False),
        ColumnDef(name="Ship Mode", logical_type=LogicalType.STRING, max_length=20, nullable=False),
        ColumnDef(name="Order Priority", logical_type=LogicalType.STRING, max_length=20, nullable=False),
        ColumnDef(name="Customer Name", logical_type=LogicalType.STRING, max_length=100, nullable=False),
        ColumnDef(name="Product Name", logical_type=LogicalType.STRING, max_length=300, nullable=False),
        ColumnDef(name="Sales", logical_type=LogicalType.DECIMAL, precision=12, scale=4, nullable=False),
        ColumnDef(name="Profit", logical_type=LogicalType.DECIMAL, precision=12, scale=4, nullable=False),
        ColumnDef(name="Discount", logical_type=LogicalType.DECIMAL, precision=5, scale=4, nullable=False),
        ColumnDef(name="Shipping Cost", logical_type=LogicalType.DECIMAL, precision=12, scale=4, nullable=False),
        ColumnDef(name="Quantity", logical_type=LogicalType.INTEGER, nullable=False),
    ]
    returns_columns = [
        ColumnDef(name="Return ID", logical_type=LogicalType.INTEGER, nullable=False, is_primary_key=True),
        ColumnDef(name="Returned", logical_type=LogicalType.STRING, max_length=5, nullable=False),
        ColumnDef(name="Order ID", logical_type=LogicalType.STRING, max_length=50, nullable=False),
        ColumnDef(name="Region", logical_type=LogicalType.STRING, max_length=50, nullable=False),
    ]
    people_columns = [
        ColumnDef(name="Person", logical_type=LogicalType.STRING, max_length=100, nullable=False,
                  is_primary_key=True),
        ColumnDef(name="Region", logical_type=LogicalType.STRING, max_length=50, nullable=False),
    ]
    return [
        TableDef(name="Orders", columns=orders_columns, description="Orders table.",
                 foreign_keys=[
                     ForeignKeyDef(column="Region", references_table="People", references_column="Region"),
                 ]),
        TableDef(name="Returns", columns=returns_columns, description="Returns table.",
                 foreign_keys=[
                     ForeignKeyDef(column="Order ID", references_table="Orders", references_column="Order ID"),
                     ForeignKeyDef(column="Region", references_table="People", references_column="Region"),
                 ]),
        TableDef(name="People", columns=people_columns, description="People table."),
    ]


def _build_test_dictionary(table_defs: list[TableDef]) -> DataDictionaryDocument:
    tables: list[TableDictionary] = []
    for table in table_defs:
        columns = [
            DictionaryEntry(
                name=column.name,
                business_description=f"Column {column.name}",
                logical_type=column.logical_type,
                postgres_type=column.logical_type.value,
                nullable=column.nullable,
                is_key=column.is_primary_key,
                key_kind="primary" if column.is_primary_key else None,
                allowed_values=None, min_value=None, max_value=None,
                unique_count=0, data_quality_notes=[],
            )
            for column in table.columns
        ]
        tables.append(TableDictionary(
            name=table.name, kaggle_label="label", purpose=f"Purpose of {table.name}",
            primary_key=[column.name for column in table.columns if column.is_primary_key],
            relationships=[
                RelationshipEntry(
                    from_column=key.column, to_table=key.references_table,
                    to_column=key.references_column, cardinality="N:1",
                )
                for key in table.foreign_keys
            ],
            columns=columns,
        ))
    return DataDictionaryDocument(
        generated_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        source_file="test", source_sha256="test-source-sha", tables=tables,
    )


def _canonical_model_text() -> str:
    return read_ossie_document(_REPO_ROOT / "semantic/sales.ossie.yaml")


def _load_test_semantic_layer(dictionary: DataDictionaryDocument | None = None) -> SemanticLayerDocument:
    if dictionary is None:
        dictionary = _build_test_dictionary(_build_test_table_defs())
    return import_ossie(_canonical_model_text(), dictionary)


def _load_legacy_semantic_layer() -> SemanticLayerDocument:
    """Read immutable history for comparisons only; never build a second authority."""
    result = subprocess.run(
        ["git", "show", f"{LEGACY_REVISION}:.artifacts/semantic_layer.json"],
        cwd=_REPO_ROOT, capture_output=True, text=True, check=True, timeout=10,
    )
    raw = json.loads(result.stdout)
    raw["generated_at"] = "2026-01-01T00:00:00Z"
    return SemanticLayerDocument.model_validate(raw)
