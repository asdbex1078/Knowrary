"""节点解析与 frontmatter 校验：md 文件 → Node（frontmatter + 正文 + 关系）。

正文与关系区块严格分离：只有 `## 关系` 会被关系解析器读取，其余原文逐字保留，
写回时原样吐回去（见《Markdown 文档规范》4）。
"""
from __future__ import annotations

import datetime as dt
import re

from dataclasses import dataclass, field
from pathlib import Path

import hashlib

from .diagnostics import Diagnostics
from .mdio import RE_ID_OK, RE_NEXT_H2, RE_REL_HEADER, read, split_frontmatter, walk_md
from .relations import Edge, parse_relations

def digest_of(text: str) -> str:
    """文件内容指纹：写回前比对它，Obsidian 改过就拒绝覆盖。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


STATUS_VALUES = ("active", "deprecated", "disputed", "stub")
LAYOUT_KEYS = ("x", "y", "w", "h", "group", "collapsed", "pinned")
REQUIRED_FIELDS = ("name", "field", "desc")

# 抽象层：历史视图按它分泳道，**从下往上**排（底层在下，应用在上）。
#
# 为什么不复用 `field` 或 layout 分组：它们是**主题**维度（冯诺依曼体系 / 编译原理 / AI），
# 而这是**层次**维度（硬件 / 汇编 / 语言 / 应用）。一个节点只能落一个分组，
# 两个维度抢同一个字段的话，结构图和历史图必有一个要将就。所以正交地各存各的。
#
# 可选字段：不填就落「未分层」，不影响任何既有功能。
LAYERS = ("理论", "硬件", "体系结构", "汇编接口", "系统软件", "高级语言", "AI应用")
UNLAYERED = "未分层"

# 聚合文档：它们是**视角**，不是知识点——领域总览是一张目录，对比组是一张表。
# 和知识点共用一套解析/索引/改名是对的（省掉一整套平行机制），但凡是"催你把知识点补完整"
# 的那些地方都必须跳过它们，否则每建一个对比组，Inbox 多一条、孤点多一条、缺 year 多一条，
# 而这三条对它们都没有意义。实盘上 `fields/计算机系统.md`（领域总览、degree 0）
# 今天就挂在孤点列表里——那正是这个标记要修掉的噪音。
AGGREGATE_TYPES = ("对比组", "领域总览", "流派", "领域线")

# 不上全局画布的那一类。**比 AGGREGATE_TYPES 窄**：领域总览是一个领域的入口，
# 摆在它自己那个域框里是有用的（实盘上就摆着）；对比组有自己的一张画布
# （`.knowrary/layouts/<id>.json`），再往主图上塞一份只会让主图更难读。
# 流派下画布的理由不同：它是**历史视图**里的一条时间带（横跨 start_year～end_year），
# 在全局图上它就是一个度数很高又说不出位置的大点，只会把主图搅浑。
OFF_CANVAS_TYPES = ("对比组", "流派", "领域线")

# **不该被考、也不该进复习队列的那一类。比 AGGREGATE_TYPES 窄。**
#
# 这两件事原来共用 `aggregate` 一个标记，而它其实管着两件不同的事：
# 「不催你把它补完整」（Inbox / 孤点 / 缺 year / 重复候选）和「不考它」。
# 对比组和领域总览两条都该跳过——考一张目录或一张对比表没有意义。
# 但**流派有正文**（实盘上「符号主义」2228 字、「连接主义」1584 字，都带「我的理解」），
# 它只是不该当知识点摆在画布上，不是不该背。塞进同一个标记就等于把这几千字
# 从复习闭环里悄悄摘掉——而"悄悄"正是最糟的那部分：没有任何地方会告诉你。
NOT_REVIEWABLE_TYPES = ("对比组", "领域总览")


def is_aggregate(fm: dict) -> bool:
    """这份 frontmatter 描述的是聚合文档（对比组 / 领域总览 / 流派）而不是知识点。"""
    return (fm or {}).get("type") in AGGREGATE_TYPES


def is_reviewable(node: dict) -> bool:
    """这个节点该不该进复习队列 / 出题范围。看的是 type，不是 `aggregate`（见上）。"""
    return (node or {}).get("type") not in NOT_REVIEWABLE_TYPES


# 参数量：`params: 175B` 这样写。**只认一个数量级后缀**，不做单位大全——
# 这个字段是拿来画图比大小的，不是拿来存规格表的。
# 解析不出来只警告不报错：它是可选字段，写错了不该让整份 index 变成"有错误"。
PARAMS_UNITS = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12,
                "万": 1e4, "亿": 1e8, "千亿": 1e11, "万亿": 1e12}
RE_PARAMS = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(k|m|b|t|万亿|千亿|万|亿)?\s*$", re.I)


def parse_params(value) -> float | None:
    """`175B` / `7.5b` / `340M` / `1.3万亿` / 纯数字 → 参数个数。看不懂返回 None。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        out = float(value)
    else:
        m = RE_PARAMS.match(str(value or ""))
        if not m:
            return None
        out = float(m.group(1)) * PARAMS_UNITS.get((m.group(2) or "").lower(), 1.0)
    # 合理区间：一千到一千万亿。超出的基本是敲错了量级（把 1750 亿写成 1.75e17），
    # 而一个"参数量 12"的模型不存在——与其画进图里误导人，不如当没填
    return out if 1e3 <= out <= 1e15 else None


