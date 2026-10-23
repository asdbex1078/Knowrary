"""原文阅读（原文层第 4 步）：列出原文目录里的原文、读一篇，附上从它拆出了哪些点。
成品（2026-10-22）也从这儿端出去：`work_file` 只做校验，HTML 由路由层整文件回给浏览器。

**只读**。原文是快照（写完不和节点同步），要改就去 Obsidian——应用里没有第二个编辑入口。
「这篇拆出了哪些点」是从节点的 `sources` 反查出来的（`core.source_backlink_refs`），原文里不回写。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import re

from .contracts import (ArticleEntry, ArticleHeading, ArticleNodeRef, ArticleRead, ArticlesRead,
                        WorkEntry, WorkNodeRef, WorksRead)
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


RE_HTML_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
RE_HTML_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
RE_TAG = re.compile(r"<[^>]+>")


def _work_title(path: Path) -> str:
    """成品标题：<title> → 第一个 <h1>（剥掉里面的标签）→ 文件名。只扫前 64KB，列表别被大文件拖慢。"""
    try:
        head = path.read_text("utf-8", errors="ignore")[:65536]
    except OSError:
        return path.stem
    for pat in (RE_HTML_TITLE, RE_HTML_H1):
        m = pat.search(head)
        if m:
            text = " ".join(RE_TAG.sub(" ", m.group(1)).split())
            if text:
                return text
    return path.stem


def list_works(vault: Path) -> WorksRead:
    """成品目录里的全部成品，新的在前。`refs` = 从它拆出来的点；空就是「待导入」。
    成品由人自己整理进来，这里只负责照单列出。"""
    root = core.load_vault_config(vault)["works_dir"]
    refs = core.source_backlink_refs(vault, current_index(vault))
    out: list[WorkEntry] = []
    for p in core.list_works(vault / root):
        rel = p.relative_to(vault).as_posix()
        st = p.stat()
        seen: dict[tuple[str, str], WorkNodeRef] = {}
        for r in refs.get(rel, []):
            seen.setdefault((r["id"], r["section"]), WorkNodeRef(**r))
        out.append(WorkEntry(path=rel, title=_work_title(p), size=st.st_size,
                             modified=dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                             refs=list(seen.values())))
    out.sort(key=lambda w: w.modified, reverse=True)
    return WorksRead(dir=root, works=out)


def work_file(vault: Path, rel: str) -> Path:
    """校验一个成品路径，回绝对路径给路由层端出去。能端的只有两种：
    成品目录里的 html，和某个节点的 sources 真链着的 html。校验口径同 `read_article`。"""
    clean = (rel or "").strip().lstrip("/")
    path = vault / clean
    try:
        path.resolve().relative_to(vault.resolve())
    except (ValueError, OSError) as exc:
        raise ArticleRejected(f"路径不在库里：{rel}") from exc
    if not clean.lower().endswith((".html", ".htm")) or not path.is_file():
        raise ArticleRejected(f"没有这个成品：{rel}")
    root = core.load_vault_config(vault)["works_dir"]
    if not clean.startswith(root + "/"):
        refs = core.source_backlink_refs(vault, current_index(vault))
        if clean not in refs:
            raise ArticleRejected(f"`{clean}` 不在成品目录 `{root}/` 里，也没有节点链着它")
    return path
