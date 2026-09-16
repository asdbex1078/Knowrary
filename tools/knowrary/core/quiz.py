"""测验记录：答题事件流，只进 .knowrary/quiz-log.json，不碰 md。

字段名跟 QuizQuestion 一致：`ref_answer` 是照我笔记正文抄的标准答案，`my_answer` 是我写的。
这是只追加的事件流，**2026-09-16 之前的老记录里这一项还叫 `answer`**——历史不回写，
而且没有任何地方读它（日历只取 points/grade/stem），留着就是留个档。

和 review-log.json 的分工——review-log 是**状态**（间隔序号、下次到期），
调度每次都要全量读，必须保持紧凑；quiz-log 是**事件流**（题面、标准答案、
我判的档位），只追加、只在回看和统计错题时读。两者混在一起会让调度越跑越慢。

错题本不是第三份数据，而是这份事件流按节点聚合出来的派生结果（wrong_nodes）。
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from pathlib import Path

from .mdio import load_json, write_json_atomic
from .review import FORGOT, FUZZY, GRADES

SCHEMA_VERSION = 1
MAX_STEM = 500          # 题面与答案的留档上限：事件流不该被长正文撑爆
TOP_WRONG = 20


def log_path(vault: Path) -> Path:
    return vault / ".knowrary" / "quiz-log.json"


def empty_log() -> dict:
    return {"schema_version": SCHEMA_VERSION, "updated_at": None, "answers": []}


def load_quiz_log(vault: Path) -> dict:
    path = log_path(vault)
    if not path.exists():
        return empty_log()
    try:
        data = load_json(path)
    except (ValueError, OSError):
        return empty_log()
    if not isinstance(data, dict) or not isinstance(data.get("answers"), list):
        return empty_log()
    data["schema_version"] = SCHEMA_VERSION
    return data


def save_log(vault: Path, log: dict) -> None:
    log["schema_version"] = SCHEMA_VERSION
    log["updated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    write_json_atomic(log_path(vault), log)


def _clean(record: dict, now: str) -> dict | None:
    """规整一条作答记录。档位非法或没有考点就丢弃——错题本靠考点聚合，无考点的留着没用。"""
    grade = record.get("grade")
    if grade not in GRADES:
        return None
    points = [str(p) for p in (record.get("points") or []) if str(p)]
    if not points:
        return None
    out = {"ts": record.get("ts") or now,
           "points": points,
           "type": str(record.get("type") or "")[:40],
           "stem": str(record.get("stem") or "")[:MAX_STEM],
           "ref_answer": str(record.get("ref_answer") or "")[:MAX_STEM],
           "my_answer": str(record.get("my_answer") or "")[:MAX_STEM],
           "grade": grade}
    # 漏掉 vs 记错分开留：漏掉是没想起来，多复习就行；记错是记成了别的东西，
    # 多复习只会把错误记忆焊得更牢，得回去看那张卡。以后回看要能分得出来。
    for key in ("missed", "wrong"):
        vals = [str(x)[:120] for x in (record.get(key) or []) if str(x).strip()]
        if vals:
            out[key] = vals
    return out


def append_answers(vault: Path, records: list[dict]) -> list[dict]:
    """追加若干条作答记录，返回真正落盘的那些。"""
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    cleaned = [c for c in (_clean(r, now) for r in records) if c]
    if not cleaned:
        return []
    log = load_quiz_log(vault)
    log["answers"].extend(cleaned)
    save_log(vault, log)
    return cleaned


def wrong_nodes(log: dict, top: int = TOP_WRONG) -> list[dict]:
    """错题本：按节点聚合答错次数，错得多、错得近的排前面。"""
    stat: dict[str, dict] = defaultdict(lambda: {"wrong": 0, "fuzzy": 0, "attempts": 0, "last": ""})
    for ans in log.get("answers") or []:
        grade = ans.get("grade")
        for nid in ans.get("points") or []:
            row = stat[nid]
            row["attempts"] += 1
            if grade == FORGOT:
                row["wrong"] += 1
            elif grade == FUZZY:
                row["fuzzy"] += 1
            if grade in (FORGOT, FUZZY):
                row["last"] = max(row["last"], str(ans.get("ts") or ""))
    out = [{"id": nid, **row} for nid, row in stat.items() if row["wrong"] or row["fuzzy"]]
    out.sort(key=lambda r: r["last"], reverse=True)          # 同样错得多时，最近错的排前面
    out.sort(key=lambda r: (r["wrong"], r["fuzzy"]), reverse=True)   # 稳定排序，保住上一轮的次序
    return out[:top]


# ---------------------------------------------------------------- 没答完的那份卷子

def open_path(vault: Path) -> Path:
    return vault / ".knowrary" / "quiz-open.json"


def save_open(vault: Path, quiz: dict) -> None:
    """把刚出的一份题存住。

    出题是要花钱和时间的，可关掉对话框、刷新页面、点错一下，题就没了——
    等于白烧一次调用。存下来之后随时能接着答，交卷（或明确放弃）才清掉。
    只存题面，不存作答：作答是事件流，走 append_answers。
    """
    payload = {"schema_version": SCHEMA_VERSION,
               "created": dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
                            .isoformat().replace("+00:00", "Z"),
               "questions": quiz.get("questions") or [],
               "index_revision": quiz.get("index_revision"),
               "style": quiz.get("style") or "",
               "level": quiz.get("level") or ""}
    write_json_atomic(open_path(vault), payload)


def load_open(vault: Path) -> dict | None:
    path = open_path(vault)
    if not path.exists():
        return None
    try:
        data = load_json(path)
    except (ValueError, OSError):
        return None
    if not isinstance(data, dict) or not data.get("questions"):
        return None
    # 改名前存下的卷子里标准答案还叫 `answer`。QuizQuestion 是 extra="forbid" 的，
    # 原样发回前端再提交回来会被契约拒掉——一份没答完的卷子不该因为一次改名就作废。
    for q in data["questions"]:
        if isinstance(q, dict) and "answer" in q:
            q.setdefault("ref_answer", q.pop("answer"))
    return data


def clear_open(vault: Path) -> None:
    open_path(vault).unlink(missing_ok=True)
