"""vault 里的图片（assets/）：列出、读取、上传。

图片是画布上的装饰元素——位置记在 layout.images，文件本身放在 vault 的 assets/ 下，
跟 md 一样属于用户的东西，所以只认这一个目录，路径一律解析后核对是否还在目录内。
上传走原始 body 而不是 multipart：为一个"贴张图"的功能引入 python-multipart 不值。
"""
from __future__ import annotations

import re
from pathlib import Path

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif"}
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
        ".webp": "image/webp", ".svg": "image/svg+xml", ".avif": "image/avif"}
MAX_BYTES = 20 * 1024 * 1024
RE_SAFE_NAME = re.compile(r"^[\w一-鿿][\w一-鿿 .\-()]{0,120}$")


class AssetRejected(Exception):
    """文件名不合法、类型不支持、太大，或者路径跑到 assets/ 外面去了。"""


def assets_dir(vault: Path) -> Path:
    return vault / "assets"


def resolve(vault: Path, name: str) -> Path:
    """把文件名解析成 assets/ 下的真实路径，越界一律拒绝。"""
    root = assets_dir(vault).resolve()
    if "/" in name or "\\" in name or not RE_SAFE_NAME.match(name):
        raise AssetRejected(f"文件名 `{name}` 不合法（只收 assets/ 下的单层文件名）")
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise AssetRejected(f"`{name}` 不在 assets/ 里")
    if path.suffix.lower() not in IMAGE_EXT:
        raise AssetRejected(f"只支持图片：{'、'.join(sorted(IMAGE_EXT))}")
    return path


def listing(vault: Path) -> list[dict]:
    root = assets_dir(vault)
    if not root.is_dir():
        return []
    out = [{"file": p.name, "size": p.stat().st_size, "url": f"/api/asset/{p.name}"}
           for p in sorted(root.iterdir())
           if p.is_file() and p.suffix.lower() in IMAGE_EXT]
    return out


def save(vault: Path, name: str, data: bytes, *, overwrite: bool = False) -> dict:
    path = resolve(vault, name)
    if not data:
        raise AssetRejected("空文件")
    if len(data) > MAX_BYTES:
        raise AssetRejected(f"图片超过 {MAX_BYTES // 1024 // 1024}MB")
    if path.exists() and not overwrite:
        raise AssetRejected(f"assets/{name} 已存在（换个名字，或加 ?overwrite=true 覆盖）")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"file": path.name, "size": len(data), "url": f"/api/asset/{path.name}"}
