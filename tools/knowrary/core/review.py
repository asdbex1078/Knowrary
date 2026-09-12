"""复习记录（F8）：固定间隔的遗忘曲线，记录只进 .knowrary/review-log.json，不碰 md。

为什么不写进 frontmatter：复习是高频、机器产生的状态，写进 md 会让每次复习都产生
一次文件改动与 git 噪音；md 里只留 `learned`（首次入库日期）。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .mdio import load_json, write_json_atomic

SCHEMA_VERSION = 1
INTERVALS = (1, 2, 4, 7, 15, 30)     # 第 n 次复习后，隔多少天再来一次
FIRST_DELAY = 1                       # 只有 learned、还没复习过：learned + 1 天到期


def log_path(vault: Path) -> Path:
    return vault / ".knowrary" / "review-log.json"


def empty_log() -> dict:
    return {"schema_version": SCHEMA_VERSION, "updated_at": None, "nodes": {}}


def load_log(vault: Path) -> dict:
    path = log_path(vault)
    if not path.exists():
        return empty_log()
    try:
        data = load_json(path)
    except (ValueError, OSError):
        return empty_log()
    return data if isinstance(data, dict) and "nodes" in data else empty_log()


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


def next_due_for(entry: dict | None, learned) -> str | None:
    """算下一次到期日：复习过按间隔表推进，没复习过按 learned + 1 天。"""
    reviews = (entry or {}).get("reviews") or []
    if reviews:
        last = _date(reviews[-1])
        if last is None:
            return None
        step = INTERVALS[min(len(reviews), len(INTERVALS)) - 1]
        return (last + dt.timedelta(days=step)).isoformat()
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
                    "overdue_days": (today - _date(due)).days})
    return sorted(out, key=lambda d: d["due"])


def record_review(vault: Path, node_id: str, when: dt.date | None = None) -> dict:
    """记一次复习，返回该节点的新记录（含下一次到期日）。"""
    log = load_log(vault)
    entry = log["nodes"].setdefault(node_id, {"reviews": []})
    entry["reviews"].append((when or dt.date.today()).isoformat())
    entry["next_due"] = next_due_for(entry, None)
    save_log(vault, log)
    return entry
