from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

TTL_MS = int(os.environ.get("TOKEN_TTL_MS", str(2 * 60 * 60 * 1000)))
MAX_ITEMS = int(os.environ.get("TOKEN_STORE_MAX", "2000"))

_LOCK = threading.Lock()
_STORE: dict[str, MediaToken] = {}
_LOADED = False


def store_path() -> Path:
    override = os.environ.get("TOKEN_STORE_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "data" / "tokens.json"


@dataclass
class MediaToken:
    token: str
    source_urls: list[str]
    kind: str
    filename: str
    expires_at: int
    # 短时效签名平台（如腾讯频道）用：记录"这条媒体是从哪个分享链接的哪一项来的"，
    # 全部候选都失效时据此重新解析换新地址。None 表示不需要刷新。
    refresh: dict | None = None

    @property
    def source_url(self) -> str:
        return self.source_urls[0]


def _now_ms() -> int:
    return int(time.time() * 1000)


def reset() -> None:
    global _LOADED
    with _LOCK:
        _STORE.clear()
        _LOADED = False


def _sweep_unlocked() -> int:
    now = _now_ms()
    expired = [key for key, item in _STORE.items() if item.expires_at < now]
    for key in expired:
        _STORE.pop(key, None)
    removed = len(expired)
    overflow = len(_STORE) - MAX_ITEMS
    if overflow > 0:
        oldest = sorted(_STORE.values(), key=lambda item: item.expires_at)[:overflow]
        for item in oldest:
            _STORE.pop(item.token, None)
        removed += overflow
    return removed


def _persist_unlocked() -> None:
    path = store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tokens": [
                {
                    "token": item.token,
                    "source_urls": item.source_urls,
                    "kind": item.kind,
                    "filename": item.filename,
                    "expires_at": item.expires_at,
                    "refresh": item.refresh,
                }
                for item in _STORE.values()
            ]
        }
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except OSError:
        return


def _load_unlocked() -> None:
    path = store_path()
    if not path.is_file():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return
    rows = raw.get("tokens") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return
    now = _now_ms()
    for row in rows:
        if not isinstance(row, dict):
            continue
        token = row.get("token")
        urls = row.get("source_urls")
        kind = row.get("kind")
        filename = row.get("filename")
        expires_at = row.get("expires_at")
        refresh = row.get("refresh")
        if not isinstance(token, str) or not token:
            continue
        if not isinstance(urls, list) or not isinstance(kind, str) or not isinstance(filename, str):
            continue
        if not isinstance(expires_at, int) or expires_at < now:
            continue
        clean_urls = [u for u in urls if isinstance(u, str) and u]
        if not clean_urls:
            continue
        _STORE[token] = MediaToken(
            token=token,
            source_urls=list(dict.fromkeys(clean_urls)),
            kind=kind,
            filename=filename,
            expires_at=expires_at,
            refresh=refresh if isinstance(refresh, dict) else None,
        )


def _ensure_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    with _LOCK:
        if _LOADED:
            return
        _load_unlocked()
        if _sweep_unlocked():
            _persist_unlocked()
        _LOADED = True


def issue_multi(
    source_urls: list[str], kind: str, filename: str, refresh: dict | None = None
) -> str:
    urls = [u for u in (source_urls or []) if isinstance(u, str) and u]
    if not urls:
        raise ValueError("issue_multi requires at least one url")
    _ensure_loaded()
    with _LOCK:
        _sweep_unlocked()
        token = secrets.token_hex(16)
        _STORE[token] = MediaToken(
            token=token,
            source_urls=list(dict.fromkeys(urls)),
            kind=kind,
            filename=filename,
            expires_at=_now_ms() + TTL_MS,
            refresh=refresh if isinstance(refresh, dict) else None,
        )
        _persist_unlocked()
        return token


def issue(source_url: str, kind: str, filename: str, refresh: dict | None = None) -> str:
    return issue_multi([source_url], kind, filename, refresh=refresh)


def update_sources(token: str, source_urls: list[str]) -> bool:
    """重新解析拿到新地址后，原地替换该 token 的候选。

    token 本身不变，所以前端已经拿到的 /api/media/<token> 链接无需重发即可恢复播放。
    """
    urls = [u for u in (source_urls or []) if isinstance(u, str) and u]
    if not urls:
        return False
    _ensure_loaded()
    with _LOCK:
        item = _STORE.get(token)
        if item is None:
            return False
        item.source_urls = list(dict.fromkeys(urls))
        item.expires_at = _now_ms() + TTL_MS
        _persist_unlocked()
        return True


def get(token: str) -> MediaToken | None:
    _ensure_loaded()
    with _LOCK:
        item = _STORE.get(token)
        if item is None:
            return None
        now = _now_ms()
        if item.expires_at < now:
            _STORE.pop(token, None)
            _persist_unlocked()
            return None
        if item.expires_at - now < TTL_MS // 2:
            item.expires_at = now + TTL_MS
            _persist_unlocked()
        return item


def size() -> int:
    _ensure_loaded()
    with _LOCK:
        if _sweep_unlocked():
            _persist_unlocked()
        return len(_STORE)
