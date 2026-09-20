"""layout.json：初始布局生成与结构常量（零第三方依赖，CLI 与服务层共用）。

layout 是"结构视图的用户数据"，与 md（知识真相源）、index（派生缓存）三者互不覆盖。
这里只负责首次生成一张能看的图：按 field 建顶层分组、按 nodes/ 子目录建二级分组、
组内网格排列。之后所有位置都由人工拖拽决定，不做自动重排。
虚拟 stub（被引用但没有 md 文件）不进 layout——它们还不是文件，等补写后再进。
"""
from __future__ import annotations

import datetime as dt
import math
from pathlib import Path
from typing import Iterable

LAYOUT_SCHEMA_VERSION = 2
NODE_W, NODE_H = 196.0, 64.0                   # 与前端最大号节点一致，网格才不会叠框
CELL_W, CELL_H = NODE_W + 44, NODE_H + 36      # 节点格子（含间距）
PAD_X, PAD_TOP, PAD_BOT = 24.0, 44.0, 24.0     # 分组内边距（顶部留标题位）
GROUP_GAP, FIELD_GAP = 80.0, 200.0
MAX_COLS = 6
MAX_ROW_W = 2600.0                             # 二级分组换行的行宽上限


def layout_path(vault: Path, name: str = "layout") -> Path:
    """全局图是 `.knowrary/layout.json`；项目画布各自一份 `.knowrary/layouts/<项目>.json`。
    与 `server/paths.py` 的同名函数保持一致——两边算出不同路径是最难查的那类 bug。"""
    root = vault / ".knowrary"
    return root / "layout.json" if name == "layout" else root / "layouts" / f"{name}.json"


def empty_layout() -> dict:
    return {"schema_version": LAYOUT_SCHEMA_VERSION, "revision": 0, "updated_at": None,
            "viewport": {"zoom": 0.8, "cx": 0.0, "cy": 0.0},
            "groups": {}, "nodes": {}, "refs": [], "notes": [], "images": [], "edges": {}}


def _off_canvas(node: dict) -> bool:
    """这个节点不该出现在全局画布上。

    目前只有对比组：它有自己的一张画布（`.knowrary/layouts/<id>.json`），
    再往主图上塞一份只会让主图更难读。领域总览**不在此列**——它是一个领域的入口，
    摆在自己那个域框里是有用的。
    """
    from .parser import OFF_CANVAS_TYPES
    return node.get("type") in OFF_CANVAS_TYPES


def bucket_nodes(index: dict) -> dict[str, dict[str, list[str]]]:
    """{field: {子目录: [节点 id]}}。fields/ 与 nodes/ 根下的文件归入 "" 桶（直接挂顶层分组）。"""
    out: dict[str, dict[str, list[str]]] = {}
    for node in index["nodes"]:
        if node.get("virtual") or _off_canvas(node):
            continue
        field = node.get("field") or "(未指定)"
        parts = (node.get("path") or "").split("/")
        sub = parts[1] if len(parts) > 2 and parts[0] == "nodes" else ""
        out.setdefault(field, {}).setdefault(sub, []).append(node["id"])
    for subs in out.values():
        for ids in subs.values():
            ids.sort()
    return out


def grid_shape(count: int) -> tuple[int, int]:
    cols = max(1, min(MAX_COLS, math.ceil(math.sqrt(count))))
    return cols, math.ceil(count / cols)


def group_size(count: int) -> tuple[float, float]:
    cols, rows = grid_shape(count)
    return cols * CELL_W - 40 + 2 * PAD_X, rows * CELL_H - 40 + PAD_TOP + PAD_BOT


