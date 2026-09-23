"""节点的来源（frontmatter `sources`）：这个点是从哪来的。

**一个列表，两种写法混放**（2026-09-23）：

    sources:
      - "[[articles/BPE全景]]"          # 库里的原文：完整路径的 wikilink，点得开
      - 图灵《计算机器与智能》            # 外部来源：一张图、一篇论文、一本书——纯文字

为什么是完整路径而不是 `[[BPE全景]]`：文章和节点常常同名（都叫「Transformer」），
Obsidian 按名字找会跳错地方。代价是换原文目录时链接要跟着改——`vault_config` 搬家时顺带改。

**旧的 `source: xxx.md`（单个字符串）照读**，当成只有一项的 sources。那时只存文件名，
所以只写了文件名的那种要去原文目录里找一次（找不到再看 vault 还是代码仓库那会儿的
`harness/ llm/ doc/`，第 5 步补旧账之后就用不上了）。

索引里只存规范化之后的**原始字符串**：建索引很频繁，不该每次都去碰文件系统；
"这一项是不是原文、文件在不在"在要用的时候（节点详情、反查）再解析。
"""
from __future__ import annotations

import re
from pathlib import Path

from .vault_config import load_vault_config

# [[路径]]、[[路径#小节]]、[[路径|显示名]] 都认；小节和显示名不参与定位
RE_SOURCE_LINK = re.compile(r"^\[\[([^\]|#]+)(#[^\]|]*)?(?:\|([^\]]*))?\]\]$")
LEGACY_DIRS = ("harness", "llm", "doc")      # vault 还是代码仓库那会儿原文散在这几处


def sources_of(fm: dict) -> list[str]:
    """frontmatter 里的来源，规范成字符串列表：`sources`（列表或单个字符串）在前，旧的 `source` 在后，去重。"""
    out: list[str] = []
    raw = fm.get("sources")
    items = raw if isinstance(raw, list) else [raw]
    legacy = fm.get("source")
    items = [*items, *(legacy if isinstance(legacy, list) else [legacy])]
    for item in items:
        text = str(item).strip() if item not in (None, "") else ""
        if text and text not in out:
            out.append(text)
    return out


def clean_sources(value) -> list[str]:
    """写回前把 `sources` 收成干净的字符串列表。单个字符串也收，空项丢掉。"""
    items = value if isinstance(value, list) else [value]
    out: list[str] = []
    for item in items:
        if isinstance(item, (dict, list)):
            raise ValueError("sources 的每一项只能是一个字符串：[[原文路径]] 或一段文字")
        text = str(item).strip() if item is not None else ""
        if text and text not in out:
            out.append(text)
    return out


def article_link(rel_path: str) -> str:
    """原文的库内路径 → 写进 sources 的那种 wikilink（去掉 .md，和 Obsidian 的写法一致）。"""
    rel = rel_path[:-3] if rel_path.endswith(".md") else rel_path
    return f"[[{rel}]]"


class Resolver:
    """把 sources 的每一项认成「库里的原文」或「外部来源」。同一个字符串只找一次。"""

    def __init__(self, vault: Path):
        self.vault = vault
        self.articles = load_vault_config(vault)["articles_dir"]
        self._memo: dict[str, dict] = {}

    def __call__(self, item: str) -> dict:
        if item not in self._memo:
            self._memo[item] = self._resolve(item)
        return self._memo[item]

    def _resolve(self, item: str) -> dict:
        m = RE_SOURCE_LINK.match(item)
        if m:
            rel = m.group(1).strip().strip("/")
            rel = rel if rel.endswith(".md") else f"{rel}.md"
            hit = self.vault / rel
            return {"raw": item, "kind": "article", "path": rel, "section": (m.group(2) or "")[1:],
                    "label": (m.group(3) or "").strip() or Path(rel).stem,
                    "exists": hit.is_file() and self._inside(hit)}
        if item.endswith(".md") and "/" not in item:
            found = self._find_by_name(item)
            if found:
                return {"raw": item, "kind": "article", "path": found, "section": "",
                        "label": Path(found).stem, "exists": True, "legacy": True}
        return {"raw": item, "kind": "external", "path": "", "section": "", "label": item, "exists": False}

    def _inside(self, path: Path) -> bool:
        return self.vault.resolve() in path.resolve().parents

    def _find_by_name(self, name: str) -> str:
        """旧写法只有文件名：先找原文目录，再找旧年代散放原文的那几处。重名取第一个。"""
        for root in (self.articles, *LEGACY_DIRS):
            folder = self.vault / root
            if folder.is_dir():
                hit = next(folder.rglob(name), None)
                if hit is not None and hit.is_file():
                    return hit.relative_to(self.vault).as_posix()
        return ""


def backlinks(vault: Path, index: dict) -> dict[str, list[str]]:
    """原文 → 从它拆出来的节点（按 id）。**派生的，不存第二份**：原文里不回写链接。"""
    resolve = Resolver(vault)
    out: dict[str, list[str]] = {}
    for node in index.get("nodes", []):
        if node.get("virtual"):
            continue
        for item in node.get("sources") or []:
            ref = resolve(item)
            if ref["kind"] == "article":
                out.setdefault(ref["path"], []).append(node["id"])
    return out

