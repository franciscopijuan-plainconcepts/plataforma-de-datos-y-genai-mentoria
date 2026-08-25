"""Integration test for end-to-end MLOps inference."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import pandas as pd


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MLOPS_ROOT = _REPO_ROOT / '.artifacts' / 'mlops'
_LOG_PATH = _MLOPS_ROOT / 'predict_sales.log'
_SOURCE = _REPO_ROOT / 'Global Superstore Data.xlsx'


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ['docker', 'info'], capture_output=True, text=True, check=False, timeout=10
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(
    not _docker_available(),
    reason='Docker daemon not running — inference integration test requires Dockerized PostgreSQL.',
)


def _run_cli(*args: str, expect_success: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ['uv', 'run', 'python', '-m', 'src.cli.main', *args],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=1200,
    )
    if expect_success and result.returncode != 0:
        pytest.fail(f"Command {' '.join(args)} failed:\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def _sample_prediction_args() -> list[str]:
    frame = pd.read_excel(_SOURCE, sheet_name='Orders').iloc[0]
    return [
        '--ship-mode', str(frame['Ship Mode']),
        '--segment', str(frame['Segment']),
        '--region', str(frame['Region']),
        '--market', str(frame['Market']),
        '--product-id', str(frame['Product ID']),
        '--sub-category', str(frame['Sub-Category']),
        '--category', str(frame['Category']),
        '--quantity', str(int(frame['Quantity'])),
        '--discount', str(frame['Discount']),
        '--order-date', frame['Order Date'].date().isoformat(),
    ]


def test_predict_sales_uses_promoted_model_and_handles_unseen_categories() -> None:
    if _MLOPS_ROOT.exists():
        shutil.rmtree(_MLOPS_ROOT)
    _run_cli('bootstrap')
    _run_cli('train-sales-model')
    registry = json.loads((_MLOPS_ROOT / 'registry.json').read_text(encoding='utf-8'))
    run_id = next(entry['run_id'] for entry in registry['runs'] if entry['model_name'] == 'catboost')
    _run_cli('promote-sales-model', '--run-id', run_id, '--env', 'dev')

    prediction = _run_cli('predict-sales', '--env', 'dev', *_sample_prediction_args())
    assert 'Predicted Sales:' in prediction.stdout
    assert 'used_fallback_encoding: false' in prediction.stdout
    assert _LOG_PATH.exists()
    last_line = _LOG_PATH.read_text(encoding='utf-8').strip().splitlines()[-1]
    payload = json.loads(last_line)
    assert payload['prediction_result']['environment'] == 'dev'
    assert payload['prediction_result']['latency_ms'] < 2000

    unseen = _run_cli(
        'predict-sales', '--env', 'dev',
        '--ship-mode', 'Second Class', '--segment', 'Consumer',
        '--region', 'West', '--market', 'US', '--product-id', 'NEW-SKU-999',
        '--sub-category', 'Accessories',
        '--category', 'Technology', '--quantity', '1', '--discount', '0.0', '--order-date', '2026-08-20'
    )
    assert unseen.returncode == 0
    assert 'used_fallback_encoding: true' in unseen.stdout
