"""层区块（2026-10-12 归属整理第 ⑤-3 项）：领域框里按抽象层划出来的底色区域，**不是分组**。

以前领域框下面套一层「层」子分组（`g-计算机系统--理论`），于是：
- 归属有两份真值——md 里的 field / layer 和画布上的 group，改了 md 画布不动（Digest 里的 misplaced 就是在补这个洞）；
- 「计算机系统」和「AI」下各有一个「理论」「硬件」，选择器里并排站着两个同名分组。

现在层框降为**区块**：`groups` 里还留着它（`kind: "zone"`，坐标、大小、颜色、层名都在——
那是他手摆的二维网格，一点不动），但它只是一块底色：
- 节点的 `group` 指向**领域框**；属于哪一层只看 md 的 `layer`（主层）；
- 放置时区块仍然是「往哪块空地里放」的那个矩形（新点落进自己那一层的区块），只是写进 layout 的 group 是领域框；
- 区块不单独折叠 / 钉住 / 钻入，跟着领域框走；画布缩小时领域内跨层的边按区块捆成粗线。

哪些框是区块**靠显式标记**，不靠猜：`plan_migration` 把「父是顶层框、名字是层名」的子框标成区块，
目录分组（`--by dir` 铺出来的）和他自己建的子簇不动。
"""
from __future__ import annotations

from .parser import LAYERS, UNLAYERED

ZONE = "zone"
LAYER_NAMES = (*LAYERS, UNLAYERED)


def is_zone(group: dict | None) -> bool:
    return bool(group) and group.get("kind") == ZONE


def home_of(gid: str | None, groups: dict) -> str | None:
    """节点真正归属的分组：区块的话是它所在的领域框，别的分组就是它自己。"""
    g = groups.get(gid or "")
    return g.get("parent") if is_zone(g) else gid


def zones_in(parent: str, groups: dict) -> dict[str, str]:
    """领域框里的区块：层名 → 区块 id。"""
    return {g["name"]: gid for gid, g in sorted(groups.items()) if is_zone(g) and g.get("parent") == parent}


def zone_for(parent: str, layer: str | None, groups: dict) -> str | None:
    """这一层在这个领域框里的区块。没分层的落「未分层」区块；没有对应区块就是 None。"""
    return zones_in(parent, groups).get(layer or UNLAYERED)


def zone_at(box: dict, parent: str, groups: dict) -> str | None:
    """节点中心落在这个领域框的哪个区块里（几何判定）。不在任何区块里返回 None。"""
    cx = box["x"] + (box.get("w") or 0) / 2
    cy = box["y"] + (box.get("h") or 0) / 2
    for zid in zones_in(parent, groups).values():
        z = groups[zid]
        if z["x"] <= cx <= z["x"] + z["w"] and z["y"] <= cy <= z["y"] + z["h"]:
            return zid
    return None


def refine(gid: str | None, node: dict, groups: dict) -> str | None:
    """放置的目标框：给的是一个有区块的领域框，就换成这个点主层的那个区块（新点落进自己那一层）。"""
    if not gid or is_zone(groups.get(gid)):
        return gid
    return zone_for(gid, node.get("layer"), groups) or gid


# ---------------------------------------------------------------- 一次性迁移

def _is_layer_box(gid: str, groups: dict) -> bool:
    g = groups[gid]
    parent = groups.get(g.get("parent") or "")
    return (not is_zone(g) and parent is not None and not parent.get("parent")
            and g.get("name") in LAYER_NAMES)


def plan_migration(layout: dict) -> dict:
    """把层子分组标成区块：返回 `{groups, nodes, items, zones}`（补丁 + 要标的区块 id），不改原 layout。

    - 区块：`kind: "zone"`，坐标大小不动；`pinned` 清掉（区块不单独折叠）；
    - 节点 / 引用卡 / 便签 / 图片：`group` 是区块的，改指它的领域框，坐标不动；
    - 以区块为父的子簇（他在层框里建的）：父改成领域框，自己不动。
    """
    groups = layout.get("groups") or {}
    zones = [gid for gid in sorted(groups) if _is_layer_box(gid, groups)]
    parent = {z: groups[z]["parent"] for z in zones}
    gpatch = {z: {"kind": ZONE, "pinned": None} for z in zones}
    for gid, g in groups.items():
        if g.get("parent") in parent and gid not in parent:
            gpatch[gid] = {"parent": parent[g["parent"]]}
    npatch = {nid: {"group": parent[n["group"]]} for nid, n in (layout.get("nodes") or {}).items()
              if n.get("group") in parent}
    items = {key: [i for i, it in enumerate(layout.get(key) or []) if it.get("group") in parent]
             for key in ("refs", "notes", "images")}
    return {"zones": zones, "groups": gpatch, "nodes": npatch, "items": {k: v for k, v in items.items() if v}}


def apply_migration(layout: dict, plan: dict) -> dict:
    """按计划改一份新的 layout（不动原对象），revision +1。"""
    groups = {gid: {**g, **plan["groups"].get(gid, {})} for gid, g in (layout.get("groups") or {}).items()}
    nodes = {nid: {**n, **plan["nodes"].get(nid, {})} for nid, n in (layout.get("nodes") or {}).items()}
    home = {z: layout["groups"][z]["parent"] for z in plan["zones"]}
    out = {**layout, "groups": groups, "nodes": nodes, "revision": int(layout.get("revision") or 0) + 1}
    for key, idxs in plan["items"].items():
        out[key] = [{**it, "group": home[it["group"]]} if i in idxs else it
                    for i, it in enumerate(layout.get(key) or [])]
    return out
