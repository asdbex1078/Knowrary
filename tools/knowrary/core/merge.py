"""把两个重复的知识点并成一个（阶段 7 第 4 项）。

重连关系的过程里撞重复是必然的——同一个概念先后用两个名字各建了一张卡。
Digest 早就能报"重复候选"，但一直只能手工删一个再挨个改引用，那正是最容易改漏的活。

**保留谁、丢弃谁由人定**，这里只负责：把丢弃那张卡的边、引用、位置、复习与答题记录
全部并到保留的那张上，正文追加到末尾（**绝不丢内容**），最后删掉那个文件。
"""
from __future__ import annotations

from pathlib import Path

from .mdio import RE_REL_HEADER, read, write, write_json_atomic
from .rename import (RenameRejected, _edge_touches, _link_pattern, _safe, _touches, canvases_with, layout_paths,
                     rewrite_drawn, scan_links)
from .writer import split_sections

MERGED_HEADER = "## 并入自"


class MergeRejected(RenameRejected):
    """请求本身不合法：两边是同一个、节点不存在、没有 md 文件等。"""


def _meta(index: dict, node_id: str, role: str) -> dict:
    meta = next((n for n in index["nodes"] if n["id"] == node_id), None)
    if meta is None:
        raise MergeRejected(f"{role} `{node_id}` 不在索引里")
    if meta.get("virtual") or not meta.get("path"):
        raise MergeRejected(f"{role} `{node_id}` 只是被引用的占位 stub，没有 md 文件")
    return meta


def plan_merge(vault: Path, index: dict, keep_id: str, drop_id: str) -> dict:
    """先算影响面，什么都不写。"""
    keep_id, drop_id = keep_id.strip(), drop_id.strip()
    if keep_id == drop_id:
        raise MergeRejected("保留和丢弃是同一个节点")
    keep, drop = _meta(index, keep_id, "保留的"), _meta(index, drop_id, "丢弃的")

    kept_pairs = {(e["type"], e["target"]) for e in index["edges"] if e["source"] == keep_id}
    moved, dropped = [], []
    for e in index["edges"]:
        if drop_id not in (e["source"], e["target"]):
            continue
        other = e["target"] if e["source"] == drop_id else e["source"]
        if other == keep_id:                                  # 两张卡之间那条边，并完就没意义了
            dropped.append(f"{e['source']} -{e['type']}→ {e['target']}（两者之间，并完自环）")
        elif e["source"] == drop_id and (e["type"], other) in kept_pairs:
            dropped.append(f"{e['source']} -{e['type']}→ {e['target']}（保留的那张已经有同样的边）")
        else:
            moved.append({"type": e["type"], "source": e["source"], "target": e["target"]})

    links = scan_links(vault, drop_id)
    body = _drop_body(read(vault / drop["path"])).strip()
    side = _side_impact(vault, drop_id)
    return {"keep_id": keep_id, "drop_id": drop_id,
            "keep_path": keep["path"], "drop_path": drop["path"],
            "moved_edges": moved, "dropped_edges": dropped,
            "links": sum(links.values()), "files": sorted(links),
            "body_chars": len(body), **side}


def _side_impact(vault: Path, drop_id: str) -> dict:
    layout = _safe(vault / ".knowrary" / "layout.json")
    review = _safe(vault / ".knowrary" / "review-log.json")
    quiz = _safe(vault / ".knowrary" / "quiz-log.json")
    projects = _safe(vault / ".knowrary" / "projects.json")
    return {
        "layout": drop_id in (layout.get("nodes") or {}),
        "layout_edges": sum(1 for k in (layout.get("edges") or {}) if _edge_touches(k, drop_id)),
        "refs": sum(1 for r in layout.get("refs") or [] if r.get("target") == drop_id),
        "reviews": len(((review.get("nodes") or {}).get(drop_id) or {}).get("reviews") or []),
        "quiz": sum(1 for a in quiz.get("answers") or [] if drop_id in (a.get("points") or [])),
        "projects": [pr.get("name") or pid for pid, pr in (projects.get("projects") or {}).items()
                     if any(pt.get("id") == drop_id for ls in pr.get("lists") or []
                            for st in ls.get("stages") or [] for pt in st.get("points") or [])],
        "canvases": canvases_with(vault, drop_id),
    }


def _drop_body(text: str) -> str:
    """丢弃那张卡要并过去的正文：关系段之前的，加上关系段**之后**的（规范允许 `## 参考资料` / `## 待办` 放在那儿）。
    以前只取前一段，后面的整段丢了。"""
    _, body, _, tail = split_sections(text)
    return body.rstrip("\n") + ("\n\n" + tail.strip("\n") if tail.strip() else "")


def _merge_body(keep_text: str, drop_id: str, drop_body: str) -> str:
    """把丢弃那张卡的正文追加到保留的末尾。

    **不做智能合并**——两段讲同一件事的话怎么揉，只有人知道。这里只保证内容不丢，
    并标出它是从哪并过来的，剩下的交给人自己删重复。
    """
    if not drop_body.strip():
        return keep_text
    block = f"\n{MERGED_HEADER} {drop_id}\n\n{drop_body.strip()}\n"
    # split_sections 的第三项是关系段的**内容**，不带 `## 关系` 这行标题——拼回去时得自己补上。
    # 以前当成「标题 + 内容」直接接，标题丢了，保留那张原有的关系行就掉进了正文（2026-10-13 修）
    fm, body, rels, tail = split_sections(keep_text)
    if not RE_REL_HEADER.search(keep_text):        # 没有关系段，直接接在正文后面
        return keep_text.rstrip("\n") + "\n" + block
    return fm + body.rstrip("\n") + "\n" + block + "\n## 关系" + rels + tail


