"""今日清单（F10.3）：教练的调度。**不调 LLM。**

"今天干什么"是确定性排序，不是生成任务——LLM 只在制定计划、出题、批改时出场。

优先级固定：

    逾期错题 > 到期复习 > 当前阶段「未建」的点 > 「只有壳」的点 > 孤点 > Inbox 里待上图的

前两项属于**线 B 保鲜**（图谱不腐烂），中间几项属于**线 A 建设**（图谱长出来）。
空图时前两项自然为空，清单从第三项开始照样排得出东西——学习计划本来就不需要图里先有节点。

同一个节点只出现一次：按上面的顺序，先被谁捡走就算谁的。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .digest import link_hints
from .placement import inbox_ids, target_group
from .projects import (SHELL, UNBUILT, done_ids, load_hours, lists_of, point_ids,
                       progress_of_project, schedule_of, states_of)
from .quiz import load_quiz_log, wrong_nodes
from .review import due_nodes, load_log

WRONG_TOP = 5        # 错题一次最多摆出几个：一屏看得完才会真去做
INBOX_TOP = 5
LONELY_TOP = 3       # 孤点：优先级最低的一类，摆多了会把上面几类挤出视线
REVIEW_MINUTES = 3   # 复习一个点大致几分钟：晨间简报要给个"今天大概多久"的数
LINK_MINUTES = 2     # 连一条边大致几分钟


def current_stage(ls: dict, points: dict[str, str]) -> tuple[int, dict] | None:
    """当前阶段 = 第一个还没全部建出来的阶段。全建完了就没有"当前阶段"了。

    收一份**清单**（学习主线 / 面试清单 / 领域地图都一样），不收整个项目——
    一个项目可以同时有好几份清单，各自有各自的进度。
    """
    for i, stage in enumerate(ls.get("stages") or []):
        if any(points.get(p.get("id")) in (UNBUILT, SHELL) for p in stage.get("points") or []):
            return i, stage
    return None


def _wrong_items(vault: Path, by_id: dict) -> list[dict]:
    rows = [w for w in wrong_nodes(load_quiz_log(vault), WRONG_TOP) if w["wrong"]]
    return [{"kind": "wrong", "id": w["id"], "name": (by_id.get(w["id"]) or {}).get("name") or w["id"],
             "detail": f"错过 {w['wrong']} 次"} for w in rows if w["id"] in by_id]


def _due_items(index: dict, log: dict, today: dt.date) -> list[dict]:
    out = []
    for d in due_nodes(index, log, today):
        detail = f"逾期 {d['overdue_days']} 天" if d["overdue_days"] else "今天到期"
        if d.get("lapses"):
            detail += f" · 忘过 {d['lapses']} 次"
        out.append({"kind": "due", "id": d["id"], "name": d["name"], "detail": detail})
    return out


def _stage_items(pid: str, project: dict, prog: dict) -> list[dict]:
    """这个项目当前该动手建的点：各份清单的当前阶段里还没建好的，「未建」排在「只有壳」前面。

    **按项目截断，不按清单截断。** 一个项目开三份清单不代表你一天能学三倍，
    `daily_quota` 是项目的（你的时间只有一份）。
    """
    picked = []
    for li, ls in enumerate(lists_of(project)):
        points = (prog.get("lists") or [{}])[li]["points"] if li < len(prog.get("lists") or []) else {}
        found = current_stage(ls, points)
        if not found:
            continue
        _, stage = found
        for kind in (UNBUILT, SHELL):
            for p in stage.get("points") or []:
                if points.get(p.get("id")) == kind:
                    picked.append({"kind": "unbuilt" if kind == UNBUILT else "shell",
                                   "id": p["id"], "name": p.get("name") or p["id"],
                                   "why": p.get("why") or "", "project": pid,
                                   "project_name": project.get("name") or pid,
                                   "list": ls.get("name") or "", "stage": stage.get("name") or "",
                                   "detail": "图里还没有，先把它建出来" if kind == UNBUILT else "只有壳，去写正文"})
    return picked[: max(1, int(project.get("daily_quota") or 2))]


def _lonely_items(index: dict, by_id: dict) -> list[dict]:
    """一条关系都没有的点。

    **这是今日清单里唯一一类"连"的任务**，别的全是"写"和"考"。加它是因为实盘上
    84 个节点里 50 个度为 0——整个产品（画布、最短解释链、历史视图、跨组桥）都建在边上，
    而欠的正是边。它排在最后：孤点不会腐烂，今天不连明天也在，不该挤掉到期复习。

    只算**已经建出来**的点：stub 和幽灵占位没有正文，还谈不上"该连谁"。
    顺手带上 digest 算出来的那条建议（名字摆明了有关系的），点一下就能连——
    没有建议的也照样摆出来，那种更需要人自己想或者问 AI。
    """
    hint_of: dict[str, dict] = {}
    for h in link_hints(index):
        for side, other in ((h["source"], h["target"]), (h["target"], h["source"])):
            hint_of.setdefault(side, {"relation": h["relation"], "target": other, "why": h["reason"]})
    # 和 digest.lonely 同一条口径（这里多一个 rank 排序和建议配对，所以没直接复用）：
    # 聚合文档不算孤点，对比组和领域总览本来就可能一条边都没有
    rows = [n for n in index["nodes"]
            if not n.get("virtual") and not n.get("stub") and n.get("path")
            and not n.get("degree") and not n.get("aggregate")]
    # 有现成建议的排前面：同样是孤点，能一键连的那个今天真会被连
    rows.sort(key=lambda n: (n["id"] not in hint_of, -(n.get("rank") or 0), n["id"]))
    out, covered = [], set()
    for n in rows:
        if len(out) >= LONELY_TOP:
            break
        # 互为建议的一对（Intel平台 / Intel手册）只摆一个：连那一条边，两个一起脱离孤岛，
        # 摆两次等于用掉两个坑办同一件事
        if n["id"] in covered:
            continue
        hint = hint_of.get(n["id"]) or {}
        if hint:
            covered.add(hint["target"])
        out.append({"kind": "lonely", "id": n["id"],
                    "name": (by_id.get(n["id"]) or {}).get("name") or n["id"],
                    "detail": "一条关系都没有，还是座孤岛",
                    "why": hint.get("why") or "",
                    "link": {"relation": hint["relation"], "target": hint["target"]} if hint else {}})
    return out


def _inbox_items(index: dict, layout: dict, by_id: dict) -> list[dict]:
    out = []
    for nid in inbox_ids(index, layout)[:INBOX_TOP]:
        gid = target_group(nid, index, layout)
        gname = layout.get("groups", {}).get(gid, {}).get("name") if gid else None
        out.append({"kind": "inbox", "id": nid, "name": (by_id.get(nid) or {}).get("name") or nid,
                    "detail": f"放进「{gname}」" if gname else "还没上画布"})
    return out


def project_lines(doc: dict, index: dict, log: dict, today: dt.date) -> tuple[list[dict], dict[str, dict]]:
    """每份清单一行进度 + 时间账，外加各项目的掌握度表（后面排清单要用，不重算第二遍）。

    一行一份清单而不是一行一个项目：一个项目里「学习主线」和「面试清单」的进度是两回事，
    并成一行只会两边都看不清。
    """
    lines, progress = [], {}
    for pid, project in (doc.get("projects") or {}).items():
        prog = progress_of_project(project, index, log, today)
        progress[pid] = prog
        for li, ls in enumerate(lists_of(project)):
            points = prog["lists"][li]["points"] if li < len(prog["lists"]) else {}
            part = prog["lists"][li] if li < len(prog["lists"]) else {"built": 0, "total": 0}
            found = current_stage(ls, points)
            sched = schedule_of(ls, done_ids(points), project.get("weekly_hours"), today)
            row = sched["stages"][found[0]] if found else None
            lines.append({"id": pid, "name": project.get("name") or pid,
                          "list": ls.get("name") or "", "kind": ls.get("kind") or "学习",
                          "stage": found[1].get("name") if found else "",
                          "done": not found and part["total"] > 0,
                          "built": part["built"], "total": part["total"],
                          "behind": sched["behind"], "verdict": sched["verdict"],
                          "days_left": sched["days_left"], "suggested_quota": sched["suggested_quota"],
                          "stage_deadline": (row.get("deadline") or row.get("suggested_deadline")) if row else None})
    return lines, progress


def quiz_pools(index: dict, log: dict, items: list[dict], doc: dict,
               project: str | None = None) -> dict[str, list[str]]:
    """出题范围。**只收已经建出来的节点**——「未建」和「只有壳」没有正文，出不了题。

    - 今日：错题 + 到期，也就是清单里那两类，默认就考这些
    - 没考过：有正文但复习记录是空的，新学的东西第一次自测
    - 本项目：当前项目里已经建出来的点（项目是视角，这一档就是那个视角的考试范围）
    - 已建全部：想通考一遍时用
    """
    # 聚合文档也不收：考的是知识点，不是那张目录/对比表
    built = [n["id"] for n in index["nodes"]
             if not n.get("virtual") and not n.get("stub") and n.get("path")
             and not n.get("aggregate")]
    built_set = set(built)
    reviewed = {nid for nid, e in (log.get("nodes") or {}).items() if (e or {}).get("reviews")}
    pools = {"今日": [it["id"] for it in items if it["kind"] in ("wrong", "due")],
             "没考过": [nid for nid in built if nid not in reviewed]}
    if project:
        pools["本项目"] = [nid for nid in point_ids(doc, project) if nid in built_set]
    pools["已建全部"] = built
    return pools


def build_today(vault: Path, index: dict, layout: dict, doc: dict,
                today: dt.date | None = None, project: str | None = None) -> dict:
    """今天干什么。`project` 给了就**整屏都只看这个项目**：建设项、到期复习、错题。

    这**不违反**"复习调度全局唯一"（重构方案 §1）：间隔、掌握度、next_due 仍然只有一份，
    同一个点在哪个项目里看都是同一个状态。这里过滤的只是**今天这一屏摆谁**——
    在 A 项目里学的时候，摆一堆 B 项目的到期项只会让人无从下手。

    但**不能把它们藏得无影无踪**：别的项目还欠着多少，用 `elsewhere` 如实报出来，
    面板上写一句"另有 N 个在别的项目"。藏起来的复习等于没有复习。
    """
    today = today or dt.date.today()
    log = load_log(vault)
    by_id = {n["id"]: n for n in index["nodes"] if not n.get("virtual")}
    wrong_ids = {w["id"] for w in wrong_nodes(load_quiz_log(vault), 999) if w["wrong"]}
    lines, progress = project_lines(doc, index, log, today)

    wrong_items = _wrong_items(vault, by_id)
    due_items = _due_items(index, log, today)
    elsewhere = {"wrong": 0, "due": 0}
    if project:
        mine = set(point_ids(doc, project))
        elsewhere = {"wrong": sum(1 for i in wrong_items if i["id"] not in mine),
                     "due": sum(1 for i in due_items if i["id"] not in mine)}
        wrong_items = [i for i in wrong_items if i["id"] in mine]
        due_items = [i for i in due_items if i["id"] in mine]

    ordered: list[dict] = []
    ordered += wrong_items
    ordered += due_items
    for pid, pr in (doc.get("projects") or {}).items():
        if project and pid != project:
            continue
        ordered += _stage_items(pid, pr, progress[pid])
    lonely = _lonely_items(index, by_id)
    if project:
        mine = set(point_ids(doc, project))
        lonely = [i for i in lonely if i["id"] in mine]
    ordered += lonely
    ordered += _inbox_items(index, layout, by_id) if not project else []

    items, seen = [], set()
    for it in ordered:                      # 同一个节点只留优先级最高的那一次
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        items.append(it)
    counts: dict[str, int] = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    states = states_of([it["id"] for it in items], index, log, wrong_ids, today)
    for it in items:
        it["state"] = states.get(it["id"]) or {}
    return {"generated_at": today.isoformat(), "items": items, "counts": counts,
            "projects": [ln for ln in lines if not project or ln["id"] == project],
            "pools": quiz_pools(index, log, items, doc, project),
            "elsewhere": elsewhere,
            "estimate_hours": _estimate(items, doc)}


def _estimate(items: list[dict], doc: dict) -> float:
    """今天大概要多久：建设项按负荷档折算，复习按每个 REVIEW_MINUTES 分钟。

    **只是个量级**，不是承诺——但"今天 3 个新点"看不出是一小时还是一下午，
    而这正是早上决定做不做的那个判断（重构方案 §4 晨间简报）。
    """
    loads = {}
    for pr in (doc.get("projects") or {}).values():
        for ls in lists_of(pr):
            for stage in ls.get("stages") or []:
                for pt in stage.get("points") or []:
                    loads.setdefault(pt.get("id"), pt)
    hours = 0.0
    for it in items:
        if it["kind"] in ("unbuilt", "shell"):
            hours += load_hours(loads.get(it["id"]) or {})
        elif it["kind"] in ("due", "wrong"):
            hours += REVIEW_MINUTES / 60
        elif it["kind"] == "lonely":
            hours += LINK_MINUTES / 60
    return round(hours, 1)
