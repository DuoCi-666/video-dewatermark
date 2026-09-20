"""成功样本池（app/samples.py）测试。

这是「把用户成功解析的链接沉淀为巡检候选」的机制，重点关注：
- 原样保存：链接不做任何参数剥离，保证样本可复现解析
- 去重与计数：同链接反复命中只累加，不重复占位
- 三重封顶：条数 / 天数 / 文件大小
- 开关：SAMPLES_ENABLE=0 时完全不落任何东西
- 导出：能产出可直接给巡检用的清单格式
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from app.samples import SampleStore


# ------------------------------------------------------------------ 原样保存
def test_record_keeps_url_verbatim(store):
    """带分享凭证 / 追踪参数的长链必须原样保留 —— 剥离会破坏可复现性。"""
    url = (
        "https://www.xiaohongshu.com/discovery/item/abc"
        "?xsec_token=TOKEN123&share_id=SHARE&type=video&app_platform=android"
    )
    store.record(platform="xiaohongshu", url=url)
    stored = store.items()[0]["url"]
    assert stored == url
    for kept in ("xsec_token", "share_id", "type=video", "app_platform"):
        assert kept in stored


def test_record_keeps_doubao_share_id(store):
    """回归：doubao 视频链的 share_id 是解析必需参数，不得被剥离。"""
    url = (
        "https://www.doubao.com/video-sharing"
        "?share_id=SHARE&video_id=v0369cg10004&source_type=mobile"
    )
    store.record(platform="doubao", url=url, kind="video")
    stored = store.items()[0]["url"]
    assert "share_id=SHARE" in stored
    assert "video_id=v0369cg10004" in stored


def test_record_keeps_clean_short_link(store):
    """用户粘贴的短链本来就没参数，原样保留。"""
    url = "https://v.douyin.com/C9rISolhaGE/"
    store.record(platform="douyin", url=url)
    assert store.items()[0]["url"] == url


def test_record_trims_surrounding_whitespace(store):
    store.record(platform="douyin", url="  https://v.douyin.com/abc/  \n")
    assert store.items()[0]["url"] == "https://v.douyin.com/abc/"


# ------------------------------------------------------------------ 记录与去重
@pytest.fixture()
def store(tmp_path):
    return SampleStore(
        tmp_path / "samples.jsonl",
        enabled=True,
        max_records=100,
        retention_days=30,
        max_bytes=1024 * 1024,
    )


def test_record_stores_platform_and_kind(store):
    assert store.record(platform="kuaishou", url="https://v.douyin.com/abc/", kind="video")
    items = store.items()
    assert len(items) == 1
    assert items[0]["url"] == "https://v.douyin.com/abc/"
    assert items[0]["platform"] == "kuaishou"
    assert items[0]["kind"] == "video"
    assert items[0]["count"] == 1


def test_record_same_link_twice_counts_once(store):
    url = "https://v.douyin.com/abc/"
    store.record(platform="douyin", url=url)
    store.record(platform="douyin", url=url)
    assert store.size() == 1
    assert store.items()[0]["count"] == 2


def test_record_rejects_non_http(store):
    assert not store.record(platform="p", url="not-a-url")
    assert store.size() == 0


def test_record_tracks_sources(store):
    url = "https://v.douyin.com/abc/"
    store.record(platform="douyin", url=url, source="web")
    store.record(platform="douyin", url=url, source="batch")
    assert store.items()[0]["sources"] == {"web": 1, "batch": 1}


# ------------------------------------------------------------------ 开关
def test_disabled_store_records_nothing(tmp_path):
    store = SampleStore(tmp_path / "s.jsonl", enabled=False)
    assert store.record(platform="p", url="https://x.com/a") is False
    assert store.size() == 0
    store.flush()
    assert not (tmp_path / "s.jsonl").exists(), "关闭时不该落任何文件"


# ------------------------------------------------------------------ 落盘与容量
def test_flush_and_reload_roundtrip(tmp_path):
    path = tmp_path / "s.jsonl"
    store = SampleStore(path, enabled=True)
    store.record(platform="douyin", url="https://v.douyin.com/abc/", kind="video")
    store.flush()

    reloaded = SampleStore(path, enabled=True)
    assert reloaded.size() == 1
    assert reloaded.items()[0]["url"] == "https://v.douyin.com/abc/"
    assert reloaded.items()[0]["count"] == 1


def test_flush_is_noop_without_changes(tmp_path):
    path = tmp_path / "s.jsonl"
    store = SampleStore(path, enabled=True)
    store.flush()
    assert not path.exists()
    store.record(platform="p", url="https://x.com/a")
    store.flush()
    assert path.exists()


def test_trim_by_max_records(tmp_path):
    store = SampleStore(tmp_path / "s.jsonl", enabled=True, max_records=3)
    for i in range(10):
        store.record(platform="p", url=f"https://x.com/{i}")
    assert store.size() == 3


def test_trim_by_retention_days(tmp_path):
    store = SampleStore(tmp_path / "s.jsonl", enabled=True, retention_days=7)
    old = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    store._items[("p", "https://x.com/old")] = {
        "platform": "p",
        "url": "https://x.com/old",
        "lastSeen": old,
        "count": 1,
    }
    store.record(platform="p", url="https://x.com/new")
    assert store.size() == 1
    assert store.items()[0]["url"] == "https://x.com/new"


def test_flush_respects_max_bytes(tmp_path):
    path = tmp_path / "s.jsonl"
    store = SampleStore(path, enabled=True, max_bytes=400)
    for i in range(50):
        store.record(platform="p", url=f"https://example.com/very/long/path/{i}")
    store.flush()
    assert path.stat().st_size <= 400


def test_load_ignores_corrupt_lines(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(
        json.dumps({"platform": "p", "url": "https://x.com/ok", "lastSeen": "2024-01-01T00:00:00"})
        + "\n{broken\n\n",
        "utf-8",
    )
    store = SampleStore(path, enabled=True)
    assert store.size() == 1


# ------------------------------------------------------------------ 查询与导出
def test_candidates_grouped_by_platform(tmp_path):
    store = SampleStore(tmp_path / "s.jsonl", enabled=True)
    for i in range(5):
        store.record(platform="douyin", url=f"https://v.douyin.com/{i}")
    store.record(platform="kuaishou", url="https://v.kuaishou.com/a")
    cands = store.candidates(per_platform=2)
    assert len(cands["douyin"]) == 2
    assert len(cands["kuaishou"]) == 1


def test_candidates_respect_min_count(tmp_path):
    store = SampleStore(tmp_path / "s.jsonl", enabled=True)
    store.record(platform="p", url="https://x.com/once")
    store.record(platform="p", url="https://x.com/twice")
    store.record(platform="p", url="https://x.com/twice")
    cands = store.candidates(per_platform=5, min_count=2)
    urls = [c["url"] for c in cands["p"]]
    assert urls == ["https://x.com/twice"]


def test_export_links_produces_巡检_schema(tmp_path):
    """导出的条目要能直接塞进巡检清单（platform / expect / url 三件套）。"""
    store = SampleStore(tmp_path / "s.jsonl", enabled=True)
    store.record(platform="douyin", url="https://v.douyin.com/abc/", kind="video")
    result = store.export_links(per_platform=2)
    assert result["count"] == 1
    entry = next(iter(result["links"].values()))
    assert entry["platform"] == "douyin"
    assert entry["expect"] == "video"
    assert entry["url"] == "https://v.douyin.com/abc/"


def test_export_links_text_is_valid_json(tmp_path):
    store = SampleStore(tmp_path / "s.jsonl", enabled=True)
    store.record(platform="douyin", url="https://v.douyin.com/abc/", kind="video")
    out = store.export_links(fmt="text")
    parsed = json.loads(out["text"])  # 必须是合法 JSON，用户可直接落盘
    assert "douyin-auto1" in parsed


def test_report_shape(tmp_path):
    store = SampleStore(tmp_path / "s.jsonl", enabled=True)
    store.record(platform="douyin", url="https://v.douyin.com/a")
    store.record(platform="douyin", url="https://v.douyin.com/b")
    store.record(platform="kuaishou", url="https://v.kuaishou.com/a")
    report = store.report()
    assert report["enabled"] is True
    assert report["total"] == 3
    assert report["byPlatform"] == {"douyin": 2, "kuaishou": 1}


# ------------------------------------------------------------------ CLI
def test_cli_prints_json_to_stdout(tmp_path, monkeypatch, capsys):
    import os

    store_path = tmp_path / "s.jsonl"
    monkeypatch.setenv("SAMPLES_STORE_PATH", str(store_path))
    store = SampleStore(store_path, enabled=True)
    store.record(platform="douyin", url="https://v.douyin.com/abc/", kind="video")
    store.flush()

    import app.samples as sm

    monkeypatch.setattr(sm, "SAMPLES", SampleStore(store_path, enabled=True))
    assert sm._cli([]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["douyin-auto1"]["url"] == "https://v.douyin.com/abc/"


def test_cli_writes_output_file_and_creates_parent(tmp_path, monkeypatch, capsys):
    store_path = tmp_path / "s.jsonl"
    store = SampleStore(store_path, enabled=True)
    store.record(platform="douyin", url="https://v.douyin.com/abc/", kind="video")
    store.flush()

    import app.samples as sm

    monkeypatch.setattr(sm, "SAMPLES", SampleStore(store_path, enabled=True))
    target = tmp_path / "nested" / "deeper" / "extra.json"
    assert sm._cli(["-o", str(target)]) == 0
    written = json.loads(target.read_text("utf-8"))
    assert written["douyin-auto1"]["platform"] == "douyin"


def test_cli_min_count_filters(tmp_path, monkeypatch, capsys):
    store_path = tmp_path / "s.jsonl"
    store = SampleStore(store_path, enabled=True)
    store.record(platform="p", url="https://x.com/once")
    store.record(platform="p", url="https://x.com/twice")
    store.record(platform="p", url="https://x.com/twice")
    store.flush()

    import app.samples as sm

    monkeypatch.setattr(sm, "SAMPLES", SampleStore(store_path, enabled=True))
    sm._cli(["--min-count", "2"])
    out = json.loads(capsys.readouterr().out)
    urls = [v["url"] for v in out.values()]
    assert urls == ["https://x.com/twice"]