@dataclass
class Node:
    id: str
    path: Path
    fm: dict
    body: str          # 不含 frontmatter、不含 ## 关系 段
    edges: list[Edge] = field(default_factory=list)
    rel_tail: str = "" # 关系段之后的残余文本（一般为空）
    raw: str = ""      # 文件原文，写回时用来比对"外部有没有改过"
    digest: str = ""   # 原文的 sha1 前 16 位

    @property
    def is_stub(self) -> bool:
        return self.fm.get("status") == "stub"


def load_node(vault: Path, p: Path) -> tuple[Node, Diagnostics]:
    """读单个 md。YAML 非法时按无 frontmatter 处理并给出 error，不丢节点。"""
    diags = Diagnostics()
    rel = p.relative_to(vault).as_posix()
    text = read(p)
    try:
        fm, rest = split_frontmatter(text)
    except ValueError as exc:
        diags.error("bad_yaml", f"frontmatter YAML 非法：{exc}", file=rel)
        fm, rest = {}, text
    parts = RE_REL_HEADER.split(rest, maxsplit=1)
    body = parts[0].rstrip() + "\n"
    section, tail = _cut_relation_section(parts[1]) if len(parts) > 1 else ("", "")
    node = Node(id=str(fm.get("id") or p.stem), path=p, fm=fm, body=body, rel_tail=tail,
                raw=text, digest=digest_of(text))
    edges, bad = parse_relations(section, node.id)
    node.edges = edges
    for b in bad:
        diags.error("bad_relation_line", f"无法解析的关系行 `{b}`", file=rel, node=node.id)
    return node, diags


def _cut_relation_section(rest: str) -> tuple[str, str]:
    """把 `## 关系` 之后的文本切成 (关系段, 后续原文)。

    规范 4 允许 `## 关系` 后面继续写 `## 参考资料`、`## 待办`；这些章节既不是关系，
    写回时也必须逐字保留，所以在这里就切开，不能整段丢给关系解析器。
    """
    m = RE_NEXT_H2.search(rest)
    return (rest, "") if m is None else (rest[:m.start()], rest[m.start():])


def load_vault(vault: Path) -> tuple[dict[str, Node], Diagnostics]:
    """扫约定目录并解析全部节点。重复 id 保留后者，同时报 error。"""
    nodes: dict[str, Node] = {}
    diags = Diagnostics()
    for p in walk_md(vault):
        rel = p.relative_to(vault).as_posix()
        if not read(p).strip():
            diags.warn("empty_file", "文件为空，已跳过", file=rel)
            continue
        node, node_diags = load_node(vault, p)
        diags.extend(node_diags)
        if node.id in nodes:
            other = nodes[node.id].path.relative_to(vault).as_posix()
            diags.error("duplicate_id", f"重复 id `{node.id}`，与 {other} 冲突", file=rel, node=node.id)
        nodes[node.id] = node
    return nodes, diags


def validate_frontmatter(vault: Path, node: Node, diags: Diagnostics) -> None:
    """按《Markdown 文档规范》3 与校验清单检查单节点的 frontmatter。"""
    rel = node.path.relative_to(vault).as_posix()
    loc = {"file": rel, "node": node.id}
    for k in REQUIRED_FIELDS:
        if not node.fm.get(k):
            diags.error("missing_field", f"frontmatter 缺少 `{k}`", **loc)
    if not RE_ID_OK.match(node.id):
        diags.error("bad_id", f"id `{node.id}` 含非法字符", **loc)
    status = node.fm.get("status")
    if status and status not in STATUS_VALUES:
        diags.error("bad_status", f"status `{status}` 不合法（可选 {'/'.join(STATUS_VALUES)}）", **loc)
    for k in LAYOUT_KEYS:
        if k in node.fm:
            diags.error("layout_in_frontmatter", f"frontmatter 混入布局字段 `{k}`，布局只存 layout.json", **loc)
    layer = node.fm.get("layer")
    if layer and layer not in LAYERS:
        # 只警告不报错：`layer` 是给历史视图分泳道用的可选提示，写错了那个节点自己单开一条道，
        # 不该因此让整份 index 变成"有错误"。但拼错必须看得见，否则会静静多出一条泳道。
        diags.warn("unknown_layer", f"layer `{layer}` 不在已知的抽象层里"
                                    f"（{' / '.join(LAYERS)}）", **loc)
    _validate_years(node, diags, loc)
    if node.fm.get("params") is not None and parse_params(node.fm["params"]) is None:
        diags.warn("bad_params", f"params `{node.fm['params']}` 看不懂"
                                 f"（写成 175B / 340M / 1.3万亿 这样）", **loc)


def _validate_years(node: Node, diags: Diagnostics, loc: dict) -> None:
    for k in ("year", "start_year", "end_year"):
        v = node.fm.get(k)
        if v is None:
            continue
        if not isinstance(v, int) or isinstance(v, bool) or not 1000 <= v <= 2999:
            diags.error("bad_year", f"{k} `{v}` 不是四位整数", **loc)
    sy, ey = node.fm.get("start_year"), node.fm.get("end_year")
    if isinstance(sy, int) and isinstance(ey, int) and ey <= sy:
        diags.error("year_range_inverted", f"时间区间反转 {sy}..{ey}", **loc)
    # 未来的年份：真要记规划中的标准也有可能，所以只警告；但绝大多数是敲错了一位
    nxt = dt.date.today().year + 1
    for k in ("year", "start_year"):
        v = node.fm.get(k)
        if isinstance(v, int) and not isinstance(v, bool) and v > nxt:
            diags.warn("year_in_future", f"{k} `{v}` 在未来（今年是 {nxt - 1}），是不是敲错了", **loc)
