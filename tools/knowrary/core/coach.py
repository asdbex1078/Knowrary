"""今日清单（F10.3）：教练的调度。**不调 LLM。**

"今天干什么"是确定性排序，不是生成任务——LLM 只在制定计划、出题、批改时出场。

优先级固定：

    逾期错题 > 到期复习 > 当前阶段「未建」的点 > 「只有壳」的点 > Inbox 里待上图的

前两项属于**线 B 保鲜**（图谱不腐烂），中间两项属于**线 A 建设**（图谱长出来）。
空图时前两项自然为空，清单从第三项开始照样排得出东西——学习计划本来就不需要图里先有节点。

同一个节点只出现一次：按上面的顺序，先被谁捡走就算谁的。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .placement import inbox_ids, target_group
from .plans import SHELL, UNBUILT, progress_of, schedule_of
from .quiz import load_quiz_log, wrong_nodes
from .review import due_nodes, load_log

WRONG_TOP = 5        # 错题一次最多摆出几个：一屏看得完才会真去做
INBOX_TOP = 5


def current_stage(plan: dict, points: dict[str, str]) -> tuple[int, dict] | None:
    """当前阶段 = 第一个还没全部建出来的阶段。全建完了就没有"当前阶段"了。"""
    for i, stage in enumerate(plan.get("stages") or []):
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


def _stage_items(plan_id: str, plan: dict, points: dict[str, str]) -> list[dict]:
    """当前阶段里还没建好的点，「未建」排在「只有壳」前面，按 daily_quota 截断。"""
    found = current_stage(plan, points)
    if not found:
        return []
    _, stage = found
    picked = []
    for kind in (UNBUILT, SHELL):
        for p in stage.get("points") or []:
            if points.get(p.get("id")) == kind:
                picked.append({"kind": "unbuilt" if kind == UNBUILT else "shell",
                               "id": p["id"], "name": p.get("name") or p["id"],
                               "why": p.get("why") or "", "plan": plan_id,
                               "plan_name": plan.get("name") or plan_id, "stage": stage.get("name") or "",
                               "detail": "图里还没有，先把它建出来" if kind == UNBUILT else "只有壳，去写正文"})
    return picked[: max(1, int(plan.get("daily_quota") or 2))]


def _inbox_items(index: dict, layout: dict, by_id: dict) -> list[dict]:
    out = []
    for nid in inbox_ids(index, layout)[:INBOX_TOP]:
        gid = target_group(nid, index, layout)
        gname = layout.get("groups", {}).get(gid, {}).get("name") if gid else None
        out.append({"kind": "inbox", "id": nid, "name": (by_id.get(nid) or {}).get("name") or nid,
                    "detail": f"放进「{gname}」" if gname else "还没上画布"})
    return out


def plan_lines(doc: dict, index: dict, log: dict, today: dt.date) -> tuple[list[dict], dict[str, dict]]:
    """每个计划一行进度 + 时间账，外加各自的掌握度表（后面排清单要用，不重算第二遍）。

    时间账在这里一并算：清单要回答的是"今天干什么"，而"我是不是已经落后了"是同一个问题的另一半。
    仍然不调 LLM——除法而已。
    """
    lines, progress = [], {}
    for pid, plan in (doc.get("plans") or {}).items():
        prog = progress_of(plan, index, log, today)
        progress[pid] = prog
        found = current_stage(plan, prog["points"])
        done = {nid for nid, m in prog["points"].items() if m not in (UNBUILT, SHELL)}
        sched = schedule_of(plan, done, today)
        row = sched["stages"][found[0]] if found else None
        lines.append({"id": pid, "name": plan.get("name") or pid,
                      "stage": found[1].get("name") if found else "",
                      "done": not found and prog["total"] > 0,
                      "built": prog["built"], "total": prog["total"],
                      "behind": sched["behind"], "verdict": sched["verdict"],
                      "days_left": sched["days_left"], "suggested_quota": sched["suggested_quota"],
                      "stage_deadline": (row.get("deadline") or row.get("suggested_deadline")) if row else None})
    return lines, progress


def quiz_pools(index: dict, log: dict, items: list[dict]) -> dict[str, list[str]]:
    """出题范围三档。**只收已经建出来的节点**——「未建」和「只有壳」没有正文，出不了题。

    - 今日：错题 + 到期，也就是清单里那两类，默认就考这些
    - 没考过：有正文但复习记录是空的，新学的东西第一次自测
    - 已建全部：想通考一遍时用
    """
    built = [n["id"] for n in index["nodes"]
             if not n.get("virtual") and not n.get("stub") and n.get("path")]
    reviewed = {nid for nid, e in (log.get("nodes") or {}).items() if (e or {}).get("reviews")}
    today = [it["id"] for it in items if it["kind"] in ("wrong", "due")]
    return {"今日": today,
            "没考过": [nid for nid in built if nid not in reviewed],
            "已建全部": built}


def build_today(vault: Path, index: dict, layout: dict, doc: dict,
                today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    log = load_log(vault)
    by_id = {n["id"]: n for n in index["nodes"] if not n.get("virtual")}
    lines, progress = plan_lines(doc, index, log, today)

    ordered: list[dict] = []
    ordered += _wrong_items(vault, by_id)
    ordered += _due_items(index, log, today)
    for pid, plan in (doc.get("plans") or {}).items():
        ordered += _stage_items(pid, plan, progress[pid]["points"])
    ordered += _inbox_items(index, layout, by_id)

    items, seen = [], set()
    for it in ordered:                      # 同一个节点只留优先级最高的那一次
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        items.append(it)
    counts: dict[str, int] = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    return {"generated_at": today.isoformat(), "items": items, "counts": counts, "plans": lines,
            "pools": quiz_pools(index, log, items)}
