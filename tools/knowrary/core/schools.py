"""流派 / 领域线取数：索引 → 历史视图里那几条泳道。

**两种「线」共用同一套机制，但必须分成两档筛选**：

| | 流派 | 领域线 |
|---|---|---|
| 是什么 | 方法论主张（符号主义 vs 连接主义、CISC vs RISC） | 任务领域（NLP / CV / ASR） |
| 彼此关系 | **竞争**，互斥的世界观 | **并列**，不互斥 |
| 「重叠」的含义 | 这个技术**同时体现两种主张**——罕见，而且有意思 | 这个技术**两个领域都用**——常见，正常 |

数据形状一模一样（一组 `包含` 边 + `start_year` / `end_year`），所以代码共用；
但叫同一个名字会把「重叠」的含义混掉：前者重叠是一件值得盯着看的事，后者是日常。
`AlexNet` 在主张这一维属于连接主义、在领域这一维属于 CV，**两维正交**，
所以它们是两档泳道，不是一档里的两种颜色。


**流派是"一段时间范围内一批技术的统称"，和对比组是同一个形状**（`fields/` 下的聚合
文档、成员是 `包含` 边、不上全局画布），只有两点不同：

1. 附加数据是 `start_year` / `end_year`，不是 `dimensions`；
2. 它只在**历史视图**里有形态——一条横跨年份区间的带子。所以它**没有独立入口**：
   带子本身就是目录，看得见就能点。对比组要全局入口是因为它的产物（表 + 自己的画布）
   没有别的地方能进去，流派的家已经存在了。

**重叠是这套东西本来就要表达的事**，不是要处理的例外：`现代Intel微架构` 同时属于
CISC 和 RISC（前端 CISC 指令集、后端拆成类似 RISC 的 μops），而 layout 的分组框
是一棵树，一个节点只能有一个家——表达不了。`包含` 是结构族的边，没有唯一性约束。

这一层不读盘：成员顺序按**年份**排（流派本身就是时间概念），不像对比表那样要回 md
拿书写顺序。所以它是一个纯函数，只吃索引。
"""
from __future__ import annotations

from pathlib import Path

from .compare import MEMBER_RELATION
from .parser import load_node

SCHOOL_TYPE = "流派"
DOMAIN_TYPE = "领域线"
LINE_TYPES = (SCHOOL_TYPE, DOMAIN_TYPE)


def _year_key(meta: dict) -> tuple[int, str]:
    """按年份排，没填 year 的排到最后（而不是当成 0 排到最前）。"""
    year = meta.get("year")
    return (year, "") if isinstance(year, int) else (10 ** 6, meta.get("id") or "")


def _span(node: dict) -> tuple[int | None, int | None]:
    start = node.get("start_year")
    end = node.get("end_year")
    return (start if isinstance(start, int) else None,
            end if isinstance(end, int) else None)


def _curve(start: int | None, metas: list[dict]) -> list[dict]:
    """累计阶梯：**每冒出一个新成员就往上一级，不涨的那段就是停摆。**

    为什么是累计而不是"每年新增"：知识不会消失，只是不再增长。累计曲线单调不降，
    平台期读起来就是"这些年没出新东西"；而脉冲图里单点年份会画成一根尖刺，
    大片 0 值反而读不出形状。连接主义 1969–1986 那 17 年，在这里就是一段平线。

    **起点画在 `start_year`、值为 0**，哪怕第一个成员晚好几年——那段"从 0 起步"
    的平段也是信息（流派先有名字，技术后来才出）。

    同一年有好几个成员就合成一级台阶（跳 k 级），不画成几个挨着的点。
    没填 year 的成员进不了曲线（放不到 X 轴上），单独由 `undated` 列出来——
    **曲线很平也可能只是年份没填**，这两件事得能分开。
    """
    dated = sorted(m["year"] for m in metas if isinstance(m.get("year"), int))
    if start is None and not dated:
        return []
    points = [{"year": start if start is not None else dated[0], "n": 0}]
    n = 0
    for year in dated:
        n += 1
        if points[-1]["year"] == year:
            points[-1]["n"] = n          # 同年合成一级，不画成挨着的两个点
        else:
            points.append({"year": year, "n": n})
    return points


