"""Digest：图谱的"待办清单"——草稿、跨分组桥、连边建议、重复候选、stub、待复习。

和 Inbox 的分工：Inbox 管"新进来的东西往哪放"，Digest 管"图谱里有哪些欠账"。
全部只读，不改任何文件；每一项都给出可定位的节点 id，前端点一下就能跳过去。
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from .placement import inbox_ids
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
    nodes = [n for n in index["nodes"] if not n.get("virtual")]
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
    """
    rows = [n for n in index["nodes"]
            if not n.get("virtual") and not n.get("stub") and n.get("path") and not n.get("degree")]
    rows.sort(key=lambda n: (-(n.get("rank") or 0), n["id"]))
    return [{"id": n["id"], "name": n.get("name") or n["id"], "field": n.get("field") or "",
             "desc": n.get("desc") or ""} for n in rows]


def no_year(index: dict) -> list[str]:
    """还没填 year 的已建节点。

    和 `bad_years` 分开：那个是**算得出来的矛盾**（演化边两端倒挂、年份在未来），
    这个只是**没填**。没填不是错，但它是历史视图的开关——一个节点没有 year
    就根本不出现在时间轴上，而"时间轴上少了谁"是这张图里最不容易看出来的一种缺失。
    """
    return sorted(n["id"] for n in index["nodes"]
                  if not n.get("virtual") and not n.get("stub") and n.get("path")
                  and not n.get("year"))


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
    no_year_list = no_year(index)
    # 年份可疑：演化边两端倒挂、或者年份落在未来。**它们是 index 算出来的结构性矛盾**，
    # 不依赖任何外部知识——口述一句"year 填 2017"没人能核，但"它比它的前身还早"能算。
    bad_years = [w["message"] for w in index.get("warnings", [])
                 if w.get("code") in ("year_inverted", "year_in_future")][:MAX_ITEMS]
    return {
        "generated_at": today.isoformat(),
        "inbox": inbox,
        "drafts": draft_list,
        "due": due,
        "stubs": stubs,
        "bridges": bridge_list,
        "links": link_list,
        "lonely": lonely_list[:MAX_ITEMS],
        "no_year": no_year_list[:MAX_ITEMS],
        "duplicates": dup_list,
        "cycles": [w["message"] for w in cycles][:MAX_ITEMS],
        "bad_years": bad_years,
        "issues": issues_summary(vault),
        "counts": {"inbox": len(inbox), "drafts": len(draft_list),
                   "stale_drafts": sum(1 for d in draft_list if d["stale"]),
                   "due": len(due), "stubs": len(stubs), "bridges": len(bridge_list),
                   "links": len(link_list), "lonely": len(lonely_list),
                   "no_year": len(no_year_list),
                   "duplicates": len(dup_list), "cycles": len(cycles),
                   "bad_years": len(bad_years),
                   "issues": issues_summary(vault)["count"]},
    }
