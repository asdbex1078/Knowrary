"""Digest：图谱的"待办清单"——草稿、跨分组桥、重复候选、stub、待复习。

和 Inbox 的分工：Inbox 管"新进来的东西往哪放"，Digest 管"图谱里有哪些欠账"。
全部只读，不改任何文件；每一项都给出可定位的节点 id，前端点一下就能跳过去。
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from difflib import SequenceMatcher

from .placement import inbox_ids
from .issues import summary as issues_summary
from .review import due_nodes, load_log

DRAFT_STALE_DAYS = 7        # 草稿放这么多天还没定稿就提醒
NAME_SIMILAR = 0.72         # 名字相似度阈值
SHARED_NEIGHBOURS = 3       # 共同邻居达到这个数也算重复候选
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


def bridges(index: dict, layout: dict) -> list[dict]:
    """跨分组的边按"组对"聚合：连接两个领域的关系往往最有价值。"""
    groups = layout.get("groups", {})
    place = layout.get("nodes", {})
    pairs: dict[tuple[str, str], list[str]] = defaultdict(list)
    for e in index["edges"]:
        a = place.get(e["source"], {}).get("group")
        b = place.get(e["target"], {}).get("group")
        if not a or not b or a == b:
            continue
        pairs[(a, b)].append(e["id"])
    out = [{"from": a, "to": b, "from_name": groups.get(a, {}).get("name", a),
            "to_name": groups.get(b, {}).get("name", b), "count": len(ids), "edges": ids[:5]}
           for (a, b), ids in pairs.items()]
    return sorted(out, key=lambda d: -d["count"])[:MAX_ITEMS]


def duplicates(index: dict) -> list[dict]:
    """重复候选：名字高度相似，或共同邻居多到不像巧合。"""
    nodes = [n for n in index["nodes"] if not n.get("virtual")]
    neighbours: dict[str, set[str]] = defaultdict(set)
    for e in index["edges"]:
        neighbours[e["source"]].add(e["target"])
        neighbours[e["target"]].add(e["source"])
    out = []
    for i, a in enumerate(nodes):
        for b in nodes[i + 1:]:
            name_a, name_b = a.get("name") or a["id"], b.get("name") or b["id"]
            ratio = SequenceMatcher(None, name_a, name_b).ratio()
            shared = neighbours[a["id"]] & neighbours[b["id"]]
            if ratio >= NAME_SIMILAR:
                out.append({"a": a["id"], "b": b["id"], "score": round(ratio, 2),
                            "reason": f"名字相似（{name_a} / {name_b}）"})
            elif len(shared) >= SHARED_NEIGHBOURS:
                out.append({"a": a["id"], "b": b["id"], "score": round(len(shared) / 10, 2),
                            "reason": f"{len(shared)} 个共同邻居：{'、'.join(sorted(shared)[:4])}"})
    return sorted(out, key=lambda d: -d["score"])[:MAX_ITEMS]


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
    dup_list = duplicates(index)
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
        "duplicates": dup_list,
        "cycles": [w["message"] for w in cycles][:MAX_ITEMS],
        "bad_years": bad_years,
        "issues": issues_summary(vault),
        "counts": {"inbox": len(inbox), "drafts": len(draft_list),
                   "stale_drafts": sum(1 for d in draft_list if d["stale"]),
                   "due": len(due), "stubs": len(stubs), "bridges": len(bridge_list),
                   "duplicates": len(dup_list), "cycles": len(cycles),
                   "bad_years": len(bad_years),
                   "issues": issues_summary(vault)["count"]},
    }
