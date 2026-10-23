"""专题图：以一个知识点为中心，按固定方位把相关的点摆开（2026-10-13）。

    上：谁在用它（可以一层叠一层：C → C++ → Java）
    左：前身（沿「源自」往前）   中：它自己的结构（`部件::` 一排方块）   右：后继（沿「演化为」往后）
                                 同类对照（对比 / 类比）
    下：它跑在什么上（扇区、磁盘……）

**主题是数据，不是模块**：中心就是一个普通知识点，frontmatter 写 `专题: 文件系统` 即成一张图；
方块顺序就是中心 md 里 `部件::` 的书写顺序。这里只按索引里已有的边**算**每个点该在哪个方位、
第几跳，不存第二份关系——手摆的位置另存（画布那一步的事）。

方位规则（他 2026-10-13 定的）：
- 上下按「依赖」族的边一跳一跳往外走，每跳一条带。**抽象层只定朝上还是朝下**：两个点层不同，
  层高的在上；同层（C 和 C++ 都是高级语言）或缺层时看边的方向——谁依赖谁，谁就在上。
  朝上走出去的点只继续朝上走，不会折回来。
- 左右沿「演化」族的边走：中心演化出去的在右，演化到中心的在左；分叉照样收进来，`via` 记它挂在谁后面。
- 「对照」族（对比 / 类比 / 争议）只看中心的一跳，放中心那一行。
- 每个方向默认走 3 跳，每张图可单独改；超出的、以及中心和方块的其余一跳邻居，进「待上图」由人拖。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from .parser import LAYERS

TOPIC_KEY = "专题"
PART_TYPES = ("部件", "包含")       # 中心 md 里这两种关系的目标是方块（属于:: 中心 归一后也是 包含）
DEFAULT_DEPTH = 3
SIDES = ("up", "down", "left", "right")
DEP, EVO, CMP, STRUCT = "依赖", "演化", "对照", "结构"
_LAYER_RANK = {name: i for i, name in enumerate(LAYERS)}


class TopicNotFound(KeyError):
    """没有哪个点声明了这个专题。"""


def topic_name(fm: dict) -> str:
    """frontmatter 里的 `专题` → 专题名；没写或写空返回空串。"""
    value = fm.get(TOPIC_KEY)
    return str(value).strip() if value not in (None, "", []) else ""


def topics(index: dict) -> list[dict]:
    """库里所有专题：{topic, center, name}，按专题名排。"""
    out = [{"topic": n["topic"], "center": n["id"], "name": n.get("name") or n["id"]}
           for n in index["nodes"] if n.get("topic")]
    return sorted(out, key=lambda t: (t["topic"], t["center"]))


@dataclass
class _Walk:
    """一次算图的共享状态：邻接、点表、已经有方位的点、超出跳数的点。"""
    adj: dict
    by_id: dict
    taken: set
    overflow: dict = field(default_factory=dict)


def topic_map(index: dict, topic: str, depth: dict | None = None) -> dict:
    """算一张专题图：每个点在哪个方位、第几跳、挂在谁后面。`depth` 按方位覆盖默认的 3 跳。"""
    by_id = {n["id"]: n for n in index["nodes"]}
    center = _center_of(index, topic)
    limits = {side: int((depth or {}).get(side, DEFAULT_DEPTH)) for side in SIDES}
    adj = _adjacency(index)
    parts = _parts(by_id[center], adj[center])
    core = [center, *parts]
    w = _Walk(adj, by_id, set(core))
    left = _walk(w, [center], limits["left"], lambda nid, e, other, out: e["family"] == EVO and not out, "演化链")
    right = _walk(w, [center], limits["right"], lambda nid, e, other, out: e["family"] == EVO and out, "演化链")
    compare = _compare(w, center)
    up, down = (_walk(w, core, limits[side], _vertical(w, side), "往上" if side == "up" else "往下")
                for side in ("up", "down"))
    return {"topic": topic, "center": center, "parts": parts, "up": up, "down": down,
            "left": left, "right": right, "compare": compare, "depth": limits,
            "pending": _pending(w, core)}


def _center_of(index: dict, topic: str) -> str:
    hits = sorted(n["id"] for n in index["nodes"] if n.get("topic") == topic)
    if not hits:
        raise TopicNotFound(topic)
    return hits[0]          # 两个点抢同一个专题名时索引已经报了警告，这里取 id 小的那个，结果稳定


def _adjacency(index: dict) -> dict[str, list[tuple[dict, str, bool]]]:
    """id → [(边, 另一端, 这条边是不是从它出发)]。"""
    adj: dict[str, list] = defaultdict(list)
    for e in index["edges"]:
        adj[e["source"]].append((e, e["target"], True))
        adj[e["target"]].append((e, e["source"], False))
    return adj


def _parts(center: dict, links: list) -> list[str]:
    """方块：中心 md 里写的顺序在前，别的点用 `属于:: 中心` 认过来的按 id 接在后面。"""
    listed = [p for p in center.get("parts") or [] if p != center["id"]]
    extra = sorted(other for e, other, out in links
                   if out and e["family"] == STRUCT and e["type"] in PART_TYPES and other not in listed)
    return list(dict.fromkeys([*listed, *extra]))


def _walk(w: _Walk, starts: list[str], limit: int, accept: Callable, label: str) -> list[dict]:
    """从 `starts` 一跳一跳往外走，`accept(当前点, 边, 另一端, 是否出边)` 决定这条边算不算这个方位。

    第 limit+1 跳还会看一眼：那些点不上图，进待上图栏并注明超了几跳——不然人不知道还有。
    """
    found, frontier = [], list(starts)
    for hop in range(1, limit + 2):
        step = []
        for nid in frontier:
            for e, other, out in w.adj[nid]:
                if other in w.taken or not accept(nid, e, other, out):
                    continue
                w.taken.add(other)
                item = {"id": other, "hop": hop, "via": nid, "edge": e["id"]}
                if hop > limit:
                    w.overflow.setdefault(other, {**item, "why": f"{label}超过 {limit} 跳"})
                else:
                    step.append(item)
        found += sorted(step, key=lambda it: (it["via"], it["id"]))
        frontier = [it["id"] for it in step]
    return found


def _compare(w: _Walk, center: str) -> list[dict]:
    out = []
    for e, other, _ in w.adj[center]:
        if e["family"] == CMP and other not in w.taken:
            w.taken.add(other)
            out.append({"id": other, "hop": 1, "via": center, "edge": e["id"]})
    return sorted(out, key=lambda it: it["id"])


def side_of(by_id: dict, here: str, edge: dict, other: str) -> str:
    """`other` 相对 `here` 在上还是在下：层不同看层，层相同 / 缺层看谁依赖谁（依赖方在上）。"""
    a = _LAYER_RANK.get((by_id.get(here) or {}).get("layer"))
    b = _LAYER_RANK.get((by_id.get(other) or {}).get("layer"))
    if a is not None and b is not None and a != b:
        return "up" if b > a else "down"
    return "up" if edge["source"] == other else "down"


def _vertical(w: _Walk, side: str) -> Callable:
    """上 / 下的取边规则：依赖族、且另一端相对当前点正好在 `side` 那边。"""
    return lambda nid, e, other, out: e["family"] == DEP and side_of(w.by_id, nid, e, other) == side


_WHY = {STRUCT: "结构关系", DEP: "依赖关系", EVO: "演化关系", CMP: "对照关系"}


def _pending(w: _Walk, core: list[str]) -> list[dict]:
    """待上图：超出跳数的，加上中心和方块的一跳邻居里哪个方位都没收的（弱关联、方块自己的演化 / 对照……）。"""
    out = dict(w.overflow)
    for nid in core:
        for e, other, _ in w.adj[nid]:
            if other in w.taken or other in out:
                continue
            out[other] = {"id": other, "hop": 1, "via": nid, "edge": e["id"],
                          "why": _WHY.get(e["family"], "弱关联")}
    return sorted(out.values(), key=lambda it: it["id"])
