"""草稿放置（设计文档 3.9）：只在已有分组框内找空位，绝不移动已定稿的东西、绝不扩大分组框。

这是"机器只做增量"原则的落点：新节点最多被放进它该去的那个框里的空位，
放不下就留在 Inbox 让人处理，而不是把别人挤开或者把框撑大。
"""
from __future__ import annotations

import datetime as dt
import math

from .layout import CELL_H, FIELD_GAP, NODE_H, NODE_W, PAD_BOT, PAD_TOP, PAD_X, group_size
from .parser import UNLAYERED

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
    return by_field_and_layer(node or {}, layout)


def by_field_and_layer(node: dict, layout: dict) -> str | None:
    """一条边都没有的节点：按 field 找顶层分组，再按 layer 落到它下面那条泳道。

    只认 field 会把节点丢在**父框里、所有泳道之外**——分组按抽象层切开之后，
    父框里那块空地不属于任何一层，节点落在那儿等于没分层。
    没有 layer 的落「未分层」那条（它本来就是为这些点留的），连子框都没有才退回父框。
    """
    groups = layout.get("groups", {})
    top = next((gid for gid, g in sorted(groups.items())
                if not g.get("parent") and g.get("name") == node.get("field")), None)
    if top is None:
        return None
    wanted = node.get("layer") or UNLAYERED
    # 逐层往下找同名子框：现在是 field → layer 两层，多包一层也不至于失灵
    seen, frontier = {top}, [top]
    while frontier:
        children = [gid for gid, g in sorted(groups.items())
                    if g.get("parent") in frontier and gid not in seen]
        if not children:
            break
        hit = next((gid for gid in children if groups[gid].get("name") == wanted), None)
        if hit:
            return hit
        seen.update(children)
        frontier = children
    return top


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


def plan_new_lane(parent: str, name: str, layout: dict,
                  gid: str | None = None) -> tuple[str, dict, dict] | None:
    """在 `parent` 里开一块新的子域（那一层的道）。返回 (gid, 新框, 父框高度补丁)。

    **什么时候需要**：`field` 改了、该去的那条道却还没建。实盘的例子是把「图灵测试」
    的 field 改成 AI —— AI 下面只有 硬件 / 体系结构 / 系统软件 / AI应用，没有「理论」，
    于是它只能留在原地，而没有任何地方会提醒你少了一条道。

    摆法跟着**这个父框已有的排法走**，不自作主张：
    · 兄弟是一摞泳道（各自满宽、上下码）→ 新的也满宽，接在最后一条下面；
    · 兄弟是并排的块 → 右边还塞得下就并上去，塞不下就另起一行，落在左边。

    只往下长父框，**不动任何已有的框**。长出来会压到父框的兄弟就整体作废（返回 None）——
    宁可让人自己拖，也不能为了塞一条新道把旁边的域挤变形。
    """
    groups = layout.get("groups", {})
    box = groups.get(parent)
    if not box or any(g.get("name") == name and g.get("parent") == parent for g in groups.values()):
        return None
    kids = [g for g in groups.values() if g.get("parent") == parent]
    gid = gid or f"{parent}--{name}"
    if gid in groups:
        gid = f"g-{name}-{len(groups)}"

    inner_x = box["x"] + PAD_X
    inner_right = box["x"] + box["w"] - PAD_X
    if not kids:                                   # 头一个子域：铺满内宽
        lane = {"x": inner_x, "y": box["y"] + PAD_TOP, "w": inner_right - inner_x, "h": CELL_H * 2}
    elif is_lane_stack(parent, layout):
        last = max(kids, key=lambda g: g["y"])
        lane = {"x": last["x"], "y": last["y"] + last["h"] + GAP, "w": last["w"], "h": CELL_H * 2}
    else:
        row_y = min(g["y"] for g in kids)          # 并排的块：先看最上面那一行右边还有没有空
        row = [g for g in kids if abs(g["y"] - row_y) < 1]
        right = max(g["x"] + g["w"] for g in row)
        width = min(g["w"] for g in kids)
        if right + GAP * 2 + width <= inner_right:
            lane = {"x": right + GAP * 2, "y": row_y, "w": width, "h": max(g["h"] for g in row)}
        else:                                      # 右边满了：另起一行，落在左边
            lane = {"x": inner_x, "y": max(g["y"] + g["h"] for g in kids) + GAP * 2,
                    "w": width, "h": CELL_H * 2}

    lane.update({"name": name, "parent": parent, "collapsed": False, "pinned": None, "color": None})
    need = (lane["y"] + lane["h"] + PAD_BOT) - (box["y"] + box["h"])
    grown = plan_growth(parent, layout, extra=need) if need > 0 else {}
    if need > 0 and grown is None:
        return None                                # 长出来会压到隔壁的域，宁可不建
    return gid, lane, grown or {}


def is_lane_stack(parent: str | None, layout: dict) -> bool:
    """这个父框下面是不是一摞**泳道**：子框各自横跨整幅宽度、上下排开、互不重叠。

    这件事要判出来，是因为两种"兄弟挡路"完全不同：
    并排的两个域挤在一起时把人家推走是破坏排版；而泳道本来就是从上到下码的，
    给中间那条加一行、下面整体下移，正是人手会做的那一下。
    """
    groups = layout.get("groups", {})
    box = groups.get(parent) if parent else None
    if not box:
        return False
    lanes = [g for g in groups.values() if g.get("parent") == parent]
    if len(lanes) < 2:
        return False
    if any(g["w"] < box["w"] * 0.85 for g in lanes):
        return False                       # 有子框没横跨整幅：不是泳道，是并排的块
    lanes = sorted(lanes, key=lambda g: g["y"])
    return all(a["y"] + a["h"] <= b["y"] + 1 for a, b in zip(lanes, lanes[1:]))


