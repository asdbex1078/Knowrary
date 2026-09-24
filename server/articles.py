"""原文阅读（原文层第 4 步）：列出原文目录里的原文、读一篇，附上从它拆出了哪些点。

**只读**。原文是快照（写完不和节点同步），要改就去 Obsidian——应用里没有第二个编辑入口。
「这篇拆出了哪些点」是从节点的 `sources` 反查出来的（`core.source_backlink_refs`），原文里不回写。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .contracts import ArticleEntry, ArticleHeading, ArticleNodeRef, ArticleRead, ArticlesRead
from .index_service import current_index
from .paths import core

MAX_BYTES = 2_000_000


class ArticleRejected(Exception):
    """读不了：不在原文目录里、不是 md、不存在。"""


def _meta(text: str) -> tuple[dict, str]:
    try:
        return core.split_frontmatter(text)
    except ValueError:
        return {}, text


def _title(fm: dict, body: str, path: str) -> str:
    """标题：frontmatter 的 title → 正文第一个一级标题 → 文件名。"""
    if str(fm.get("title") or "").strip():
        return str(fm["title"]).strip()
    head = next((h for h in core.outline(body) if h.level == 1), None)
    return head.title if head else Path(path).stem


def list_articles(vault: Path) -> ArticlesRead:
    """原文目录里的全部原文，新的在前。`nodes` = 从它拆出了几个点；0 就是「还没拆」。"""
    root = core.load_vault_config(vault)["articles_dir"]
    refs = core.source_backlink_refs(vault, current_index(vault))
    out: list[ArticleEntry] = []
    for p in core.list_articles(vault / root):
        rel = p.relative_to(vault).as_posix()
        fm, body = _meta(core.read(p))
        st = p.stat()
        out.append(ArticleEntry(
            path=rel, title=_title(fm, body, rel), nodes=len({r["id"] for r in refs.get(rel, [])}),
            imported=str(fm.get("imported") or ""), origin=str(fm.get("origin") or ""), size=st.st_size,
            modified=dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")))
    out.sort(key=lambda a: a.modified, reverse=True)
    return ArticlesRead(dir=root, articles=out)


def read_article(vault: Path, rel: str) -> ArticleRead:
    """读一篇。能读的只有两种：原文目录里的，和某个节点的 sources 真链着的（旧年代散在别处的那几篇）。
    路径越界、不是 md 的一律拒——一个 `..` 都不放过。"""
    clean = (rel or "").strip().lstrip("/")
    path = vault / clean
    try:
        path.resolve().relative_to(vault.resolve())
    except (ValueError, OSError) as exc:
        raise ArticleRejected(f"路径不在库里：{rel}") from exc
    if not clean.endswith(".md") or not path.is_file():
        raise ArticleRejected(f"没有这篇原文：{rel}")
    root = core.load_vault_config(vault)["articles_dir"]
    refs = core.source_backlink_refs(vault, current_index(vault))
    if not clean.startswith(root + "/") and clean not in refs:
        raise ArticleRejected(f"`{clean}` 不在原文目录 `{root}/` 里，也没有节点链着它")
    if path.stat().st_size > MAX_BYTES:
        raise ArticleRejected(f"原文太大（{path.stat().st_size} 字节）")
    fm, body = _meta(core.read(path))
    return ArticleRead(
        path=clean, title=_title(fm, body, clean), text=body,
        imported=str(fm.get("imported") or ""), origin=str(fm.get("origin") or ""),
        in_dir=clean.startswith(root + "/"),
        headings=[ArticleHeading(level=h.level, title=h.title) for h in core.outline(body)],
        nodes=[ArticleNodeRef(**r) for r in refs.get(clean, [])])
