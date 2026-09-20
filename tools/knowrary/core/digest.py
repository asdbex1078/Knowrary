"""Digest：图谱的"待办清单"——草稿、跨分组桥、连边建议、重复候选、stub、待复习。

和 Inbox 的分工：Inbox 管"新进来的东西往哪放"，Digest 管"图谱里有哪些欠账"。
全部只读，不改任何文件；每一项都给出可定位的节点 id，前端点一下就能跳过去。
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from .compare import gaps as compare_gaps
from .placement import by_field_and_layer, inbox_ids
from .issues import summary as issues_summary
from .review import due_nodes, load_log

DRAFT_STALE_DAYS = 7        # 草稿放这么多天还没定稿就提醒
NAME_SIMILAR = 0.72         # 名字相似度阈值
SHARED_NEIGHBOURS = 3       # 共同邻居达到这个数就提示连边
AFFIX_REMAIN = 3            # 掐掉公共前后缀后两边各自剩的字数上限，再长就不像"同族兄弟"
# 公共词缀至少要这么长才算证据。单个「器」「段」「存」是中文的类别后缀，不是共同的意思：
# 按它算，`寄存器` / `控制器`、`数据段` / `数论`、`内存` / `高速缓存` 全成了"同族"。
# 前后缀**合计**计数，acronym 才不会被误伤（`MHA` / `MLA` 是 M…A，两头各一个字）。
MIN_AFFIX = 2
# 加了等于没加的尾巴：`MHA` / `MHA机制` 是同一个东西被建了两遍，而 `内存` / `堆内存` 不是。
# 两者字面上都是包含关系，差别只在**多出来的那一截有没有内容**。
EMPTY_TAILS = ("机制", "技术", "原理", "方法", "模型", "算法", "架构", "体系", "理论", "概念", "问题")
MAX_ITEMS = 20


def _age_days(value, today: dt.date) -> int | None:
    try:
        return (today - dt.date.fromisoformat(str(value)[:10])).days
    except (TypeError, ValueError):
        return None


def drafts(layout: dict, today: dt.date) -> list[dict]:
    out = []
    for nid, n in sorted(layout.get("nodes", {}).items()):
        if n.get("state") != "draft":
            continue
        age = _age_days(n.get("placedAt"), today)
        out.append({"id": nid, "group": n.get("group"), "placedAt": n.get("placedAt"),
                    "days": age, "stale": age is not None and age >= DRAFT_STALE_DAYS})
    return out


def group_labels(groups: dict) -> dict[str, str]:
    """分组 id → 显示名，**重名的带上父级消歧**。

    布局里 `硬件` 有两个（计算机系统下一个、AI 下一个），体系结构 / 系统软件 / AI应用
    同理。只取 `name` 的话，最有价值的那条桥会显示成"硬件 → 硬件"——一句看不出
    在说什么的话，等于把这条线藏了。重名的才加前缀：不重名的加了反而啰嗦。
    """
    dup = {n for n, c in Counter(g.get("name") for g in groups.values()).items() if c > 1}
    out = {}
    for gid, g in groups.items():
        name = g.get("name") or gid
        parent = groups.get(g.get("parent") or "", {}).get("name")
        out[gid] = f"{parent}/{name}" if name in dup and parent else name
    return out


def bridges(index: dict, layout: dict) -> list[dict]:
    """跨分组的边按"组对"聚合：连接两个领域的关系往往最有价值。"""
    groups = layout.get("groups", {})
    labels = group_labels(groups)
    place = layout.get("nodes", {})
    pairs: dict[tuple[str, str], list[str]] = defaultdict(list)
    for e in index["edges"]:
        a = place.get(e["source"], {}).get("group")
        b = place.get(e["target"], {}).get("group")
        if not a or not b or a == b:
            continue
        pairs[(a, b)].append(e["id"])
    out = [{"from": a, "to": b, "from_name": labels.get(a, a), "to_name": labels.get(b, b),
            "count": len(ids), "edges": ids[:5]}
           for (a, b), ids in pairs.items()]
    return sorted(out, key=lambda d: -d["count"])[:MAX_ITEMS]


def _common_affix(a: str, b: str) -> tuple[int, int]:
    """公共前缀长度、公共后缀长度（后缀不与前缀重叠）。"""
    head = 0
    while head < len(a) and head < len(b) and a[head] == b[head]:
        head += 1
    tail = 0
    while tail < len(a) - head and tail < len(b) - head and a[-1 - tail] == b[-1 - tail]:
        tail += 1
    return head, tail


def kinship(a_id: str, name_a: str, b_id: str, name_b: str) -> dict | None:
    """两个名字像不像"同一族"；像的话，该连的是哪条边。

    中文复合词天生共享中心语：`内存` / `堆内存` / `栈内存` 的字面重合度 0.8，稳稳
    过重复候选的线。但 SequenceMatcher 只会说"这俩像"，说不出**像在哪**——而像在
    哪正是答案：

    · 一个是另一个的严格子串 ⇒ 上下位，短的那个是上位（`包含`）；
    · 掐掉公共前后缀后两边都还剩一点 ⇒ 同级兄弟（`对比`）。

    两种都**不是重复，是图上缺的那条边**。给的类型只是默认值，连边前还要过一遍
    关系对话框——方向和类型由人定，这里只负责把"这俩有关系"摆到眼前。

    唯一的例外是多出来的那一截**加了等于没加**（`MHA` / `MHA机制`）：那是同一个
    东西被建了两遍，返回 None 交回给重复候选。
    """
    if name_a == name_b:
        return None
    for short, long_, sid, lid in ((name_a, name_b, a_id, b_id), (name_b, name_a, b_id, a_id)):
        if short not in long_:
            continue
        if long_.replace(short, "", 1).strip() in EMPTY_TAILS:
            return None                       # 同一个东西的两种写法，是重复不是上下位
        return {"source": sid, "target": lid, "relation": "包含",
                "reason": f"「{long_}」的名字里含着「{short}」，多半是它的一种"}
    head, tail = _common_affix(name_a, name_b)
    if head + tail < MIN_AFFIX:
        return None
    rest_a, rest_b = name_a[head:len(name_a) - tail], name_b[head:len(name_b) - tail]
    if not rest_a or not rest_b or max(len(rest_a), len(rest_b)) > AFFIX_REMAIN:
        return None
    shared = "…".join(x for x in (name_a[:head], name_a[len(name_a) - tail:] if tail else "") if x)
    return {"source": a_id, "target": b_id, "relation": "对比",
            "reason": f"同族兄弟：都带「{shared}」，差在「{rest_a}」/「{rest_b}」"}


def _pairs(index: dict) -> tuple[list[dict], list[dict]]:
    """名字相近的节点对扫一遍，分成两堆：**该连边的**和**真像重复的**。

    先排掉已经连过边的对：关系已经在图上表达过了，再提醒一次纯属噪音。
    """
    # 聚合文档排掉：`分词技术对比` 和 `分词` 字面重合度很高，但它俩既不是重复、
    # 也不该连边——那是"表和它的表头"，提一次就是一次纯噪音
    nodes = [n for n in index["nodes"] if not n.get("virtual") and not n.get("aggregate")]
    neighbours: dict[str, set[str]] = defaultdict(set)
    linked: set[frozenset] = set()
    for e in index["edges"]:
        neighbours[e["source"]].add(e["target"])
        neighbours[e["target"]].add(e["source"])
        linked.add(frozenset((e["source"], e["target"])))
    dups: list[dict] = []
    hints: list[dict] = []
    for i, a in enumerate(nodes):
        for b in nodes[i + 1:]:
            if frozenset((a["id"], b["id"])) in linked:
                continue
            name_a = a.get("name") or a["id"]
            name_b = b.get("name") or b["id"]
            ratio = round(SequenceMatcher(None, name_a, name_b).ratio(), 2)
            kin = kinship(a["id"], name_a, b["id"], name_b)
            if kin:
                # 孤立节点优先：连边建议最大的用处就是把 degree 0 的点接回图里，
                # 两个都孤立的那条最该先连。
                lonely = int(not a.get("degree")) + int(not b.get("degree"))
                hints.append({**kin, "lonely": lonely, "score": ratio})
                continue
            if ratio >= NAME_SIMILAR:
                dups.append({"a": a["id"], "b": b["id"], "score": ratio,
                             "reason": f"名字相似（{name_a} / {name_b}）"})
                continue
            # 共同邻居多**不是重复的证据**：Transformer 那一簇是个近全连通的小团，
            # 团里每一对的邻居都几乎一样（实测 Jaccard 全在 0.6 以上），按这条规则
            # 会把 MHA / MQA / GQA 两两报成重复。它证明的是"同族"，那就当缺边提。
            shared = neighbours[a["id"]] & neighbours[b["id"]]
            union = neighbours[a["id"]] | neighbours[b["id"]]
            if len(shared) >= SHARED_NEIGHBOURS:
                hints.append({"source": a["id"], "target": b["id"], "relation": "相关",
                              "lonely": 0, "score": round(len(shared) / len(union), 2),
                              "reason": f"邻居几乎一样（{len(shared)} 个共同邻居："
                                        f"{'、'.join(sorted(shared)[:4])}），同族的点之间通常该有条线"})
    dups.sort(key=lambda d: -d["score"])
    hints.sort(key=lambda d: (-d["lonely"], -d["score"]))
    return dups[:MAX_ITEMS], hints[:MAX_ITEMS]


def duplicates(index: dict) -> list[dict]:
    """重复候选：名字高度相似，或共同邻居多到不像巧合。**同族兄弟不算**（见 kinship）。"""
    return _pairs(index)[0]


def link_hints(index: dict) -> list[dict]:
    """连边建议：名字摆明了有关系、图上却没连的那些对。"""
    return _pairs(index)[1]


def lonely(index: dict) -> list[dict]:
    """一条关系都没有的已建节点。

    **这是这张图最大的一笔欠账**：整个产品（画布、最短解释链、跨分组桥、历史视图）
    都建在边上，实盘上却有六成节点度为 0。连边建议只认得出名字有线索的那些
    （`内存` / `堆内存`），`eBPF`、`乐观锁`、`存储器层次结构` 这种名字上看不出亲戚的
    一条都提不出来——那正是要问 AI 的部分，所以这里把孤点原样列全。

    聚合文档不算孤点：领域总览和对比组本来就可能一条边都没有。在这条口径之前，
    `fields/计算机系统.md`（领域总览、degree 0）一直挂在这张表的第一屏。
    """
    rows = [n for n in index["nodes"]
            if not n.get("virtual") and not n.get("stub") and n.get("path")
            and not n.get("degree") and not n.get("aggregate")]
    rows.sort(key=lambda n: (-(n.get("rank") or 0), n["id"]))
    return [{"id": n["id"], "name": n.get("name") or n["id"], "field": n.get("field") or "",
             "desc": n.get("desc") or ""} for n in rows]


def lonely_batches(index: dict) -> list[dict]:
    """按来源聚合孤点：**一整批一起进来、又一条边都没有，多半是导入那一步丢了边。**

    这条是从一次真实损失里长出来的：2026-09-10 导入的 8 个节点，方案里写了 20 条边，
    stub 也建出来了，**唯独边一条没落盘**。`lonely` 当时照常把它们列了出来，
    但混在另外 38 个"抄来的图"孤点里，看上去和那些没区别，于是躺了 10 天。

    单看一个节点是孤点，说明不了什么（可能只是还没想好连谁）；
    但**同一篇文章拆出来的 8 个节点全是孤点**，那不是"还没连"，那是"没写进去"。
    所以这里报的是批，不是点。

    `ratio == 1` 且不止一两个的那种，才是 bug 的形状；半数的那种通常是
    "抄进来的参考资料一直没盘活"，也是欠账，但性质不同——`whole` 把两者分开。
    """
    by_src: dict[str, list[dict]] = {}
    for n in index["nodes"]:
        if n.get("virtual") or n.get("stub") or not n.get("path") or n.get("aggregate"):
            continue
        src = str(n.get("source") or "").strip()
        if src:
            by_src.setdefault(src, []).append(n)
    out = []
    for src, rows in by_src.items():
        alone = [n for n in rows if not n.get("degree")]
        if len(alone) < 2:            # 一个孤点不成批
            continue
        out.append({"source": src, "total": len(rows), "lonely": len(alone),
                    "ratio": round(len(alone) / len(rows), 2),
                    "whole": len(alone) == len(rows) and len(rows) >= 3,
                    "ids": [n["id"] for n in alone][:MAX_ITEMS]})
    out.sort(key=lambda d: (-d["whole"], -d["ratio"], -d["lonely"]))
    return out


def no_year(index: dict) -> list[str]:
    """还没填 year 的已建节点。

    和 `bad_years` 分开：那个是**算得出来的矛盾**（演化边两端倒挂、年份在未来），
    这个只是**没填**。没填不是错，但它是历史视图的开关——一个节点没有 year
    就根本不出现在时间轴上，而"时间轴上少了谁"是这张图里最不容易看出来的一种缺失。

    聚合文档除外：一张对比表没有"诞生年份"，催也补不出来。
    """
    return sorted(n["id"] for n in index["nodes"]
                  if not n.get("virtual") and not n.get("stub") and n.get("path")
                  and not n.get("year") and not n.get("aggregate"))


def _top_group(gid: str | None, groups: dict) -> str | None:
    """一路往上找到顶层分组的名字。分组 id 是 `g-<field>--<layer>`，顶层那个就该等于 field。"""
    seen = 0
    while gid and seen < 12:
        g = groups.get(gid) or {}
        if not g.get("parent"):
            return g.get("name") or gid
        gid = g["parent"]
        seen += 1
    return None


def misplaced(index: dict, layout: dict) -> list[dict]:
    """`field` 和它在画布上所属的顶层域对不上的点。

    **和「年份可疑」同一类：算得出来的矛盾，不依赖任何外部知识。** 分组 id 的生成规则
    就是 `g-<field>--<layer>`（core/layout.py），所以"这个点该归哪个域"是机械可算的。

    为什么会对不上：`field` 是知识层的，`group` 是画布层的，改 md 不动画布——
    那条分界是对的（否则手工摆位会被一次改 frontmatter 冲掉），但代价是**两边可以
    悄悄走散**。实盘上就出现过：在对话里把「图灵测试」的 field 改成 AI，md 和索引都更新了，
    画布上它还待在「计算机系统/理论」里，而唯一的发现方式是肉眼看出"咦怎么没动"。

    `want` 是它该去的那条道；`want_exists` 为假表示那条道还没建（`by_field_and_layer`
    找不到同名子框时会退回领域大框）。
    """
    groups = layout.get("groups", {})
    by_id = {n["id"]: n for n in index["nodes"]}
    out = []
    for nid, place in sorted((layout.get("nodes") or {}).items()):
        node = by_id.get(nid)
        gid = place.get("group")
        if not node or not gid or node.get("virtual") or not node.get("field"):
            continue
        top = _top_group(gid, groups)
        if not top or top == node["field"]:
            continue
        want = by_field_and_layer(node, layout)
        # 图上压根没有这个 field 的域 = 这张布局不是按 field 组织的（`layout init --by dir`
        # 就是按目录建组的）。那时候"摆错了"无从谈起，报出来只会是满屏假阳性。
        if want is None:
            continue
        want_name = (groups.get(want, {}).get("name") or want) if want else None
        # want 退回了领域大框 = 该去的那条泳道还不存在，挪过去之前得先建一条
        want_exists = bool(want and groups.get(want, {}).get("parent"))
        out.append({"id": nid, "field": node["field"], "layer": node.get("layer") or "",
                    "group": gid, "group_name": _top_group(gid, groups),
                    "want": want, "want_name": want_name, "want_exists": want_exists})
    return out[:MAX_ITEMS]


def build_digest(vault, index: dict, layout: dict, today: dt.date | None = None) -> dict:
    """汇总一份 Digest。参数少而全：vault 只用来读复习记录。"""
    today = today or dt.date.today()
    log = load_log(vault)
    inbox = inbox_ids(index, layout)
    draft_list = drafts(layout, today)
    due = due_nodes(index, log, today)
    stubs = [n["id"] for n in index["nodes"] if n.get("stub")]
    cycles = [w for w in index.get("warnings", []) if w.get("code") == "relation_cycle"]
    bridge_list = bridges(index, layout)
    dup_list, link_list = _pairs(index)
    lonely_list = lonely(index)
    batch_list = lonely_batches(index)
    no_year_list = no_year(index)
    misplaced_list = misplaced(index, layout)
    # 年份可疑：演化边两端倒挂、或者年份落在未来。**它们是 index 算出来的结构性矛盾**，
    # 不依赖任何外部知识——口述一句"year 填 2017"没人能核，但"它比它的前身还早"能算。
    bad_years = [w["message"] for w in index.get("warnings", [])
                 if w.get("code") in ("year_inverted", "year_in_future")][:MAX_ITEMS]
    # 空得过头的对比组：半张表都是空的，摆出来也读不出东西。点一条该能直接去"补一轮"
    gap_list = compare_gaps(vault, index)
    return {
        "generated_at": today.isoformat(),
        "inbox": inbox,
        "drafts": draft_list,
        "due": due,
        "stubs": stubs,
        "bridges": bridge_list,
        "links": link_list,
        "lonely": lonely_list[:MAX_ITEMS],
        "lonely_batches": batch_list[:MAX_ITEMS],
        "no_year": no_year_list[:MAX_ITEMS],
        "misplaced": misplaced_list,
        "duplicates": dup_list,
        "cycles": [w["message"] for w in cycles][:MAX_ITEMS],
        "bad_years": bad_years,
        "compare_gaps": gap_list[:MAX_ITEMS],
        "issues": issues_summary(vault),
        "counts": {"inbox": len(inbox), "drafts": len(draft_list),
                   "stale_drafts": sum(1 for d in draft_list if d["stale"]),
                   "due": len(due), "stubs": len(stubs), "bridges": len(bridge_list),
                   "links": len(link_list), "lonely": len(lonely_list),
                   "lonely_batches": len(batch_list),
                   "no_year": len(no_year_list), "misplaced": len(misplaced_list),
                   "duplicates": len(dup_list), "cycles": len(cycles),
                   "bad_years": len(bad_years),
                   "compare_gaps": len(gap_list),
                   "issues": issues_summary(vault)["count"]},
    }
