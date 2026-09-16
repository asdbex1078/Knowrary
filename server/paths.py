"""路径解析与 core 注入。

vault 默认是仓库根目录（本仓库自身就是 Obsidian vault），可用环境变量
`KNOWRARY_VAULT` 覆盖（自测就是靠它指到临时 vault）。

阶段 1 的 `tools/knowrary/core` 是零第三方依赖的普通包，这里把它的父目录塞进
sys.path 后按 `core` 导入——服务层与 CLI 共用同一套解析，不复制第二份实现。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE_DIR = REPO / "tools" / "knowrary"
WEB_DIST = REPO / "web" / "dist"
WEB3D_DIST = REPO / "web3d" / "dist"   # 3D 总览原型（只读，可整目录删除）

if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

import core  # noqa: E402  （必须先注入 sys.path）


def vault_path() -> Path:
    """当前 vault。每次调用都读环境变量，方便测试里切换。"""
    return Path(os.environ.get("KNOWRARY_VAULT", str(REPO))).resolve()


DEFAULT_LAYOUT = "layout"


def layout_path(vault: Path | None = None, name: str = DEFAULT_LAYOUT) -> Path:
    """全局图仍然是 `.knowrary/layout.json`；项目画布各自一份 `.knowrary/layouts/<项目>.json`。

    **契约是同一个 `LayoutDoc`**，只是文件不同——项目画布是工作台，全局图才是成品图，
    但它们的形状没有理由不一样（重构方案 §5A）。
    """
    root = (vault or vault_path()) / ".knowrary"
    return root / "layout.json" if name == DEFAULT_LAYOUT else root / "layouts" / f"{name}.json"


__all__ = ["DEFAULT_LAYOUT", "REPO", "WEB3D_DIST", "WEB_DIST", "core", "layout_path", "vault_path"]