def schools(index: dict, kind: str | None = None) -> list[dict]:
    """所有流派，**按 start_year 从早到晚**——"出现早的放前面"就是这一行。

    `end` 为 None 表示还在延续（连接主义就是），单独给一个 `open` 标记：
    前端要能分清"画到时间轴右端"和"数据缺了"，这两件事看起来一样但意思相反。

    `outliers` 是成员里年份落在区间外的那些。**不报错**——追溯到更早的前身是合理的
    （阈值逻辑单元 1943 之于连接主义），但它同样是 `start_year` 填错时唯一看得见的
    症状，所以列出来让人自己判。
    """
    by_id = {n["id"]: n for n in index["nodes"]}
    members: dict[str, list[str]] = {}
    for e in index["edges"]:
        if e["type"] != MEMBER_RELATION:
            continue
        if by_id.get(e["source"], {}).get("type") in LINE_TYPES:
            members.setdefault(e["source"], []).append(e["target"])

    out = []
    want = (kind,) if kind else LINE_TYPES
    for node in index["nodes"]:
        if node.get("type") not in want or node.get("virtual"):
            continue
        start, end = _span(node)
        ids = members.get(node["id"], [])
        metas = [{**by_id[i], "id": i} for i in ids if i in by_id]
        metas.sort(key=_year_key)
        outliers = [{"id": m["id"], "year": m["year"]} for m in metas
                    if isinstance(m.get("year"), int)
                    and (start is not None and m["year"] < start
                         or end is not None and m["year"] > end)]
        curve = _curve(start, metas)
        out.append({
            "id": node["id"], "name": node.get("name") or node["id"],
            "kind": node.get("type"),
            "field": node.get("field") or "", "desc": node.get("desc") or "",
            "color": node.get("color") or "",
            "start": start, "end": end, "open": end is None,
            "members": [m["id"] for m in metas],
            "outliers": outliers,
            "curve": curve, "peak": curve[-1]["n"] if curve else 0,
            "undated": [m["id"] for m in metas if not isinstance(m.get("year"), int)],
        })
    # 没填 start 的排最后：check 已经把它报成 error 了，这里只保证不把 None 塞进比较
    out.sort(key=lambda s: (s["start"] is None, s["start"] or 0, s["name"]))
    return out


def school_of(index: dict, node_id: str) -> list[str]:
    """一个节点属于哪几个流派。**返回列表不是单值**——重叠是常态，不是例外。"""
    return [s["id"] for s in schools(index) if node_id in s["members"]]


def line_homes(vault: Path, index: dict, kind: str | None = None) -> dict[str, str]:
    """跨多条线的点，**它的「主道」是哪条**（真身画在那儿，边也只连真身）。

    **这推不出来，只能声明。** `Transformer` 属于 NLP / CV / ASR 三条线，
    但它出生在 NLP —— 而这是个历史事实，从成员年份、线的起始年、排第几
    都推不出正确答案（ASR 这条线 1952 年就起步，按「线开得早」会判给它；
    ASR 成员少，按「在这条线里排第几」也会判给它）。

    判错的后果是图上读反：`ViT 源自 Transformer`、`Whisper 源自 Transformer`
    这些箭头会汇聚到一条它根本没出生在那儿的道上，看起来就是
    **「语音这条线发明了 Transformer」**。

    所以口径和对比组的行序一致：**按节点 md 里 `- 属于::` 的书写顺序，第一条就是主道**。
    想换就去 md 里挪那两行。只读跨线的那几个节点，不跨线的没有歧义。
    """
    lines = {s["id"]: s for s in schools(index, kind)}
    by_node: dict[str, list[str]] = {}
    for lid, line in lines.items():
        for nid in line["members"]:
            by_node.setdefault(nid, []).append(lid)
    paths = {n["id"]: n.get("path") for n in index["nodes"]}
    out: dict[str, str] = {}
    for nid, ids in by_node.items():
        if len(ids) < 2:
            continue                      # 只属于一条线：没有歧义，不用读盘
        path = paths.get(nid)
        declared = []
        if path and (vault / path).exists():
            node, _ = load_node(vault, vault / path)
            # `属于` 归一成 `包含`，但 md 里写的是 `属于`，所以两个都认
            declared = [e.target for e in node.edges
                        if e.type in ("属于", MEMBER_RELATION) and e.target in lines]
        # 没声明就落到**起始年最早的那条线**。这只是个稳定的默认值，不是判断：
        # 成员写在线那侧（`- 包含:: [[点]]`）时，点自己的 md 里没有书写顺序可查。
        # 想指定就在点的 md 里写 `- 属于:: [[那条线]]`，写在最前面的那条就是主道。
        fallback = sorted(ids, key=lambda i: (lines[i]["start"] is None,
                                              lines[i]["start"] or 0, i))[0]
        out[nid] = next((d for d in declared if d in ids), fallback)
    return out