def _merge_relations(vault: Path, impact: dict) -> None:
    """把要迁的边写进保留的那张卡；方向反过来的边写回它的源文件。"""
    keep_id, drop_id = impact["keep_id"], impact["drop_id"]
    keep_path = vault / impact["keep_path"]
    text = read(keep_path)
    drop_body = _drop_body(read(vault / impact["drop_path"]))
    text = _merge_body(text, drop_id, drop_body)

    own = [e for e in impact["moved_edges"] if e["source"] == drop_id]
    if own:
        lines = "".join(f"- {e['type']}:: [[{e['target']}]]\n" for e in own)
        if RE_REL_HEADER.search(text):
            fm, body, rels, tail = split_sections(text)
            text = fm + body + "## 关系\n" + rels.strip("\n") + ("\n" if rels.strip() else "") + lines + \
                ("\n" + tail if tail else "")
        else:
            text = text.rstrip("\n") + "\n\n## 关系\n" + lines
    write(keep_path, text)

    # 指向 drop 的边住在别人的文件里，改成指向 keep 就行（`[[ ]]` 替换会一并覆盖）
    pat = _link_pattern(drop_id)
    for rel in impact["files"]:
        path = vault / rel
        if path == vault / impact["drop_path"]:
            continue
        write(path, pat.sub(lambda m: f"[[{keep_id}{m.group(1) or ''}]]", read(path)))


def _merge_layout(vault: Path, keep_id: str, drop_id: str) -> None:
    for path in layout_paths(vault):                # 全局图和每张项目画布都要改
        _merge_one_layout(path, keep_id, drop_id)


def _merge_one_layout(path: Path, keep_id: str, drop_id: str) -> None:
    doc = _safe(path)
    if not doc or not _touches(doc, drop_id):
        return
    nodes = doc.get("nodes") or {}
    gone = nodes.pop(drop_id, None)                 # 位置以保留的那张为准，丢弃的直接去掉
    if gone is not None and keep_id not in nodes:
        nodes[keep_id] = gone                       # 这张画布上没有保留的那张：接过丢弃那张的位置，别让它从画布上消失
    edges = doc.get("edges") or {}
    for key in [k for k in edges if _edge_touches(k, drop_id)]:
        head, _, tail = key.partition("#")
        a, _, b = head.partition("->")
        a, b = (keep_id if a == drop_id else a), (keep_id if b == drop_id else b)
        style = edges.pop(key)
        if a != b:                                  # 并完变成自环的拐点没有意义
            edges.setdefault(f"{a}->{b}#{tail}", style)
    for ref in doc.get("refs") or []:
        if ref.get("target") == drop_id:
            ref["target"] = keep_id
    for g in (doc.get("groups") or {}).values():
        if g.get("doc") == drop_id:
            g["doc"] = keep_id
    rewrite_drawn(doc, drop_id, keep_id)
    doc["revision"] = int(doc.get("revision") or 0) + 1
    write_json_atomic(path, doc)


def _merge_records(vault: Path, keep_id: str, drop_id: str) -> None:
    review = vault / ".knowrary" / "review-log.json"
    doc = _safe(review)
    nodes = doc.get("nodes") or {}
    if drop_id in nodes:
        gone = nodes.pop(drop_id)
        cur = nodes.get(keep_id)
        if cur is None:
            nodes[keep_id] = gone
        else:
            cur["reviews"] = sorted([*(cur.get("reviews") or []), *(gone.get("reviews") or [])],
                                    key=lambda r: r.get("date") if isinstance(r, dict) else str(r))
            # step 取小的那个：合并不该让间隔凭空变长，宁可多考一次
            cur["step"] = min(cur.get("step", 0), gone.get("step", 0))
            cur["lapses"] = cur.get("lapses", 0) + gone.get("lapses", 0)
        write_json_atomic(review, doc)

    quiz = vault / ".knowrary" / "quiz-log.json"
    doc = _safe(quiz)
    if doc.get("answers"):
        for ans in doc["answers"]:
            pts = [keep_id if p == drop_id else p for p in ans.get("points") or []]
            ans["points"] = list(dict.fromkeys(pts))
        write_json_atomic(quiz, doc)

    path = vault / ".knowrary" / "projects.json"
    doc = _safe(path)
    if doc.get("projects"):
        for project in doc["projects"].values():
            for ls in project.get("lists") or []:
                for stage in ls.get("stages") or []:
                    seen, kept = set(), []
                    for pt in stage.get("points") or []:
                        pid = keep_id if pt.get("id") == drop_id else pt.get("id")
                        if pid in seen:
                            continue
                        seen.add(pid)
                        kept.append({**pt, "id": pid})
                    stage["points"] = kept
        doc["revision"] = int(doc.get("revision") or 0) + 1
        write_json_atomic(path, doc)


def apply_merge(vault: Path, impact: dict) -> dict:
    """按算好的影响面落盘。先并内容与引用，最后才删文件——顺序同 rename。"""
    _merge_relations(vault, impact)
    (vault / impact["drop_path"]).unlink(missing_ok=True)
    _merge_layout(vault, impact["keep_id"], impact["drop_id"])
    _merge_records(vault, impact["keep_id"], impact["drop_id"])
    return impact
