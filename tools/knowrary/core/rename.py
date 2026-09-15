"""重命名一个知识点：改 id 的同时，把所有指向它的引用一起迁走。

**文件名就是 id**，所以在 Obsidian 里直接改名会一次断四类引用：别的节点 `## 关系`
里的 `[[旧id]]`、layout 里的位置与边样式、复习与答题记录、学习计划里的知识点。
事后再想补救是补不回来的——旧 id 已经消失，系统看到的只是"少了一个、多了一个"，
无从确认它俩是同一个东西。所以改名必须是一个**动作**，不能是事后的同步。

改名只动 id 与引用，不碰正文，也不改 frontmatter 里除 id 以外的任何字段。
"""
from __future__ import annotations

import re
from pathlib import Path

import datetime as dt
import shutil

from .mdio import load_json, read, walk_md, write, write_json_atomic

# 改名会动到的四份机器数据：位置、复习、答题、计划。备份必须把它们一起带上——
# 只备份 md 的话，一旦迁移出错，画布位置和复习进度是找不回来的。
SIDE_FILES = ("layout.json", "review-log.json", "quiz-log.json", "plans.json")

ID_BAD = re.compile(r'[\\/:*?"<>|\s]')


class RenameRejected(Exception):
    """请求本身不合法：id 非法、目标已存在、源节点没有 md 文件等。"""


def check(index: dict, old_id: str, new_id: str) -> dict:
    """校验并返回源节点的元数据。失败一律抛，不返回半对的结果。"""
    old_id, new_id = old_id.strip(), new_id.strip()
    if not new_id or ID_BAD.search(new_id):
        raise RenameRejected('新名字不能为空，也不能含空格或 / \\ : * ? " < > |（它同时是文件名）')
    if old_id == new_id:
        raise RenameRejected("新旧名字一样")
    by_id = {n["id"]: n for n in index["nodes"]}
    meta = by_id.get(old_id)
    if meta is None:
        raise RenameRejected(f"`{old_id}` 不在索引里")
    if meta.get("virtual") or not meta.get("path"):
        raise RenameRejected(f"`{old_id}` 只是被引用的占位 stub，还没有 md 文件，改名无从谈起")
    if new_id in by_id and not by_id[new_id].get("virtual"):
        raise RenameRejected(f"已经有一个叫 `{new_id}` 的知识点了")
    return meta


def _link_pattern(old_id: str) -> re.Pattern:
    """只换 `[[旧id]]` 这种整体匹配，不碰正文里恰好同名的普通文字。"""
    return re.compile(r"\[\[\s*" + re.escape(old_id) + r"\s*(\|[^\]]*)?\]\]")


def scan_links(vault: Path, old_id: str) -> dict[str, int]:
    """哪些 md 里有多少处 `[[旧id]]`。"""
    pat = _link_pattern(old_id)
    hits: dict[str, int] = {}
    for path in walk_md(vault):
        n = len(pat.findall(read(path)))
        if n:
            hits[str(path.relative_to(vault))] = n
    return hits


def _json_impact(vault: Path, old_id: str) -> dict:
    layout = _safe(vault / ".knowrary" / "layout.json")
    review = _safe(vault / ".knowrary" / "review-log.json")
    quiz = _safe(vault / ".knowrary" / "quiz-log.json")
    plans = _safe(vault / ".knowrary" / "plans.json")
    edge_keys = [k for k in (layout.get("edges") or {}) if _edge_touches(k, old_id)]
    refs = [r for r in (layout.get("refs") or []) if r.get("target") == old_id]
    docs = [g for g, v in (layout.get("groups") or {}).items() if v.get("doc") == old_id]
    hit_plans = [p.get("name") or pid for pid, p in (plans.get("plans") or {}).items()
                 if any(pt.get("id") == old_id for st in p.get("stages") or []
                        for pt in st.get("points") or [])]
    return {
        "layout": old_id in (layout.get("nodes") or {}),
        "layout_edges": len(edge_keys), "refs": len(refs), "docs": len(docs),
        "reviews": len(((review.get("nodes") or {}).get(old_id) or {}).get("reviews") or []),
        "quiz": sum(1 for a in quiz.get("answers") or [] if old_id in (a.get("points") or [])),
        "plans": hit_plans,
    }


def _safe(path: Path) -> dict:
    try:
        return load_json(path) if path.exists() else {}
    except (ValueError, OSError):
        return {}


