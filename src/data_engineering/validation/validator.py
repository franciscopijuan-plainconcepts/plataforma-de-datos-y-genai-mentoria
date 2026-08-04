"""Reproducibility validation for the baseline warehouse (FR-005 / SC-003).

Implements the deterministic re-bootstrap validation: capture a snapshot of
the warehouse schema + row counts, run teardown -> bootstrap, then re-capture
and compare. Drift between runs indicates a non-reproducible load.

A `ReproducibilityReport` captures the per-run snapshot and the diff; it is
consumed by the integration test (tests/integration/test_reproducibility.py)
and by the `validate` command (provenance side).

Reference: specs/001-data-genai-platform-baseline/spec.md (FR-005, SC-003)
            specs/001-data-genai-platform-baseline/quickstart.md § D
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.data_access.adapters.postgres.connection import PostgresConfig
from src.data_access.adapters.postgres.repository import PostgresRepository
from src.data_access.interfaces import DataProvider, SchemaProvider


# Canonical expected row counts (EDA-derived, see research.md Part A).
_EXPECTED_ROW_COUNTS: dict[str, int] = {
    "Orders": 51290,
    "Returns": 2033,
    "People": 24,
}


@dataclass(frozen=True)
class SchemaSnapshot:
    """A point-in-time snapshot of the warehouse schema + row counts.

    - `columns_by_table` maps table name -> tuple of (column_name,
      data_type) pairs, ordered as they appear in information_schema. This
      is engine-neutral enough to detect drift: if a re-bootstrap produces a
      different column set or order, the snapshot differs and reproducibility
      is broken.
    - `row_counts` maps table name -> live row count.
    """

    columns_by_table: dict[str, tuple[tuple[str, str], ...]]
    row_counts: dict[str, int]


@dataclass(frozen=True)
class ReproducibilityReport:
    """Outcome of a reproducibility check (compare two snapshots)."""

    before: SchemaSnapshot
    after: SchemaSnapshot
    schema_matches: bool
    row_counts_match: bool
    drift_details: list[str] = field(default_factory=list)

    @property
    def is_reproducible(self) -> bool:
        return self.schema_matches and self.row_counts_match


def capture_snapshot(
    schema_provider: SchemaProvider,
    data_provider: DataProvider,
) -> SchemaSnapshot:
    """Capture a snapshot of the current warehouse schema + row counts.

    Requires a live connection (the PG adapter). Uses `information_schema`
    via the data-access layer's read path — but since the Protocols do not
    expose raw SQL, we snapshot row counts via `count_rows` and column
    metadata via the PG adapter's `list_tables` + an information_schema read
    done inside the adapter (the read path stays engine-specific, confined to
    the adapter, per constitution Principle III).
    """
    # Cast to the concrete adapter to read information_schema (engine-specific
    # metadata reads are permitted inside the adapter; this keeps the read
    # path honest while not adding a generic `execute_sql` to the Protocol).
    if not isinstance(schema_provider, PostgresRepository):
        raise TypeError(
            "capture_snapshot requires a PostgresRepository (engine-specific "
            "information_schema read); got "
            f"{type(schema_provider).__name__}"
        )

    repo = schema_provider  # alias for clarity (also implements DataProvider)
    assert isinstance(repo, PostgresRepository)

    columns_by_table: dict[str, tuple[tuple[str, str], ...]] = {}
    row_counts: dict[str, int] = {}
    for table_name in _EXPECTED_ROW_COUNTS:
        if not repo.table_exists(table_name):
            columns_by_table[table_name] = tuple()
            row_counts[table_name] = 0
            continue
        # Column metadata via information_schema (engine-specific).
        with repo._conn.cursor() as cur:  # noqa: SLF001 — adapter-internal access
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s "
                "ORDER BY ordinal_position",
                (table_name,),
            )
            rows = cur.fetchall()
        columns_by_table[table_name] = tuple(
            (str(r["column_name"]), str(r["data_type"])) for r in rows if r
        )
        row_counts[table_name] = data_provider.count_rows(table_name)

    return SchemaSnapshot(
        columns_by_table=columns_by_table, row_counts=row_counts
    )


def compare_snapshots(before: SchemaSnapshot, after: SchemaSnapshot) -> ReproducibilityReport:
    """Compare two `SchemaSnapshot`s for schema + row-count drift."""
    drift: list[str] = []

    tables_before = set(before.columns_by_table.keys())
    tables_after = set(after.columns_by_table.keys())
    if tables_before != tables_after:
        drift.append(
            f"Table set changed: before={sorted(tables_before)} "
            f"after={sorted(tables_after)}"
        )

    schema_matches = True
    for table in sorted(tables_before & tables_after):
        cols_before = before.columns_by_table.get(table, tuple())
        cols_after = after.columns_by_table.get(table, tuple())
        if cols_before != cols_after:
            schema_matches = False
            drift.append(
                f"Schema drift in {table}: before={cols_before} after={cols_after}"
            )

    row_counts_match = True
    for table in sorted(tables_before & tables_after):
        rb = before.row_counts.get(table, -1)
        ra = after.row_counts.get(table, -1)
        if rb != ra:
            row_counts_match = False
            drift.append(
                f"Row-count drift in {table}: before={rb} after={ra}"
            )

    return ReproducibilityReport(
        before=before,
        after=after,
        schema_matches=schema_matches,
        row_counts_match=row_counts_match,
        drift_details=drift,
    )


def capture_snapshot_from_config(config: PostgresConfig | None = None) -> SchemaSnapshot:
    """Convenience: open a PG repository, capture a snapshot, close it."""
    from src.data_access.adapters.postgres.connection import connect

    cfg = config if config is not None else PostgresConfig.from_env()
    conn = connect(cfg)
    try:
        repo = PostgresRepository(conn=conn)
        return capture_snapshot(repo, repo)
    finally:
        conn.close()


__all__ = [
    "SchemaSnapshot",
    "ReproducibilityReport",
    "capture_snapshot",
    "compare_snapshots",
    "capture_snapshot_from_config",
]
