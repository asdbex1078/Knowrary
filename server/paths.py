"""路径解析与 core 注入。

vault 由 `vaults` 模块选定（环境变量 `KNOWRARY_VAULT` > 用户级配置 `~/.knowrary/config.json`）。
**这个仓库自己不再是 vault**：2026-09-22 起知识库是另一个目录，由人在设置页挑。

阶段 1 的 `tools/knowrary/core` 是零第三方依赖的普通包，这里把它的父目录塞进
sys.path 后按 `core` 导入——服务层与 CLI 共用同一套解析，不复制第二份实现。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE_DIR = REPO / "tools" / "knowrary"
WEB_DIST = REPO / "web" / "dist"

if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

import core  # noqa: E402  （必须先注入 sys.path）


def vault_path() -> Path:
    """当前 vault。每次调用都现算（环境变量 > 用户级配置），方便测试里切换。

    选库的规则全在 `vaults` 里，这里只做转发——`vaults` 反过来要用本模块的 `REPO` 与
    `core`，所以这个 import 必须留在函数体内，放到模块头会成环。
    """
    from . import vaults
    return vaults.current_vault()


DEFAULT_LAYOUT = "layout"


def layout_path(vault: Path | None = None, name: str = DEFAULT_LAYOUT) -> Path:
    """全局图仍然是 `.knowrary/layout.json`；项目画布各自一份 `.knowrary/layouts/<项目>.json`。

    **契约是同一个 `LayoutDoc`**，只是文件不同——项目画布是工作台，全局图才是成品图，
    但它们的形状没有理由不一样（重构方案 §5A）。
    """
    root = (vault or vault_path()) / ".knowrary"
    return root / "layout.json" if name == DEFAULT_LAYOUT else root / "layouts" / f"{name}.json"


__all__ = ["DEFAULT_LAYOUT", "REPO", "WEB_DIST", "core", "layout_path", "vault_path"]
