"""Batch seeding of future Sales predictions into the `Predictions` table.

POC rationale (see chat decision): generating "predicted Sales for the next
months" is a *batch* concern, not something the LLM should trigger on-demand
from the client — the LLM never touches the model directly (constitution
Principle I / `mlops_inference.md`), and doing N ad-hoc LLM-parsed calls at
dashboard-view time would add latency, cost, and a live-demo failure surface
for no benefit (the inputs are deterministic, not natural language). Instead,
this module runs a deterministic, LLM-free batch: for a handful of
representative historical (region, category) profiles, it predicts Sales for
each of the next `months_ahead` months and persists the results into
`Predictions` via the SAME `src.mlops.inference.predict_sales` path used by
the `predict-sales` CLI — no parallel prediction logic (FR-023 pattern).

Wired into `bootstrap` (best-effort — never fails bootstrap) so the
`Predictions` table already has forecast rows for Metabase/dashboards to
read immediately after `uv run python -m src.cli.main bootstrap`, without
depending on Postgres/model/LLM availability at view time.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from src.contracts.data_access import TableDef
from src.contracts.mlops import EnvironmentName, FeatureSet, PredictionInput, SalesFeatureRow
from src.data_access.interfaces import QueryProvider
from src.mlops.dataset import extract_feature_set
from src.mlops.inference import predict_sales
from src.mlops.predictions_store import (
    PredictionsRepository,
    ensure_predictions_table,
    predictions_table_def,
)
from src.mlops.registry import ArtifactRegistry, NoActiveModelError
from src.mlops.training import train_sales_models

DEFAULT_MONTHS_AHEAD = 6
DEFAULT_TOP_N_PROFILES = 10
# A representative non-zero discount applied to profiles whose historical
# group was discounted more often than not (simple heuristic — not a
# forecast of discount *policy*, just a plausible representative input).
_REPRESENTATIVE_DISCOUNT = Decimal("0.15")
_NO_DISCOUNT = Decimal("0.0")


@dataclass(frozen=True)
class PredictionProfile:
    """Representative historical (region, category) combination used as the
    template for every future-month forecast row for that combination."""

    ship_mode: str
    segment: str
    region: str
    market: str
    product_id: str
    sub_category: str
    category: str
    quantity: int
    discount: Decimal
    order_count: int  # historical frequency — ranking only, not persisted


def _build_profiles(feature_set: FeatureSet, top_n: int) -> list[PredictionProfile]:
    """Rank (region, category) combinations by historical order count and,
    for each of the top `top_n`, pick the most common value of every other
    categorical field plus the median quantity — a representative "typical
    order" for that combination, used as the forecast input template."""
    groups: dict[tuple[str, str], list[SalesFeatureRow]] = {}
    for row in feature_set.rows:
        groups.setdefault((row.region, row.category), []).append(row)

    ranked = sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)[:top_n]

    profiles: list[PredictionProfile] = []
    for (region, category), rows in ranked:
        quantities = sorted(r.quantity for r in rows)
        median_quantity = quantities[len(quantities) // 2]
        discounted_share = sum(1 for r in rows if r.has_discount) / len(rows)
        profiles.append(
            PredictionProfile(
                ship_mode=Counter(r.ship_mode for r in rows).most_common(1)[0][0],
                segment=Counter(r.segment for r in rows).most_common(1)[0][0],
                region=region,
                market=Counter(r.market for r in rows).most_common(1)[0][0],
                product_id=Counter(r.product_id for r in rows).most_common(1)[0][0],
                sub_category=Counter(r.sub_category for r in rows).most_common(1)[0][0],
                category=category,
                quantity=median_quantity,
                discount=_REPRESENTATIVE_DISCOUNT if discounted_share >= 0.5 else _NO_DISCOUNT,
                order_count=len(rows),
            )
        )
    return profiles


def _future_month_starts(months_ahead: int, *, now: datetime | None = None) -> list[datetime]:
    """First day of each of the next `months_ahead` months (UTC), starting
    with next month — the current, partially-elapsed month is skipped."""
    base = now or datetime.now(timezone.utc)
    year, month = base.year, base.month
    months: list[datetime] = []
    for _ in range(months_ahead):
        month += 1
        if month > 12:
            month = 1
            year += 1
        months.append(datetime(year, month, 1, tzinfo=timezone.utc))
    return months


def _build_prediction_inputs(
    profiles: list[PredictionProfile], months_ahead: int, *, now: datetime | None = None
) -> list[PredictionInput]:
    future_dates = _future_month_starts(months_ahead, now=now)
    return [
        PredictionInput(
            order_date=order_date,
            ship_mode=profile.ship_mode,
            segment=profile.segment,
            region=profile.region,
            market=profile.market,
            product_id=profile.product_id,
            sub_category=profile.sub_category,
            category=profile.category,
            quantity=profile.quantity,
            discount=profile.discount,
        )
        for profile in profiles
        for order_date in future_dates
    ]


def _has_future_predictions(
    query_provider: QueryProvider, run_id: str, *, now: datetime | None = None
) -> bool:
    """Best-effort idempotency check: does `Predictions` already hold
    future-dated rows for the currently active `run_id`? Returns `False`
    (never blocks seeding) if the table can't be read for any reason — e.g.
    it was just created and is empty."""
    base = now or datetime.now(timezone.utc)
    try:
        rows = query_provider.execute_readonly_query(
            'SELECT * FROM "Predictions"', predictions_table_def()
        )
    except Exception:
        return False
    for row in rows:
        if row.data.get("Run ID") != run_id:
            continue
        order_date = row.data.get("Order Date")
        if isinstance(order_date, datetime):
            comparable = order_date if order_date.tzinfo else order_date.replace(tzinfo=timezone.utc)
            if comparable >= base:
                return True
    return False


def _resolve_or_bootstrap_active_run(
    repo: QueryProvider,
    orders_table_def: TableDef,
    registry: ArtifactRegistry,
    environment: EnvironmentName,
):
    """Return the active run for `environment`, training + promoting a fresh
    pair of models (picking the better RMSE) if none is active yet — a
    first-run convenience so `bootstrap` alone is enough to produce
    forecasts, without requiring a separate manual
    `train-sales-model`/`promote-sales-model` step."""
    active_entry = registry.resolve_active_run(environment)
    if active_entry is not None:
        return active_entry

    linear_entry, catboost_entry = train_sales_models(repo, orders_table_def, registry)
    better = linear_entry if linear_entry.metrics.rmse <= catboost_entry.metrics.rmse else catboost_entry

    # Promote through the gated path (dev -> staging -> prod) so a direct
    # request for `prod` doesn't trip the staging gate (FR-018).
    ordered_envs: list[EnvironmentName] = ["dev", "staging", "prod"]
    target_index = ordered_envs.index(environment)
    for env in ordered_envs[: target_index + 1]:
        registry.promote(run_id=better.run_id, environment=env)

    return registry.resolve_active_run(environment)


def seed_future_predictions(
    repo: PredictionsRepository,
    orders_table_def: TableDef,
    registry: ArtifactRegistry,
    environment: EnvironmentName = "prod",
    months_ahead: int = DEFAULT_MONTHS_AHEAD,
    top_n_profiles: int = DEFAULT_TOP_N_PROFILES,
    force: bool = False,
) -> int:
    """Populate `Predictions` with deterministic forecasts for the next
    `months_ahead` months, using the model promoted (or freshly
    trained+promoted) in `environment`. Returns the number of prediction
    rows written (`0` when skipped because future rows for the active run
    already exist and `force=False`).
    """
    ensure_predictions_table(repo)

    active_entry = _resolve_or_bootstrap_active_run(repo, orders_table_def, registry, environment)
    if active_entry is None:  # pragma: no cover - defensive, should not happen
        raise NoActiveModelError(
            f"Could not resolve or create an active model for environment {environment!r}."
        )

    if not force and _has_future_predictions(repo, active_entry.run_id):
        return 0

    feature_set = extract_feature_set(repo, orders_table_def)
    profiles = _build_profiles(feature_set, top_n_profiles)
    prediction_inputs = _build_prediction_inputs(profiles, months_ahead)

    for prediction_input in prediction_inputs:
        predict_sales(registry, environment, prediction_input, repo)

    return len(prediction_inputs)


__all__ = [
    "DEFAULT_MONTHS_AHEAD",
    "DEFAULT_TOP_N_PROFILES",
    "PredictionProfile",
    "seed_future_predictions",
]
