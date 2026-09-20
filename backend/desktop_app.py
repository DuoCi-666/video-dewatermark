"""桌面版启动入口：单文件 exe 双击即用。

- 数据（统计/失败样本/token/打包/HLS 缓存）落到 exe 同级的 data 目录；
- 内置 ffmpeg.exe（HLS 转封装用）；
- 前端 dist 由 FastAPI 直接托管，不需要 nginx / Vite；
- 启动后自动打开浏览器 http://127.0.0.1:5173 。
"""
from __future__ import annotations

import os
import sys
import threading
import time
import urllib.request
from pathlib import Path


def _app_dir() -> Path:
    """exe 所在目录（onefile）或源码目录（开发）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resource(rel: str) -> Path:
    """打包进 exe 的资源（onefile 解压到 _MEIPASS）；开发时回源码目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / rel  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent / rel


def _configure_env() -> None:
    base = _app_dir()
    data_dir = base / "video-dewatermark-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "packs").mkdir(exist_ok=True)
    (data_dir / "hls").mkdir(exist_ok=True)

    os.environ.setdefault("STATS_STORE_PATH", str(data_dir / "stats.json"))
    os.environ.setdefault("FAILURES_STORE_PATH", str(data_dir / "failures.jsonl"))
    os.environ.setdefault("TOKEN_STORE_PATH", str(data_dir / "tokens.json"))
    os.environ.setdefault("PACK_STORE_PATH", str(data_dir / "packs"))
    os.environ.setdefault("HLS_CACHE_PATH", str(data_dir / "hls"))
    # 本机单用户使用，不限制频率
    os.environ.setdefault("GUARD_ENABLED", "0")

    ffmpeg = _resource("ffmpeg.exe")
    if ffmpeg.exists():
        os.environ["FFMPEG_BIN"] = str(ffmpeg)


def main() -> None:
    _configure_env()

    import uvicorn
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    from app.main import app

    dist = _resource(Path("frontend") / "dist")
    if dist.exists():
        assets = dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/", include_in_schema=False)
        async def _index() -> FileResponse:
            return FileResponse(dist / "index.html")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def _spa(full_path: str) -> FileResponse:
            # 已存在的静态文件直接返回；其余回退到 index.html（SPA）
            candidate = (dist / full_path).resolve()
            if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    port = 5173
    url = f"http://127.0.0.1:{port}"

    def _serve() -> None:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")

    threading.Thread(target=_serve, daemon=True).start()

    # 等后端起来再开窗口，避免窗口先打开看到空白/错误页
    for _ in range(50):
        try:
            urllib.request.urlopen(url, timeout=0.5)
            break
        except Exception:
            time.sleep(0.2)

    import webview

    webview.create_window(
        "视频去水印工具箱",
        url,
        width=1200,
        height=820,
        min_size=(960, 640),
        resizable=True,
    )
    webview.start()


if __name__ == "__main__":
    main()
