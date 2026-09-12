"""草稿放置（设计文档 3.9）：只在已有分组框内找空位，绝不移动已定稿的东西、绝不扩大分组框。

这是"机器只做增量"原则的落点：新节点最多被放进它该去的那个框里的空位，
放不下就留在 Inbox 让人处理，而不是把别人挤开或者把框撑大。
"""
from __future__ import annotations

import datetime as dt
import math

from .layout import CELL_H, NODE_H, NODE_W

FAMILY_WEIGHT = {"演化": 3.0, "依赖": 2.0, "结构": 2.0, "对照": 1.0, "弱关联": 0.5}
GAP = 16.0            # 候选位置与已有元素之间至少留这么多空
STEP = 40.0           # 螺旋搜索步长
MAX_RING = 40         # 最多搜这么多圈，搜不到就认输


def _rects_overlap(a: dict, b: dict) -> bool:
    return (a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"]
            and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"])


def _occupied(layout: dict, gid: str, box: dict | None = None) -> list[tuple[float, float, float, float]]:
    """框里已经占住的矩形：节点、引用卡、便签、图片都算。

    除了属于这个分组的，还算上"别的组压进框里"的元素——用户把两个分组框拖重叠之后，
    只看本组成员就会把新节点摞到人家头上。
    """
    out = []
    def consider(item, group):
        rect = (item["x"], item["y"], item.get("w") or NODE_W, item.get("h") or NODE_H)
        if group == gid or (box is not None and _rects_overlap(
                {"x": rect[0], "y": rect[1], "w": rect[2], "h": rect[3]}, box)):
            out.append(rect)

    for n in layout.get("nodes", {}).values():
        consider(n, n.get("group"))
    for key in ("refs", "notes", "images"):
        for item in layout.get(key) or []:
            consider(item, item.get("group"))
    return out


def target_group(node_id: str, index: dict, layout: dict) -> str | None:
    """该去哪个分组。

    先按关系族加权投票——邻居里哪个分组的"关系分"最高就去哪，结构族优先体现在权重上；
    取第一个匹配到的邻居会被随机的边顺序带偏（实测把 CPU 判到了只有 1 个节点的小组）。
    投不出来再按 field 找同名的顶层分组。
    """
    votes: dict[str, float] = {}
    for e in index["edges"]:
        other = e["target"] if e["source"] == node_id else e["source"] if e["target"] == node_id else None
        if other is None:
            continue
        gid = layout.get("nodes", {}).get(other, {}).get("group")
        if gid in layout.get("groups", {}):
            votes[gid] = votes.get(gid, 0) + FAMILY_WEIGHT.get(e["family"], 0.5)
    if votes:
        return max(sorted(votes), key=lambda k: votes[k])
    node = next((n for n in index["nodes"] if n["id"] == node_id), None)
    field = (node or {}).get("field")
    for gid, g in sorted(layout.get("groups", {}).items()):
        if not g.get("parent") and g.get("name") == field:
            return gid
    return None


def anchor_for(node_id: str, gid: str, index: dict, layout: dict) -> str | None:
    """锚点：同组里与它关系最紧的那个已定稿节点（按关系族加权）。"""
    scores: dict[str, float] = {}
    for e in index["edges"]:
        other = e["target"] if e["source"] == node_id else e["source"] if e["target"] == node_id else None
        if other is None:
            continue
        place = layout.get("nodes", {}).get(other)
        if not place or place.get("group") != gid or place.get("state") == "draft":
            continue
        scores[other] = scores.get(other, 0) + FAMILY_WEIGHT.get(e["family"], 0.5)
    return max(sorted(scores), key=lambda k: scores[k]) if scores else None


def _overlaps(a: tuple, b: tuple) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw + GAP <= bx or bx + bw + GAP <= ax or ay + ah + GAP <= by or by + bh + GAP <= ay)


def _spiral(start: tuple[float, float]) -> list[tuple[float, float]]:
    out = []
    for ring in range(MAX_RING):
        radius = STEP * (ring + 1)
        steps = max(8, ring * 6)
        for i in range(steps):
            angle = 2 * math.pi * i / steps
            out.append((start[0] + radius * math.cos(angle), start[1] + radius * math.sin(angle) * 0.6))
    return out


def _grid(box: dict) -> list[tuple[float, float]]:
    """螺旋搜不到时，把整个框按网格扫一遍——框里可能只在角落有空隙。"""
    out = []
    y = box["y"] + 44
    while y + NODE_H <= box["y"] + box["h"]:
        x = box["x"] + 12
        while x + NODE_W <= box["x"] + box["w"]:
            out.append((x, y))
            x += STEP / 2
        y += STEP / 2
    return out


