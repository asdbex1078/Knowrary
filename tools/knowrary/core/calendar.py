"""学习日历（重构方案 §6）：**全部派生，不新增任何记录。**

现有数据已经够画一张有信息量的日历：

| 每天显示 | 来源 |
| --- | --- |
| 建了几个节点 | md frontmatter 的 `learned` |
| 复习了几次、忘了几次 | `review-log.json` 的 `reviews[].date` / `.grade` |
| 答了几道题 | `quiz-log.json` 的 `answers[].ts` |
| 烧了多少模型钱 | `llm-usage.json` 的 `by_day` |

**学习时长没有做**，那是唯一真正需要新记录的东西：手动计时要处理"忘了停表"，
一旦有脏数据整张日历就不可信；被动推断会把"开着页面去吃饭"算进去。
而"这天学得多不多"，现有数据（建了几个、考了几道）已经回答得了——
时长只是个更差的代理指标。

一个坑：`llm-usage` 的 `by_day` 在 2026-09-16 之前按 **UTC** 分桶，之后按本地日期。
那一段历史数据会偏一天，量很小（几十条），不值得回填——知道有这个断层就行。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .quiz import load_quiz_log
from .review import load_log
from .usage import load_usage

MAX_DAYS = 400          # 一次最多算多长一段：再长热力图也画不下
DETAIL_TOP = 12         # 某一天的明细里每类最多列几条


def _empty_day() -> dict:
    return {"built": 0, "reviews": 0, "lapses": 0, "answers": 0, "wrong": 0,
            "calls": 0, "cost_usd": 0.0}


def _day_of(ts: str | None) -> str:
    return (ts or "")[:10]


def build_calendar(vault: Path, index: dict, start: dt.date, end: dt.date) -> dict:
    """[start, end] 每天一格。纯读，不写任何文件。"""
    if (end - start).days > MAX_DAYS:
        start = end - dt.timedelta(days=MAX_DAYS)
    lo, hi = start.isoformat(), end.isoformat()
    days: dict[str, dict] = {}

    def slot(day: str) -> dict | None:
        if not (lo <= day <= hi):
            return None
        return days.setdefault(day, _empty_day())

    detail: dict[str, dict[str, list]] = {}

    def note(day: str, kind: str, item) -> None:
        rows = detail.setdefault(day, {}).setdefault(kind, [])
        if len(rows) < DETAIL_TOP:
            rows.append(item)

    for node in index["nodes"]:
        if node.get("virtual"):
            continue
        cell = slot(_day_of(node.get("learned")))
        if cell is not None:
            cell["built"] += 1
            note(_day_of(node["learned"]), "built", {"id": node["id"], "name": node.get("name")})

    for nid, entry in (load_log(vault).get("nodes") or {}).items():
        for r in (entry or {}).get("reviews") or []:
            day = _day_of(r.get("date"))
            cell = slot(day)
            if cell is None:
                continue
            cell["reviews"] += 1
            if r.get("grade") == "忘了":
                cell["lapses"] += 1
            note(day, "reviews", {"id": nid, "grade": r.get("grade")})

    for ans in load_quiz_log(vault).get("answers") or []:
        day = _day_of(ans.get("ts"))
        cell = slot(day)
        if cell is None:
            continue
        cell["answers"] += 1
        if ans.get("grade") == "忘了":
            cell["wrong"] += 1
        note(day, "answers", {"points": ans.get("points") or [], "grade": ans.get("grade"),
                              "stem": (ans.get("stem") or "")[:60]})

    for day, bucket in (load_usage(vault).get("by_day") or {}).items():
        cell = slot(day)
        if cell is None:
            continue
        cell["calls"] += int(bucket.get("calls") or 0)
        cell["cost_usd"] = round(cell["cost_usd"] + float(bucket.get("cost_usd") or 0), 4)

    return {"from": lo, "to": hi, "days": days, "detail": detail,
            "totals": _totals(days), "streak": streak(days, end),
            "busiest": max(days, key=lambda d: _weight(days[d]), default=None)}


def _totals(days: dict[str, dict]) -> dict:
    out = _empty_day()
    for cell in days.values():
        for k in out:
            out[k] = round(out[k] + cell[k], 4) if k == "cost_usd" else out[k] + cell[k]
    out["active_days"] = sum(1 for c in days.values() if _weight(c))
    return out


def _weight(cell: dict) -> int:
    """这一天"学了多少"。**不含模型调用**——调了几次 LLM 是花销，不是学习量，
    把它算进热力图会让"跟模型聊了一下午"看起来像"学了一整天"。"""
    return cell["built"] * 3 + cell["reviews"] + cell["answers"]


def streak(days: dict[str, dict], today: dt.date) -> int:
    """连续学习了几天。**今天还没动手不算断**——早上八点打开就显示"断了"太蠢了，
    所以从昨天开始回溯，今天有动静就再加一天。"""
    n = 0
    if _weight(days.get(today.isoformat()) or _empty_day()):
        n = 1
    day = today - dt.timedelta(days=1)
    while _weight(days.get(day.isoformat()) or _empty_day()):
        n += 1
        day -= dt.timedelta(days=1)
    return n
