"""Integration test for end-to-end MLOps training against Dockerized Postgres."""

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
    reason='Docker daemon not running — MLOps training integration test requires PostgreSQL.',
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


def test_train_sales_model_persists_two_runs_and_fails_fast_when_pg_down() -> None:
    if _MLOPS_ROOT.exists():
        shutil.rmtree(_MLOPS_ROOT)
    _run_cli('bootstrap')
    train = _run_cli('train-sales-model')
    assert 'linear_regression' in train.stdout
    assert 'catboost' in train.stdout
    registry = json.loads((_MLOPS_ROOT / 'registry.json').read_text(encoding='utf-8'))
    assert len(registry['runs']) == 2
    for entry in registry['runs']:
        artifact_dir = _MLOPS_ROOT / 'models' / entry['model_name'] / entry['run_id']
        assert artifact_dir.exists()

    before_failure = (_MLOPS_ROOT / 'registry.json').read_text(encoding='utf-8')
    _run_cli('teardown')
    failed = _run_cli('train-sales-model', expect_success=False)
    assert failed.returncode != 0
    assert (_MLOPS_ROOT / 'registry.json').read_text(encoding='utf-8') == before_failure
    _run_cli('bootstrap')