def find_slot(box: dict, occupied: list[tuple], start: tuple[float, float]) -> tuple[float, float] | None:
    """先从锚点周围螺旋找，找不到再整框网格扫；必须整体落在分组框内且不压到别人。"""
    for x, y in _spiral(start) + _grid(box):
        x, y = round(x), round(y)
        inside = (box["x"] <= x and box["y"] + 40 <= y
                  and x + NODE_W <= box["x"] + box["w"] and y + NODE_H <= box["y"] + box["h"])
        if not inside:
            continue
        if any(_overlaps((x, y, NODE_W, NODE_H), other) for other in occupied):
            continue
        return x, y
    return None


def place_node(node_id: str, index: dict, layout: dict, today: str | None = None,
               gid: str | None = None) -> dict | None:
    """给一个 Inbox 节点找位置。返回可直接写进 layout.nodes 的条目，找不到返回 None。

    `gid` 是人工指定的目标分组（拖进某个框）；不给就按邻居投票自己判。
    """
    gid = gid or target_group(node_id, index, layout)
    if not gid or gid not in layout.get("groups", {}):
        return None
    box = layout["groups"][gid]
    occupied = _occupied(layout, gid, box)
    anchor = anchor_for(node_id, gid, index, layout)
    anchor_box = layout["nodes"].get(anchor) if anchor else None
    start = ((anchor_box["x"] + NODE_W + STEP, anchor_box["y"]) if anchor_box
             else (box["x"] + 24, box["y"] + 44))
    slot = find_slot(box, occupied, start)
    if slot is None:
        return None
    return {"x": float(slot[0]), "y": float(slot[1]), "w": NODE_W, "h": NODE_H, "group": gid,
            "state": "draft", "anchor": anchor, "placedAt": today or dt.date.today().isoformat()}


def plan_growth(gid: str, layout: dict, extra: float = CELL_H) -> dict[str, float] | None:
    """组里满了：把这个组和它的所有祖先都往下长 extra。

    初始布局的组框是按网格严丝合缝算出来的，网格排满时框内一个空位都没有——
    不许长框就等于新节点永远进不来。长框只往下长，不动任何已有元素；
    但只要某一层长出来会压到它的同级兄弟，就整体作废（节点留在 Inbox 让人处理）。
    返回 {分组 id: 新高度}。
    """
    groups = layout.get("groups", {})
    grown: dict[str, float] = {}
    cur: str | None = gid
    while cur and cur in groups:
        box = groups[cur]
        candidate = {**box, "h": box["h"] + extra}
        siblings = [o for oid, o in groups.items() if oid != cur and o.get("parent") == box.get("parent")]
        # 只有"长出来才新压上的"兄弟才拦；两个框本来就被人拖重叠了的话，不该因此永远长不了
        if any(_rects_overlap(candidate, sib) and not _rects_overlap(box, sib) for sib in siblings):
            return None
        grown[cur] = candidate["h"]
        cur = box.get("parent")
    return grown or None


def place_or_grow(node_id: str, index: dict, layout: dict, today: str | None = None,
                  gid: str | None = None) -> tuple[dict | None, dict[str, float]]:
    """先按 place_node 在框内找空位；框内排满了就长一行再找。

    返回 (节点条目, 要改高度的分组)。两者要在同一次 PATCH 里一起写，
    否则会出现"节点站在框外面"的中间状态。
    """
    box = place_node(node_id, index, layout, today=today, gid=gid)
    if box is not None:
        return box, {}
    gid = gid or target_group(node_id, index, layout)
    if not gid or gid not in layout.get("groups", {}):
        return None, {}
    grown = plan_growth(gid, layout)
    if not grown:
        return None, {}
    taller = {**layout, "groups": {**layout["groups"],
                                   **{k: {**layout["groups"][k], "h": h} for k, h in grown.items()}}}
    box = place_node(node_id, index, taller, today=today, gid=gid)
    return (box, grown) if box else (None, {})


def inbox_ids(index: dict, layout: dict) -> list[str]:
    """还没放到画布上的节点（虚拟 stub 不算，它们连文件都还没有）。"""
    placed = set(layout.get("nodes", {}))
    return sorted(n["id"] for n in index["nodes"] if not n.get("virtual") and n["id"] not in placed)
