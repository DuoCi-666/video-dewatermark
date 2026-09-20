"""生产/云端部署入口：FastAPI 同时托管前端静态页与 API。

与 desktop_app.py 的区别：不开桌面窗口，绑定 0.0.0.0，端口读 PORT 环境变量。
Render / Railway / Fly 等平台通过这个文件启动。
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.main import app

PORT = int(os.environ.get("PORT", "10000"))
# Dockerfile 把前端 dist 放在这里
DIST = Path(os.environ.get("DIST_DIR", "/srv/dist"))

if DIST.exists():
    assets = DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/", include_in_schema=False)
    async def _index() -> FileResponse:
        return FileResponse(DIST / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa(full_path: str) -> FileResponse:
        candidate = (DIST / full_path).resolve()
        if full_path and candidate.is_file() and DIST.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
