"""Integration test for reproducible MLOps runs."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MLOPS_ROOT = _REPO_ROOT / '.artifacts' / 'mlops'


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
    reason='Docker daemon not running — reproducibility test requires Dockerized PostgreSQL.',
)


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    # SEED_SALES_PREDICTIONS=false: this test asserts an EXACT registry run
    # count after `bootstrap` + `train-sales-model`. `bootstrap` best-effort
    # auto-trains+promotes a model (v3.1 seeding amendment) when no model is
    # active yet, which would add extra run(s) and break the exact-count
    # assertion below — disable it here since this test is about
    # `train-sales-model`'s own reproducibility, not the seeder.
    import os

    env = {**os.environ, "SEED_SALES_PREDICTIONS": "false"}
    result = subprocess.run(
        ['uv', 'run', 'python', '-m', 'src.cli.main', *args],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=1200,
        env=env,
    )
    if result.returncode != 0:
        pytest.fail(f"Command {' '.join(args)} failed:\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def test_retraining_with_same_data_produces_same_hash_and_metrics() -> None:
    if _MLOPS_ROOT.exists():
        shutil.rmtree(_MLOPS_ROOT)
    _run_cli('bootstrap')
    _run_cli('train-sales-model')
    _run_cli('train-sales-model')

    registry = json.loads((_MLOPS_ROOT / 'registry.json').read_text(encoding='utf-8'))
    catboost_runs = [entry for entry in registry['runs'] if entry['model_name'] == 'catboost']
    assert len(catboost_runs) == 2
    first_dir = _MLOPS_ROOT / 'models' / 'catboost' / catboost_runs[0]['run_id']
    second_dir = _MLOPS_ROOT / 'models' / 'catboost' / catboost_runs[1]['run_id']
    assert (first_dir / 'data_hash.txt').read_text(encoding='utf-8') == (second_dir / 'data_hash.txt').read_text(encoding='utf-8')
    assert json.loads((first_dir / 'metrics.json').read_text(encoding='utf-8')) == json.loads((second_dir / 'metrics.json').read_text(encoding='utf-8'))
