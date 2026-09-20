import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _isolated_token_store(tmp_path, monkeypatch):
    from app import tokens

    monkeypatch.setenv("TOKEN_STORE_PATH", str(tmp_path / "tokens.json"))
    tokens.reset()
    yield
    tokens.reset()


@pytest.fixture(autouse=True)
def _guard_disabled_by_default():
    """限流默认关闭，避免无关用例互相干扰配额；test_guard.py 会按需自行开启。"""
    from app import guard

    previous = guard.GUARD.enabled
    guard.GUARD.configure(enabled=False)
    yield
    guard.GUARD.configure(enabled=previous)
    guard.GUARD.reset()


@pytest.fixture(autouse=True)
def _isolated_stats_store(tmp_path, monkeypatch):
    """统计落盘指到临时目录，并清空内存计数，避免用例之间互相污染。"""
    from app import stats

    monkeypatch.setenv("STATS_STORE_PATH", str(tmp_path / "stats.json"))
    stats.STATS.store_path = tmp_path / "stats.json"
    stats.STATS.reset()
    yield
    stats.STATS.reset()


@pytest.fixture(autouse=True)
def _isolated_failures_store(tmp_path, monkeypatch):
    """失败样本同样指到临时目录并清空，避免用例互相污染。"""
    from app import failures

    monkeypatch.setenv("FAILURES_STORE_PATH", str(tmp_path / "failures.jsonl"))
    failures.SAMPLES.store_path = tmp_path / "failures.jsonl"
    failures.SAMPLES.reset()
    yield
    failures.SAMPLES.reset()
