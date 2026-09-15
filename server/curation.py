"""Inbox / 放置 / Digest / 复习（阶段 4）：把 core 的算法接到 HTTP 上。

分成独立模块而不是堆进 app.py：这四件事共享"读 index + 读 layout"的前置，
而 app.py 里只留一层薄路由。写入仍然只走 layout_store.apply_patch，
revision 并发与备份逻辑一份不复制。
"""
from __future__ import annotations

import datetime as dt
import difflib
from pathlib import Path

from .contracts import (CoachToday, FileDiff, GroupPatch, InboxItem, InboxRead, LayoutDoc, LayoutPatch,
                        NodePatch, Placed, PlaceRequest, PlaceResult, ReviewDone)
from .index_service import current_index
from .layout_store import apply_patch, load_or_init
from .paths import core


class PlaceRejected(Exception):
    """请求本身不合法：节点不在索引里、指定的分组不存在等。"""


def load_pair(vault: Path) -> tuple[dict, LayoutDoc]:
    index = current_index(vault)
    layout, _ = load_or_init(vault, index)
    return index, layout


def inbox(vault: Path) -> InboxRead:
    """还没上画布的节点，附带建议分组。"""
    index, layout = load_pair(vault)
    plain = layout.model_dump()
    by_id = {n["id"]: n for n in index["nodes"]}
    items = []
    for nid in core.inbox_ids(index, plain):
        meta = by_id.get(nid, {})
        gid = core.target_group(nid, index, plain)
        items.append(InboxItem(
            id=nid, name=meta.get("name") or nid, field=meta.get("field"), desc=meta.get("desc"),
            stub=bool(meta.get("stub")), degree=int(meta.get("degree") or 0),
            suggested_group=gid, suggested_group_name=plain["groups"].get(gid, {}).get("name") if gid else None))
    return InboxRead(items=items, index_revision=index["revision"], layout_revision=layout.revision)


def _one_placement(nid: str, req: PlaceRequest, index: dict, plain: dict,
                   today: str) -> tuple[dict | None, dict[str, float]]:
    """算一个节点的落点。返回 (节点条目, 需要加高的分组)；人工指定坐标时不长框。"""
    if req.at is not None:
        gid = req.group or core.target_group(nid, index, plain)
        if not gid:
            return None, {}
        return {"x": req.at.x, "y": req.at.y, "w": core.NODE_W, "h": core.NODE_H,
                "group": gid, "state": req.state, "anchor": None, "placedAt": today}, {}
    box, grown = core.place_or_grow(nid, index, plain, today=today, gid=req.group)
    if box:
        box["state"] = req.state
    return box, grown


def place(vault: Path, req: PlaceRequest) -> PlaceResult:
    """把若干 Inbox 节点放上画布。逐个算，算完立刻并进工作副本，后面的才不会撞上。"""
    index, layout = load_pair(vault)
    plain = layout.model_dump()
    if req.group and req.group not in plain["groups"]:
        raise PlaceRejected(f"分组 `{req.group}` 不存在")
    if req.at is not None and len(req.ids) != 1:
        raise PlaceRejected("指定坐标时一次只能放一个节点")
    known = {n["id"] for n in index["nodes"] if not n.get("virtual")}
    today = dt.date.today().isoformat()

    placed, skipped, patch_nodes, patch_groups = [], [], {}, {}
    for nid in req.ids:
        if nid not in known:
            skipped.append({"id": nid, "reason": "不在索引里（可能是还没有 md 文件的占位 stub）"})
            continue
        if nid in plain["nodes"]:
            skipped.append({"id": nid, "reason": "已经在画布上了"})
            continue
        box, grown = _one_placement(nid, req, index, plain, today)
        if box is None:
            skipped.append({"id": nid, "reason": "目标分组放不下或判不出分组，留在 Inbox"})
            continue
        for gid, h in grown.items():                   # 组框长高也并进工作副本
            plain["groups"][gid]["h"] = h
            patch_groups[gid] = GroupPatch(h=h)
        plain["nodes"][nid] = box                      # 并进工作副本：下一个节点会避开它
        patch_nodes[nid] = NodePatch(**box)
        placed.append(Placed(id=nid, x=box["x"], y=box["y"], group=box["group"],
                             state=box["state"], anchor=box.get("anchor")))

    if not patch_nodes:
        return PlaceResult(revision=layout.revision, placed=[], skipped=skipped)
    patch = LayoutPatch(base_revision=req.base_revision, nodes=patch_nodes,
                        groups=patch_groups or None)
    doc, _, _ = apply_patch(vault, patch, index)
    return PlaceResult(revision=doc.revision, placed=placed, skipped=skipped,
                       grown_groups=sorted(patch_groups))


def digest(vault: Path) -> dict:
    """图谱本身的欠账。错题归今日清单（coach_today）管，这里不重复开第二个出口。"""
    index, layout = load_pair(vault)
    return core.build_digest(vault, index, layout.model_dump())


def coach_today(vault: Path) -> CoachToday:
    """今日清单：错题 > 到期 > 未建 > 只有壳 > Inbox。纯排序，不调 LLM，不写任何文件。"""
    index, layout = load_pair(vault)
    return CoachToday(**core.build_today(vault, index, layout.model_dump(), core.load_plans(vault)))


def review_due(vault: Path) -> dict:
    index = current_index(vault)
    items = core.due_nodes(index, core.load_log(vault))
    return {"due": items, "count": len(items), "generated_at": dt.date.today().isoformat()}


def mark_reviewed(vault: Path, node_id: str, grade: str = "记得") -> ReviewDone:
    index = current_index(vault)
    if not any(n["id"] == node_id and not n.get("virtual") for n in index["nodes"]):
        raise PlaceRejected(f"节点 `{node_id}` 不在索引里")
    entry = core.record_review(vault, node_id, grade=grade)
    return ReviewDone(id=node_id, reviews=len(entry["reviews"]), step=entry["step"],
                      lapses=entry["lapses"], next_due=entry.get("next_due"))


# ---------------------------------------------------------------- 变更预览（阶段 3 / 12 共用）

def diff_of(edit) -> str:
    """给人看的统一 diff（只保留有变化的片段）。"""
    lines = difflib.unified_diff(edit.before.splitlines(), edit.after.splitlines(),
                                 fromfile=f"a/{edit.rel}", tofile=f"b/{edit.rel}", lineterm="", n=2)
    return "\n".join(list(lines)[:60])


def preview(vault: Path, changes: list[dict], index: dict) -> list[FileDiff]:
    """算出这组变更会改成什么样，**不写盘**。

    `/api/changes` 的 dry_run 和对话里的「变更卡」用的是同一份：卡片上看到的 diff
    必须和真按下写入时写下去的一模一样，各算一遍迟早对不上。
    """
    edits = core.plan(vault, changes, index)
    return [FileDiff(path=e.rel, notes=e.notes, diff=diff_of(e)) for e in edits]
