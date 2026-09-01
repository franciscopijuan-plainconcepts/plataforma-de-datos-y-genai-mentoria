"""CLI-level unit tests for MLOps commands."""

from __future__ import annotations

import shutil
from collections.abc import Generator
from pathlib import Path

import pytest

from src.cli.main import main
from src.contracts.mlops import EvaluationMetrics
from src.mlops.linear_model import fit_linear_model
from src.mlops.registry import ArtifactRegistry
from tests.unit._mlops_support import sample_training_rows, registry_test_root


@pytest.fixture()
def cli_registry(monkeypatch: pytest.MonkeyPatch) -> Generator[ArtifactRegistry, None, None]:
    root = registry_test_root('mlops-cli-tests')
    if root.exists():
        shutil.rmtree(root)
    registry = ArtifactRegistry(root=root)
    monkeypatch.setattr('src.mlops.registry.ArtifactRegistry', lambda root=Path('.artifacts/mlops'): registry)
    yield registry
    if root.exists():
        shutil.rmtree(root)


def _persist_linear_run(registry: ArtifactRegistry) -> str:
    rows = sample_training_rows()
    model, metadata = fit_linear_model(rows[:15], {"fit_intercept": True})
    metadata = metadata.model_copy(update={"data_hash": "hash-cli", "test_row_count": 5, "split_cutoff_date": rows[15].order_date})
    metrics = EvaluationMetrics(rmse=1.0, mae=0.8, r2=0.5, test_row_count=5, split_cutoff_date=rows[15].order_date)
    entry = registry.persist_run("linear_regression", model, metadata, metrics)
    return entry.run_id


def test_promote_sales_model_rejects_unknown_run(cli_registry: ArtifactRegistry, capsys: pytest.CaptureFixture[str]) -> None:
    _persist_linear_run(cli_registry)
    with pytest.raises(SystemExit) as exc_info:
        main(["promote-sales-model", "--run-id", "does-not-exist", "--env", "dev"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "Available run_ids" in captured.err


def test_promote_sales_model_enforces_staging_gate(cli_registry: ArtifactRegistry, capsys: pytest.CaptureFixture[str]) -> None:
    run_id = _persist_linear_run(cli_registry)
    with pytest.raises(SystemExit) as exc_info:
        main(["promote-sales-model", "--run-id", run_id, "--env", "prod"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "staging" in captured.err.lower()


def test_promote_sales_model_force_bypasses_gate(cli_registry: ArtifactRegistry, capsys: pytest.CaptureFixture[str]) -> None:
    run_id = _persist_linear_run(cli_registry)
    main(["promote-sales-model", "--run-id", run_id, "--env", "prod", "--force"])
    captured = capsys.readouterr()
    assert "GOVERNANCE BYPASS" in captured.out
    assert "bypassed_staging_gate=true" in captured.out


def test_predict_sales_without_promoted_model_fails(cli_registry: ArtifactRegistry, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([
            "predict-sales",
            "--env",
            "dev",
            "--ship-mode",
            "Second Class",
            "--segment",
            "Consumer",
            "--region",
            "West",
            "--market",
            "US",
            "--product-id",
            "TEC-AC-10003033",
            "--sub-category",
            "Accessories",
            "--category",
            "Technology",
            "--quantity",
            "3",
            "--discount",
            "0.0",
            "--order-date",
            "2026-08-20",
        ])
    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "No active model" in captured.err


class _FakePredictLlmClient:
    """Fake LLM client swapped in for `src.ai_engineering.llm_client.LlmClient`."""

    _RESPONSE = (
        '{"status": "ok", "prediction_input": {'
        '"order_date": "2026-08-20", "ship_mode": "Second Class", '
        '"segment": "Consumer", "region": "West", "market": "US", '
        '"product_id": "TEC-AC-10003033", "sub_category": "Accessories", '
        '"category": "Technology", "quantity": 3, "discount": 0.0}, '
        '"missing_fields": [], "clarification": ""}'
    )

    def __init__(self, config: object) -> None:
        del config

    def complete(self, prompt: str) -> str:
        del prompt
        return self._RESPONSE


def test_predict_sales_nl_without_promoted_model_fails(
    cli_registry: ArtifactRegistry,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORGE_API_KEY", "test-key")
    monkeypatch.setattr("src.ai_engineering.llm_client.LlmClient", _FakePredictLlmClient)
    with pytest.raises(SystemExit) as exc_info:
        main(["predict-sales-nl", "predict sales for a West region order", "--env", "dev"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "No active model" in captured.err


def test_predict_sales_nl_parses_and_predicts_with_promoted_model(
    cli_registry: ArtifactRegistry,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORGE_API_KEY", "test-key")
    monkeypatch.setattr("src.ai_engineering.llm_client.LlmClient", _FakePredictLlmClient)
    run_id = _persist_linear_run(cli_registry)
    cli_registry.promote(run_id, "dev")

    main([
        "predict-sales-nl",
        "Predict the sales for a Second Class Consumer order in the West "
        "region, market US, product TEC-AC-10003033, sub-category "
        "Accessories, category Technology, quantity 3, no discount, ordered "
        "2026-08-20.",
        "--env",
        "dev",
    ])
    captured = capsys.readouterr()
    assert "Predicted Sales" in captured.out
    assert "Parsed prediction input" in captured.out