def plan_lane_growth(gid: str, layout: dict, extra: float = CELL_H) -> tuple[dict, dict] | None:
    """给一条泳道加一行：它自己长高，**下面的泳道连同里面的节点整体下移**。

    泳道框是按"当时有几个点"算出来的，常常只装得下一两个；不许长就等于
    这条道以后再也进不来新点（实测 248x128 的道，容量正好 1 个）。
    返回 ({分组 id: 局部补丁}, {节点 id: 局部补丁})。
    """
    groups = layout.get("groups", {})
    box = groups.get(gid)
    if not box or not is_lane_stack(box.get("parent"), layout):
        return None
    gpatch: dict[str, dict] = {gid: {"h": box["h"] + extra}}
    npatch: dict[str, dict] = {}
    bottom = box["y"] + box["h"]

    moved_groups = {oid for oid, g in groups.items()
                    if g.get("parent") == box.get("parent") and oid != gid and g["y"] >= bottom - 1}
    # 子孙框跟着走，否则嵌套的那层会被留在原地
    while True:
        more = {oid for oid, g in groups.items()
                if g.get("parent") in moved_groups and oid not in moved_groups}
        if not more:
            break
        moved_groups |= more
    for oid in moved_groups:
        gpatch[oid] = {"y": groups[oid]["y"] + extra}
    for nid, place in (layout.get("nodes") or {}).items():
        if place.get("group") in moved_groups or (
                place.get("group") == box.get("parent") and place.get("y", 0) >= bottom - 1):
            npatch[nid] = {"y": place["y"] + extra}

    # 祖先也要跟着长高，否则整摞泳道会顶出父框
    cur = box.get("parent")
    while cur and cur in groups:
        gpatch.setdefault(cur, {})["h"] = groups[cur]["h"] + extra
        cur = groups[cur].get("parent")
    return gpatch, npatch


def _apply_patch(layout: dict, gpatch: dict, npatch: dict) -> dict:
    """把补丁应用到一份工作副本上（不改原 layout）。"""
    groups = {gid: {**g, **gpatch.get(gid, {})} for gid, g in (layout.get("groups") or {}).items()}
    nodes = {nid: {**n, **npatch.get(nid, {})} for nid, n in (layout.get("nodes") or {}).items()}
    return {**layout, "groups": groups, "nodes": nodes}


def place_or_grow(node_id: str, index: dict, layout: dict, today: str | None = None,
                  gid: str | None = None) -> tuple[dict | None, dict[str, dict], dict[str, dict]]:
    """先按 place_node 在框内找空位；框内排满了就长一行再找。

    返回 (节点条目, 分组补丁, 节点补丁)。三者要在同一次 PATCH 里一起写，
    否则会出现"节点站在框外面"的中间状态。
    """
    box = place_node(node_id, index, layout, today=today, gid=gid)
    if box is not None:
        return box, {}, {}
    gid = gid or target_group(node_id, index, layout)
    if not gid or gid not in layout.get("groups", {}):
        return None, {}, {}
    grown = plan_growth(gid, layout)
    if grown:
        gpatch = {k: {"h": h} for k, h in grown.items()}
        box = place_node(node_id, index, _apply_patch(layout, gpatch, {}), today=today, gid=gid)
        if box:
            return box, gpatch, {}
    lane = plan_lane_growth(gid, layout)
    if lane:
        gpatch, npatch = lane
        box = place_node(node_id, index, _apply_patch(layout, gpatch, npatch), today=today, gid=gid)
        if box:
            return box, gpatch, npatch
    return None, {}, {}


def inbox_ids(index: dict, layout: dict) -> list[str]:
    """还没放到画布上的节点（虚拟 stub 不算，它们连文件都还没有）。

    聚合文档也不算：Inbox 问的是"这个知识点该放进哪个域框"，而对比组根本不上全局画布、
    领域总览早就摆好了。不排掉的话，每建一个对比组 Inbox 就多一条永远处理不掉的待办。
    """
    placed = set(layout.get("nodes", {}))
    return sorted(n["id"] for n in index["nodes"]
                  if not n.get("virtual") and not n.get("aggregate") and n["id"] not in placed)


def plan_field_group(field: str, layout: dict) -> tuple[str, dict] | None:
    """给一个还没有域框的领域开一个顶层框，接在整张图最下面。返回 (gid, 框)；已经有同名顶层框就返回 None。

    什么时候需要：导入了一个全新领域的笔记——`by_field_and_layer` 找不到同名顶层框，节点只能留在 Inbox
    "需手动拖"，而画布上根本没有可拖的目标。开框是**加法**：接在所有内容下面、不动任何已有的东西。
    框先按两行四列的量给，放满了 place_or_grow 会自己往下长；里面的泳道（按 layer）留给 regroup 的
    create_lane，一个新领域头几个点还谈不上分层。
    """
    groups = layout.get("groups", {})
    if not field or any(not g.get("parent") and g.get("name") == field for g in groups.values()):
        return None
    gid = f"g-{field}"
    if gid in groups:
        gid = f"g-{field}-{len(groups)}"
    bottom = 0.0
    for g in groups.values():
        if not g.get("parent"):
            bottom = max(bottom, g["y"] + g["h"])
    for n in layout.get("nodes", {}).values():
        if not n.get("group"):
            bottom = max(bottom, n["y"] + (n.get("h") or NODE_H))
    w, h = group_size(8)
    return gid, {"name": field, "x": 0.0, "y": bottom + (FIELD_GAP if bottom else 0.0), "w": w, "h": h,
                 "parent": None, "collapsed": False, "pinned": None, "color": "#eef2f8", "doc": None}
