"""学习计划（F10）：目标 → 要掌握的点。**计划是图谱的施工图，不是图谱的选集。**

和复习/测验的关系是两条线，只在一处交汇：计划里的一个点被"建成"（md 落盘）之后，
它自动进入复习队列。除此之外互不依赖——所以空图也能立计划，那正是它的用途。

最要紧的一条：`points` 里的 id **允许指向图里还不存在的节点**。你要学 Transformer 的时候
这些节点一个都没有，计划就是用来把它们填出来的。待建的点只活在这个文件里，
**不预先在 nodes/_stubs/ 下建空壳**——立一个计划就凭空多出 20 个空文件，
check / Inbox / Digest 全被污染，而其中多数你未必真会去学。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .mdio import load_json, write_json_atomic
from .review import next_due_for

SCHEMA_VERSION = 1

# 掌握度五档。前两档属于"线 A 建设"，后三档属于"线 B 保鲜"——
# 一个还没写出来的知识点谈不上"学没学"，它是**还没建**。
UNBUILT, SHELL, LEARNED, MASTERED, DUE = "未建", "只有壳", "学过", "已掌握", "待复习"
MASTERY_ORDER = (UNBUILT, SHELL, DUE, LEARNED, MASTERED)
MASTERED_STEP = 4          # 间隔序号到这一档才算"已掌握"（对应间隔 7 天以上）


def plans_path(vault: Path) -> Path:
    return vault / ".knowrary" / "plans.json"


def empty_plans() -> dict:
    return {"schema_version": SCHEMA_VERSION, "revision": 0, "updated_at": None, "plans": {}}


def load_plans(vault: Path) -> dict:
    path = plans_path(vault)
    if not path.exists():
        return empty_plans()
    try:
        data = load_json(path)
    except (ValueError, OSError):
        return empty_plans()
    if not isinstance(data, dict) or not isinstance(data.get("plans"), dict):
        return empty_plans()
    data["schema_version"] = SCHEMA_VERSION
    data.setdefault("revision", 0)
    return data


def save_plans(vault: Path, doc: dict) -> dict:
    """写盘并把 revision 往前推一格。并发由调用方用 base_revision 挡。"""
    doc["schema_version"] = SCHEMA_VERSION
    doc["revision"] = int(doc.get("revision") or 0) + 1
    doc["updated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    write_json_atomic(plans_path(vault), doc)
    return doc


def mastery_of(meta: dict | None, entry: dict | None, today: dt.date) -> str:
    """一个知识点现在处在哪一档。**全部现算，不落盘**——存下来就是第二份真值，必然漂移。"""
    if meta is None or meta.get("virtual"):
        return UNBUILT
    if meta.get("stub"):
        return SHELL
    due = next_due_for(entry, meta.get("learned"))
    if due and dt.date.fromisoformat(due[:10]) <= today:
        return DUE
    return MASTERED if (entry or {}).get("step", 0) >= MASTERED_STEP else LEARNED


def progress_of(plan: dict, index: dict, log: dict, today: dt.date | None = None) -> dict:
    """一个计划的进度：每个点一档，外加各档计数。"""
    today = today or dt.date.today()
    by_id = {n["id"]: n for n in index["nodes"]}
    points: dict[str, str] = {}
    for stage in plan.get("stages") or []:
        for p in stage.get("points") or []:
            pid = p.get("id")
            if pid and pid not in points:
                points[pid] = mastery_of(by_id.get(pid), log["nodes"].get(pid), today)
    counts = {k: 0 for k in MASTERY_ORDER}
    for m in points.values():
        counts[m] = counts.get(m, 0) + 1
    built = len(points) - counts[UNBUILT] - counts[SHELL]
    return {"points": points, "counts": counts, "total": len(points), "built": built}


def all_progress(doc: dict, index: dict, log: dict, today: dt.date | None = None) -> dict:
    return {pid: progress_of(plan, index, log, today) for pid, plan in doc.get("plans", {}).items()}


def point_ids(doc: dict, plan_id: str | None = None) -> list[str]:
    """计划里出现过的全部知识点 id，按出现顺序去重。出题范围选择器要用。"""
    out: list[str] = []
    seen = set()
    for pid, plan in doc.get("plans", {}).items():
        if plan_id and pid != plan_id:
            continue
        for stage in plan.get("stages") or []:
            for p in stage.get("points") or []:
                if p.get("id") and p["id"] not in seen:
                    seen.add(p["id"])
                    out.append(p["id"])
    return out


# ---------------------------------------------------------------- 时间账（F10.2b）

# 每个知识点的学习负荷。**让模型估"几小时"是噪声，三档才稳**——
# 换算成小时只是这一张表，整体排得太松或太紧就调它，不用动模型也不用动数据。
LOAD_HOURS = {"轻": 1.0, "中": 2.5, "重": 5.0}
DEFAULT_LOAD = "中"
LOADS = tuple(LOAD_HOURS)

ENOUGH, TIGHT, IMPOSSIBLE = "充裕", "紧", "不可能"
TIGHT_AT = 0.7        # 用掉七成以内的可用时间算充裕，超过就是紧
BUFFER = 1.25         # 建议日期留的余量：排到分秒不差的计划没人做得完
DEFAULT_WEEKLY_HOURS = 7


def as_date(value) -> dt.date | None:
    """日期字段是人手填的自由文本，填错不该让整条链路 500，当没填处理。"""
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def load_hours(point: dict) -> float:
    return LOAD_HOURS.get(point.get("load") or DEFAULT_LOAD, LOAD_HOURS[DEFAULT_LOAD])


def _stage_hours(stage: dict, done: set[str]) -> tuple[float, float, int]:
    """(总工时, 还没建的工时, 还没建的点数)。已经建出来的点不再占用时间预算。"""
    total = left = 0.0
    n = 0
    for p in stage.get("points") or []:
        h = load_hours(p)
        total += h
        if p.get("id") not in done:
            left += h
            n += 1
    return total, left, n


def _days_for(hours: float, daily: float) -> int:
    """按每天能投入多少小时，算这些工时要几天。至少 1 天。"""
    return max(1, int(-(-hours * BUFFER // daily))) if hours > 0 else 0


def schedule_of(plan: dict, done: set[str], today: dt.date | None = None) -> dict:
    """计划的时间账：每阶段排到哪天、装不装得下、落后了几个点。

    **和进度一样全部现算、不落盘**（F10.2 第 2 条）——存下来就是第二份真值。
    也**不调 LLM**：除法是确定性计算，模型只负责给每个点估一档负荷（同 F10.3 的分工）。

    `done` 是"已经建出来、不再占用时间预算"的点。阶段的建议截止日按**剩余工时**
    在窗口里按比例摊——已经做完的阶段不该再占未来的日子。
    """
    today = today or dt.date.today()
    weekly = max(1, int(plan.get("weekly_hours") or DEFAULT_WEEKLY_HOURS))
    daily = weekly / 7.0
    stages = plan.get("stages") or []

    total = sum(_stage_hours(s, done)[0] for s in stages)
    remaining = sum(_stage_hours(s, done)[1] for s in stages)
    left_points = sum(_stage_hours(s, done)[2] for s in stages)

    target = as_date(plan.get("target_date"))
    days_left = (target - today).days if target else None
    capacity = round(max(0, days_left) * daily, 1) if days_left is not None else None

    verdict = ""
    if target is not None and remaining > 0:
        if capacity <= 0 or remaining > capacity:
            verdict = IMPOSSIBLE
        else:
            verdict = TIGHT if remaining > capacity * TIGHT_AT else ENOUGH

    need_days = _days_for(remaining, daily)
    rows = _stage_rows(stages, done, today, target, remaining, daily)
    return {"total_hours": round(total, 1), "remaining_hours": round(remaining, 1),
            "remaining_points": left_points, "weekly_hours": weekly,
            "days_left": days_left, "capacity_hours": capacity, "verdict": verdict,
            "need_days": need_days,
            "suggested_target_date": (today + dt.timedelta(days=need_days)).isoformat() if need_days else None,
            "suggested_quota": max(1, int(-(-left_points // max(1, days_left)))) if days_left and left_points else 0,
            "stages": rows, "behind": sum(r["behind"] for r in rows)}


def _stage_rows(stages: list[dict], done: set[str], today: dt.date,
                target: dt.date | None, remaining: float, daily: float) -> list[dict]:
    """每个阶段一行：建议截止日 + 已经逾期还没建出来的点数。

    建议日按剩余工时的累计比例摊在 今天→目标日 这个窗口里；没填目标日就按投入速度顺排。
    `behind` 只看**计划里写着的** deadline（人手填的或采纳提议时带下来的），
    建议日不参与判定——建议随时会变，拿它判"落后"会天天变脸。
    """
    rows, acc = [], 0.0
    window = (target - today).days if target else None
    for stage in stages:
        total_h, left_h, left_n = _stage_hours(stage, done)
        acc += left_h
        if window is not None and window > 0 and remaining > 0:
            at = today + dt.timedelta(days=max(1, round(window * acc / remaining)))
        elif remaining > 0:
            at = today + dt.timedelta(days=_days_for(acc, daily))
        else:
            at = None
        due = as_date(stage.get("deadline"))
        rows.append({"name": stage.get("name") or "", "hours": round(total_h, 1),
                     "remaining_hours": round(left_h, 1), "remaining_points": left_n,
                     "suggested_deadline": at.isoformat() if at else None,
                     "deadline": stage.get("deadline") or None,
                     "behind": left_n if (due and due < today) else 0})
    return rows


def all_schedules(doc: dict, progress: dict, today: dt.date | None = None) -> dict:
    """每个计划一份时间账。`done` 从掌握度派生：只有「未建」和「只有壳」还欠着工时。"""
    out = {}
    for pid, plan in (doc.get("plans") or {}).items():
        points = (progress.get(pid) or {}).get("points") or {}
        done = {nid for nid, m in points.items() if m not in (UNBUILT, SHELL)}
        out[pid] = schedule_of(plan, done, today)
    return out