def _edge_touches(key: str, node_id: str) -> bool:
    head = key.split("#", 1)[0]
    return node_id in head.split("->")


def plan_rename(vault: Path, index: dict, old_id: str, new_id: str) -> dict:
    """先算影响面给人看，什么都不写。"""
    meta = check(index, old_id, new_id)
    old_path = meta["path"]
    new_path = f"{old_path.rsplit('/', 1)[0]}/{new_id}.md" if "/" in old_path else f"{new_id}.md"
    if (vault / new_path).exists():
        raise RenameRejected(f"文件已存在：{new_path}")
    links = scan_links(vault, old_id)
    return {"old_id": old_id, "new_id": new_id, "path": old_path, "new_path": new_path,
            "links": sum(links.values()), "files": sorted(links), **_json_impact(vault, old_id)}


def _rewrite_md(vault: Path, old_id: str, new_id: str, files: list[str]) -> None:
    pat = _link_pattern(old_id)
    for rel in files:
        path = vault / rel
        text = read(path)
        write(path, pat.sub(lambda m: f"[[{new_id}{m.group(1) or ''}]]", text))


def _move_key(d: dict, old_id: str, new_id: str) -> None:
    if old_id in d:
        d[new_id] = d.pop(old_id)


def _rewrite_layout(vault: Path, old_id: str, new_id: str) -> None:
    path = vault / ".knowrary" / "layout.json"
    doc = _safe(path)
    if not doc:
        return
    _move_key(doc.get("nodes") or {}, old_id, new_id)
    edges = doc.get("edges") or {}
    for key in [k for k in edges if _edge_touches(k, old_id)]:
        head, _, tail = key.partition("#")
        a, _, b = head.partition("->")
        edges[f"{new_id if a == old_id else a}->{new_id if b == old_id else b}#{tail}"] = edges.pop(key)
    for ref in doc.get("refs") or []:
        if ref.get("target") == old_id:
            ref["target"] = new_id
    for g in (doc.get("groups") or {}).values():
        if g.get("doc") == old_id:
            g["doc"] = new_id
    doc["revision"] = int(doc.get("revision") or 0) + 1      # 让还开着的页面撞上 409 去重新拉
    write_json_atomic(path, doc)


def _rewrite_records(vault: Path, old_id: str, new_id: str) -> None:
    review = vault / ".knowrary" / "review-log.json"
    doc = _safe(review)
    if doc.get("nodes"):
        _move_key(doc["nodes"], old_id, new_id)
        write_json_atomic(review, doc)

    quiz = vault / ".knowrary" / "quiz-log.json"
    doc = _safe(quiz)
    if doc.get("answers"):
        for ans in doc["answers"]:
            ans["points"] = [new_id if p == old_id else p for p in ans.get("points") or []]
        write_json_atomic(quiz, doc)

    plans = vault / ".knowrary" / "plans.json"
    doc = _safe(plans)
    if doc.get("plans"):
        for plan in doc["plans"].values():
            for stage in plan.get("stages") or []:
                for point in stage.get("points") or []:
                    if point.get("id") == old_id:
                        point["id"] = new_id
                        if point.get("name") == old_id:
                            point["name"] = new_id
        doc["revision"] = int(doc.get("revision") or 0) + 1
        write_json_atomic(plans, doc)


def backup_rename(vault: Path, impact: dict) -> str:
    """改名前整份快照：涉及的 md + 四份机器数据。"""
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    root = vault / ".knowrary" / "backup" / f"rename-{stamp}"
    rels = [impact["path"], *impact["files"], *(f".knowrary/{n}" for n in SIDE_FILES)]
    for rel in dict.fromkeys(rels):                  # 去重且保持顺序
        src = vault / rel
        if not src.exists():
            continue
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return str(root.relative_to(vault))


def apply_rename(vault: Path, impact: dict) -> dict:
    """按算好的影响面落盘。顺序要紧：先改引用、再挪文件——

    反过来的话，中途出错会留下一个"文件已经改名、但半数引用还指着旧名"的现场，
    而这种现场解析器只会当成"未知目标"静静吞掉，不会报错。
    """
    old_id, new_id = impact["old_id"], impact["new_id"]
    _rewrite_md(vault, old_id, new_id, impact["files"])
    src, dst = vault / impact["path"], vault / impact["new_path"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    _rewrite_layout(vault, old_id, new_id)
    _rewrite_records(vault, old_id, new_id)
    return impact
