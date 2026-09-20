import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.parsers.kuaishou import ParsedWork, VideoVariant, _group_variants

client = TestClient(app)


def test_group_variants_hd_and_sd_with_cdn_fallback():
    urls = [
        "https://v23-3.kwaicdn.com/bs2/x_7582_hd15.mp4?pkey=1",
        "https://v4.oskwai.com/bs2/x_7582_hd15.mp4?pkey=2",
        "https://tymov2.a.kwimgs.com/upic/aa_b_Bbf.mp4?tt=b&bp=10000",
        "https://v23-3.kwaicdn.com/upic/aa_b_Bbf.mp4?pkey=3",
    ]
    groups = _group_variants(urls)
    assert len(groups) == 2
    assert groups[0].label == "高清" and groups[0].rank == 0 and len(groups[0].urls) == 2
    assert groups[1].label == "标清" and groups[1].rank == 2 and len(groups[1].urls) == 2
    assert groups[0].urls[0].startswith("https://v23-3.kwaicdn.com")


def test_group_variants_unknown_only():
    groups = _group_variants(["https://cdn.example/plain/video.mp4"])
    assert len(groups) == 1
    assert groups[0].label == "默认"
    assert groups[0].urls == ["https://cdn.example/plain/video.mp4"]


def test_parse_api_returns_variants(monkeypatch):
    import app.main as m

    async def fake_parse(url, timeout=15.0, client=None):
        return ParsedWork(
            type="video",
            title="t",
            author="a",
            video_url="https://cdn.example/hd.mp4",
            variants=[
                VideoVariant(label="高清", rank=0, urls=["https://cdn.example/hd.mp4"]),
                VideoVariant(label="标清", rank=2, urls=["https://cdn.example/sd.mp4"]),
            ],
        )

    monkeypatch.setattr(m, "parse_kuaishou", fake_parse)
    r = client.post("/api/parse", json={"input": "https://v.kuaishou.com/vtest1"})
    body = r.json()
    assert body["ok"] is True
    assert len(body["variants"]) == 2
    assert body["videoUrl"] == body["variants"][0]["mediaUrl"]
    assert body["downloadUrl"] == body["variants"][0]["downloadUrl"]
    assert body["variants"][1]["label"] == "标清"
