"""复习记录（F8）：遗忘曲线 + 三档反馈，记录只进 .knowrary/review-log.json，不碰 md。

为什么不写进 frontmatter：复习是高频、机器产生的状态，写进 md 会让每次复习都产生
一次文件改动与 git 噪音；md 里只留 `learned`（首次入库日期）。

这里只存"调度状态"（间隔序号 step / 下次到期 next_due / 忘记次数 lapses），
答题的题面与作答原文是事件流，另存 quiz-log.json（见 core/quiz.py）——
调度每次都要全量读这个文件，不能让它被历史正文撑大。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .mdio import load_json, write_json_atomic

SCHEMA_VERSION = 2
INTERVALS = (1, 2, 4, 7, 15, 30)     # 间隔序号 step 对应的天数
FIRST_DELAY = 1                       # 只有 learned、还没复习过：learned + 1 天到期

REMEMBERED, FUZZY, FORGOT = "记得", "模糊", "忘了"
GRADES = (REMEMBERED, FUZZY, FORGOT)
DEFAULT_GRADE = REMEMBERED            # v1 记录升级、以及不带档位的旧接口调用


def log_path(vault: Path) -> Path:
    return vault / ".knowrary" / "review-log.json"


def empty_log() -> dict:
    return {"schema_version": SCHEMA_VERSION, "updated_at": None, "nodes": {}}


def _upgrade_entry(entry) -> dict:
    """把一条记录规整成 v2 形状。v1 的 reviews 是日期字符串数组，没有 step / lapses。"""
    if not isinstance(entry, dict):
        return {"reviews": [], "step": 0, "lapses": 0, "next_due": None}
    reviews = []
    for item in entry.get("reviews") or []:
        if isinstance(item, dict):
            date = str(item.get("date") or "")[:10]
            grade = item.get("grade") if item.get("grade") in GRADES else DEFAULT_GRADE
        else:
            date, grade = str(item)[:10], DEFAULT_GRADE   # v1：一律当作"记得"
        if date:
            reviews.append({"date": date, "grade": grade})
    step = entry.get("step")
    if not isinstance(step, int) or step < 0:
        # v1 没有 step：旧算法用 len(reviews) 当序号，等价于每次都"记得"
        step = len(reviews)
    lapses = entry.get("lapses")
    if not isinstance(lapses, int) or lapses < 0:
        lapses = sum(1 for r in reviews if r["grade"] == FORGOT)
    return {"reviews": reviews, "step": step, "lapses": lapses, "next_due": entry.get("next_due")}


def load_log(vault: Path) -> dict:
    """读并就地升级到 v2。只在内存里升级，不写盘——读操作不该改用户的文件。"""
    path = log_path(vault)
    if not path.exists():
        return empty_log()
    try:
        data = load_json(path)
    except (ValueError, OSError):
        return empty_log()
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), dict):
        return empty_log()
    return {"schema_version": SCHEMA_VERSION, "updated_at": data.get("updated_at"),
            "nodes": {nid: _upgrade_entry(e) for nid, e in data["nodes"].items()}}


def save_log(vault: Path, log: dict) -> None:
    log["schema_version"] = SCHEMA_VERSION
    log["updated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    write_json_atomic(log_path(vault), log)


def _date(value) -> dt.date | None:
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def interval_for(step: int) -> int:
    """间隔序号 → 天数。step=0（刚忘过）与 step=1 都回到最短的 1 天；到顶后固定 30 天。

    注：设计文档 F8.3 写的是"超过 6 次后每 60 天"，但既有实现一直是封顶 30 天，
    现有用例也按 30 天断言。这里沿用实现，不在三档反馈这一版顺手改动复习节奏。
    """
    return INTERVALS[max(min(step, len(INTERVALS)) - 1, 0)]


def advance_step(step: int, grade: str) -> int:
    """F8.3 三档：忘了 → 序号归 0；模糊 → 序号不变；记得 → 序号 +1。"""
    if grade == FORGOT:
        return 0
    if grade == FUZZY:
        return max(step, 0)
    return max(step, 0) + 1


def next_due_for(entry: dict | None, learned) -> str | None:
    """算下一次到期日：复习过按 step 查间隔表，没复习过按 learned + 1 天。"""
    entry = _upgrade_entry(entry) if entry else None
    reviews = (entry or {}).get("reviews") or []
    if reviews:
        last = _date(reviews[-1]["date"])
        if last is None:
            return None
        return (last + dt.timedelta(days=interval_for(entry["step"]))).isoformat()
    start = _date(learned)
    return (start + dt.timedelta(days=FIRST_DELAY)).isoformat() if start else None


def due_nodes(index: dict, log: dict, today: dt.date | None = None) -> list[dict]:
    """今天（含之前）该复习的节点，按到期日从早到晚。"""
    today = today or dt.date.today()
    out = []
    for node in index["nodes"]:
        if node.get("virtual") or node.get("stub"):
            continue
        entry = log["nodes"].get(node["id"])
        due = next_due_for(entry, node.get("learned"))
        if not due or _date(due) > today:
            continue
        out.append({"id": node["id"], "name": node.get("name") or node["id"],
                    "field": node.get("field"), "due": due,
                    "reviews": len((entry or {}).get("reviews") or []),
                    "step": (entry or {}).get("step", 0),
                    "lapses": (entry or {}).get("lapses", 0),
                    "overdue_days": (today - _date(due)).days})
    return sorted(out, key=lambda d: d["due"])


def record_review(vault: Path, node_id: str, when: dt.date | None = None,
                  grade: str = DEFAULT_GRADE) -> dict:
    """记一次复习，返回该节点的新记录（含下一次到期日）。"""
    if grade not in GRADES:
        raise ValueError(f"未知的复习档位 `{grade}`，只能是 {'/'.join(GRADES)}")
    log = load_log(vault)
    entry = log["nodes"].setdefault(node_id, {"reviews": [], "step": 0, "lapses": 0, "next_due": None})
    day = (when or dt.date.today()).isoformat()
    entry["reviews"].append({"date": day, "grade": grade})
    entry["step"] = advance_step(entry.get("step", 0), grade)
    if grade == FORGOT:
        entry["lapses"] = entry.get("lapses", 0) + 1
    entry["next_due"] = next_due_for(entry, None)
    save_log(vault, log)
    return entry