def place_grid(ids: Iterable[str], x0: float, y0: float, doc: dict, group: str | None) -> None:
    """把一批节点按网格放进 (x0, y0) 起点的分组内。"""
    ids = list(ids)
    cols, _ = grid_shape(len(ids))
    for i, nid in enumerate(ids):
        doc["nodes"][nid] = {"x": x0 + PAD_X + (i % cols) * CELL_W,
                             "y": y0 + PAD_TOP + (i // cols) * CELL_H,
                             "w": NODE_W, "h": NODE_H, "group": group, "state": "final"}


def _merge_same_name_sub(field: str, subs: dict[str, list[str]]) -> dict[str, list[str]]:
    """与 field 同名的子目录（如 nodes/AI-Agent 在 field AI-Agent 下）不再套一层同名分组。"""
    if field not in subs:
        return subs
    merged = {k: v for k, v in subs.items() if k != field}
    merged[""] = sorted(merged.get("", []) + subs[field])
    return merged


def _add_child_groups(field: str, subs: dict[str, list[str]], top_id: str,
                      origin: tuple[float, float], doc: dict,
                      keep_order: bool = False) -> tuple[float, float]:
    """在顶层分组里横向摆放二级分组，超过行宽换行。返回顶层分组的内容尺寸。

    `keep_order`：按传进来的次序摆（分层时次序有意义，字典序会把「AI应用」排到「体系结构」前面）。
    """
    x0, y0 = origin
    cx, cy, row_h, used_w = x0 + PAD_X, y0 + PAD_TOP, 0.0, 0.0
    for sub, ids in (subs.items() if keep_order else sorted(subs.items())):
        w, h = group_size(len(ids))
        if cx > x0 + PAD_X and cx + w > x0 + MAX_ROW_W:
            cx, cy, row_h = x0 + PAD_X, cy + row_h + GROUP_GAP, 0.0
        gid = f"g-{field}--{sub}" if sub else top_id
        if sub:
            doc["groups"][gid] = {"name": sub, "x": cx, "y": cy, "w": w, "h": h, "parent": top_id,
                                  "collapsed": False, "pinned": None, "color": None}
        place_grid(ids, cx, cy, doc, gid)
        cx, row_h = cx + w + GROUP_GAP, max(row_h, h)
        used_w = max(used_w, cx - x0 - GROUP_GAP)
    return used_w + PAD_X, (cy + row_h) - y0 + PAD_BOT


def bucket_by_layer(index: dict) -> dict[str, dict[str, list[str]]]:
    """{field: {层: [节点 id]}}。二级分组按**抽象层**而不是子目录。

    为什么值得单独一种铺法：目录是文件放哪儿，层是它在栈的哪一层。
    这个 vault 的目录（01-理论基础 … 07-高级语言）本来就是层，
    但新节点未必按这个目录放，而 `layer` 是显式的。
    """
    from .parser import LAYERS, UNLAYERED
    out: dict[str, dict[str, list[str]]] = {}
    for node in index["nodes"]:
        if node.get("virtual") or _off_canvas(node):
            continue
        field = node.get("field") or "(未指定)"
        out.setdefault(field, {}).setdefault(node.get("layer") or UNLAYERED, []).append(node["id"])
    order = {name: i for i, name in enumerate((*LAYERS, UNLAYERED))}
    for subs in out.values():
        for ids in subs.values():
            ids.sort()
    # 按层的固定次序返回（dict 保序），底层在前——画出来就是自下而上的栈
    return {f: {k: subs[k] for k in sorted(subs, key=lambda k: order.get(k, 99))}
            for f, subs in out.items()}


def build_initial_layout(index: dict, by: str = "dir") -> dict:
    """按 field 两级分组生成初始布局，顶层分组自上而下排列。

    `by="dir"`：二级分组 = `nodes/` 子目录（默认，老行为）。
    `by="layer"`：二级分组 = 抽象层，按 LAYERS 的固定次序。
    """
    buckets = bucket_by_layer(index) if by == "layer" else bucket_nodes(index)
    doc = empty_layout()
    y = 0.0
    for field, subs in (buckets.items() if by == "layer" else sorted(buckets.items())):
        top_id = f"g-{field}"
        subs = subs if by == "layer" else _merge_same_name_sub(field, subs)
        doc["groups"][top_id] = {"name": field, "x": 0.0, "y": y, "w": 0.0, "h": 0.0, "parent": None,
                                 "collapsed": False, "pinned": None, "color": "#eef2f8"}
        w, h = _add_child_groups(field, subs, top_id, (0.0, y), doc, keep_order=(by == "layer"))
        doc["groups"][top_id]["w"], doc["groups"][top_id]["h"] = w, h
        y += h + FIELD_GAP
    return doc


COMPARE_COLS = 6                               # 对比画布一行摆几个成员，多了就换行


def build_compare_layout(member_ids: list[str], index: dict) -> dict:
    """对比画布的初始布局：成员按 md 里的书写顺序铺成网格，**不画分组框**。

    顺序和下面那张表的行序是同一个（都来自对比组 md 里 `- 包含::` 的先后），
    这样"表上第三行"和"图上第三个"是同一个东西——两边对不上的话，联动高亮就只是个特效。

    还没建出来的成员画成幽灵占位（`state: "ghost"`），和项目画布同一套：它不进全局
    layout、不进 vault，点一下去建。摆在原位而不是角落——对比表里它本来就该占一行，
    "这个还没写"是这张表的一部分信息。
    """
    real = {n["id"] for n in index["nodes"] if not n.get("virtual")}
    nodes: dict[str, dict] = {}
    for i, nid in enumerate(member_ids):
        col, row = i % COMPARE_COLS, i // COMPARE_COLS
        nodes[nid] = _box(80.0 + col * CELL_W, 80.0 + row * CELL_H,
                          "final" if nid in real else "ghost")
    doc = empty_layout()
    doc["nodes"] = nodes
    return doc


STAGE_MAX_ROWS = 6                             # 一个阶段一列，超过这么多点就在旁边再起一列
LIST_GAP = 120.0                               # 两份清单之间、已建区与待学区之间留的空
GHOST_COLS = 4                                 # 右下角待学区最多铺几列


def build_project_layout(project: dict, index: dict) -> dict:
    """项目画布的初始布局：**不画任何分组框**。已建的点按阶段从左到右成列，
    还没建的点画成幽灵占位，**集中停在右下角**当"待学区"。

    和全局图的初始布局是两套铺法，因为要回答的问题不同：全局图回答"这个领域里有什么"，
    项目画布回答"我这个项目还差哪几块"。

    2026-09-18 起去掉了"一份清单一个框"的父框：项目里各技术之间的关系才是重点，
    框把所有点圈在一起，既不带信息又挡着人在点之间自由摆放。阶段这层信息保留成
    "列的先后"——左边是先学的、右边是后学的——但只是初始位置，不是约束。
    要框的话在画布上自己右键建，那是人对知识的概括，不该由清单结构代劳。

    幽灵不混进阶段列里而是停在角落：一眼看清"还有哪些没学"，建出来之后再从角落拖到该去的位置。
    幽灵占位（`state: "ghost"`）**只活在项目画布**：不进全局 layout，也不进 vault
    （沿用"推测出来的东西不进真值源"）。节点真建出来之后，它就在原地变成普通节点。
    """
    real = {n["id"] for n in index["nodes"] if not n.get("virtual")}
    built, ghosts = _split_points(project, real)
    nodes: dict[str, dict] = {}
    x, rows = 80.0, 0
    for stage_ids in built:
        for i, nid in enumerate(stage_ids):
            col, row = divmod(i, STAGE_MAX_ROWS)
            nodes[nid] = _box(x + col * CELL_W, 80.0 + row * CELL_H, "final")
        rows = max(rows, min(len(stage_ids), STAGE_MAX_ROWS))
        x += (-(-len(stage_ids) // STAGE_MAX_ROWS)) * CELL_W + GROUP_GAP
    # 待学区：已建区右侧、与最后一行对齐往下长；一个都没建就从左上角铺起
    gx = x - GROUP_GAP + LIST_GAP if built else 80.0
    gy = 80.0 + max(rows - 1, 0) * CELL_H
    cols = max(1, min(GHOST_COLS, math.ceil(math.sqrt(len(ghosts))))) if ghosts else 1
    for i, nid in enumerate(ghosts):
        nodes[nid] = _box(gx + (i % cols) * CELL_W, gy + (i // cols) * CELL_H, "ghost")
    return stamp({**empty_layout(), "nodes": nodes})


def _split_points(project: dict, real: set[str]) -> tuple[list[list[str]], list[str]]:
    """按清单→阶段的顺序去重：已建的按阶段分桶（空桶不要），没建的攒成一列。"""
    built: list[list[str]] = []
    ghosts: list[str] = []
    seen: set[str] = set()
    for ls in project.get("lists") or []:
        for stage in ls.get("stages") or []:
            bucket = []
            for pt in stage.get("points") or []:
                nid = pt.get("id")
                if not nid or nid in seen:
                    continue
                seen.add(nid)
                (bucket if nid in real else ghosts).append(nid)
            if bucket:
                built.append(bucket)
    return built, ghosts


def _box(x: float, y: float, state: str) -> dict:
    return {"x": x, "y": y, "w": NODE_W, "h": NODE_H, "group": None, "state": state}


def find_orphans(doc: dict, index: dict, vault: Path) -> list[dict]:
    """layout 里引用不到的记录：只报告，不删除（md 删了节点也要留住用户的位置数据）。"""
    real = {n["id"] for n in index["nodes"] if not n.get("virtual")}
    edges = {e["id"] for e in index["edges"]}
    out: list[dict] = []
    for nid in sorted(set(doc.get("nodes", {})) - real):
        # 幽灵占位本来就不在索引里——那正是它的含义（"计划里有、还没建"），不是孤立记录
        if (doc["nodes"][nid] or {}).get("state") == "ghost":
            continue
        out.append({"kind": "node", "id": nid, "reason": "索引里没有这个节点（md 可能已删除或改名）"})
    for ref in doc.get("refs", []):
        if ref.get("target") not in real:
            out.append({"kind": "ref", "id": ref.get("id"), "reason": f"引用卡指向的节点 `{ref.get('target')}` 不存在"})
    for key in sorted(set(doc.get("edges", {})) - edges):
        out.append({"kind": "edge", "id": key, "reason": "这条边已不在索引中，手工拐点悬空"})
    for image in doc.get("images", []):
        if not (vault / str(image.get("file", ""))).exists():
            out.append({"kind": "image", "id": image.get("id"), "reason": f"图片文件不存在：{image.get('file')}"})
    groups = set(doc.get("groups", {}))
    for gid, group in doc.get("groups", {}).items():
        if group.get("parent") and group["parent"] not in groups:
            out.append({"kind": "group", "id": gid, "reason": f"父分组 `{group['parent']}` 不存在"})
    for nid, node in doc.get("nodes", {}).items():
        if node.get("group") and node["group"] not in groups:
            out.append({"kind": "node_group", "id": nid, "reason": f"所属分组 `{node['group']}` 不存在"})
    return out


def stamp(doc: dict) -> dict:
    """revision +1 并更新时间戳（写盘前调用）。"""
    doc["schema_version"] = LAYOUT_SCHEMA_VERSION
    doc["revision"] = int(doc.get("revision", 0)) + 1
    doc["updated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return doc
