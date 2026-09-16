"""项目（v2，原「学习计划」）：**项目是视角，不是容器。**

项目不拥有节点，只引用一组 node_id。这一条决定了其余全部设计：

- **交集**（Transformer ∩ CNN = 反向传播）：两个项目都列它，掌握度是同一个；
- **子集**（NLP ⊃ Transformer）：NLP 的选集包含它的全部点，**不需要显式父子**；
- **后来的父级**：建 NLP 时把已有节点选进去，不用重构已有项目。

复习调度必须全局唯一——同一个大脑不可能"在 NLP 项目里记得、在 Transformer 项目里忘了"。
这是生理事实，不是技术选择。所以掌握度、review-log、quiz-log 一律全局，项目只是过滤器。

**一个项目下挂 N 份清单（`lists`）**，每份带一个 `kind`：

    学习 = 按依赖顺序拆   面试 = 按会怎么问拆   领域 = 按覆盖度铺一张地图

`kind` 只决定两件事：用哪份拆解模板、用哪套出题口径。它是"这份清单怎么拆出来的",
不是"项目的类型"——所以它属于清单，不属于项目（重构方案 §3）。

最要紧的一条没变：`points` 里的 id **允许指向图里还不存在的节点**。计划是施工图，
待建的点只活在这个文件里，**不预先在 nodes/_stubs/ 下建空壳**。
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from .mdio import load_json, write_json_atomic
from .review import next_due_for

SCHEMA_VERSION = 2
KINDS = ("学习", "面试", "领域")
DEFAULT_KIND = "学习"

# 掌握度五档。前两档属于"线 A 建设"，后三档属于"线 B 保鲜"——
# 一个还没写出来的知识点谈不上"学没学"，它是**还没建**。
UNBUILT, SHELL, LEARNED, MASTERED, DUE = "未建", "只有壳", "学过", "已掌握", "待复习"
MASTERY_ORDER = (UNBUILT, SHELL, DUE, LEARNED, MASTERED)
MASTERED_STEP = 4          # 间隔序号到这一档才算"已掌握"（对应间隔 7 天以上）

# 项目 id 只允许 ASCII：它会成为 .knowrary/chat/<project_id>/ 的目录名，
# 中文目录名加上将来改名，迁起来纯属自找麻烦。显示名另存 name。
ID_OK = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def projects_path(vault: Path) -> Path:
    return vault / ".knowrary" / "projects.json"


def legacy_plans_path(vault: Path) -> Path:
    return vault / ".knowrary" / "plans.json"


def empty_projects() -> dict:
    return {"schema_version": SCHEMA_VERSION, "revision": 0, "updated_at": None, "projects": {}}


def _squeeze(raw: str) -> str:
    """压成 ASCII。中文全被挤掉，剩下的可能是空串或纯数字——那都不算好名字。"""
    out = re.sub(r"[^A-Za-z0-9_-]+", "-", raw or "").strip("-").lower()[:64]
    return "" if out.isdigit() else out


def ascii_id(raw: str, taken: set[str], name: str = "") -> str:
    """把旧 id 压成合法的项目 id。

    先用 id 本身，压不出东西（`新计划2` → `2`，纯数字不算名字）就用**显示名**再试一次
    （`Transformer` → `transformer`），还不行才 `p1` / `p2`。撞了就加后缀。
    """
    base = _squeeze(raw) or _squeeze(name) or f"p{len(taken) + 1}"
    out, i = base, 2
    while out in taken:
        out = f"{base}-{i}"
        i += 1
    return out


def upgrade_v1(doc: dict) -> dict:
    """v1（plans）→ v2（projects），**只在内存里升，不写盘**。

    沿用 review-log v1→v2 的做法：读的时候升级，下次保存才落成 v2，
    这样看一眼不会改动文件，回滚也简单。一份计划升成"一个项目 + 一份清单"。
    """
    projects: dict[str, dict] = {}
    for pid, plan in (doc.get("plans") or {}).items():
        if not isinstance(plan, dict):
            continue
        new_id = ascii_id(pid, set(projects), plan.get("name") or "")
        projects[new_id] = {
            "name": plan.get("name") or pid,
            "created": plan.get("created"),
            "weekly_hours": plan.get("weekly_hours") or DEFAULT_WEEKLY_HOURS,
            "daily_quota": plan.get("daily_quota") or 2,
            "field": plan.get("field") or "",
            "legacy_id": pid,                      # 对话留档目录迁移要用，迁完可以删
            "lists": [{
                "kind": plan.get("kind") or DEFAULT_KIND,
                "name": plan.get("name") or pid,
                "goal": plan.get("goal") or "",
                "coach": plan.get("coach") or "",
                "field": plan.get("field") or "",
                "target_date": plan.get("target_date"),
                "stages": plan.get("stages") or [],
            }],
        }
    return {"schema_version": SCHEMA_VERSION, "revision": int(doc.get("revision") or 0),
            "updated_at": doc.get("updated_at"), "projects": projects}


def load_projects(vault: Path) -> dict:
    """读项目。没有 projects.json 就去读 plans.json 并就地升级（不写盘）。"""
    path = projects_path(vault)
    if not path.exists():
        legacy = legacy_plans_path(vault)
        if not legacy.exists():
            return empty_projects()
        try:
            old = load_json(legacy)
        except (ValueError, OSError):
            return empty_projects()
        return _merge_levels(upgrade_v1(old)) if isinstance(old, dict) else empty_projects()
    try:
        data = load_json(path)
    except (ValueError, OSError):
        return empty_projects()
    if not isinstance(data, dict):
        return empty_projects()
    if isinstance(data.get("plans"), dict) and "projects" not in data:
        return _merge_levels(upgrade_v1(data))      # 文件名换了但内容还是 v1
    if not isinstance(data.get("projects"), dict):
        return empty_projects()
    data["schema_version"] = SCHEMA_VERSION
    data.setdefault("revision", 0)
    return _merge_levels(data)


def save_projects(vault: Path, doc: dict) -> dict:
    """写盘并把 revision 往前推一格。并发由调用方用 base_revision 挡。"""
    doc["schema_version"] = SCHEMA_VERSION
    doc["revision"] = int(doc.get("revision") or 0) + 1
    doc["updated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    write_json_atomic(projects_path(vault), doc)
    return doc


def lists_of(project: dict) -> list[dict]:
    return [ls for ls in (project.get("lists") or []) if isinstance(ls, dict)]


def list_field(project: dict, ls: dict) -> str:
    """清单自己的领域优先（面试考点常用单独的 field，免得淹掉主线），没有就用项目的。"""
    return (ls.get("field") or "").strip() or (project.get("field") or "").strip()


# ---------------------------------------------------------------- 掌握度（全局唯一）

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


# 学 / 考双态：五档掌握度的另一种编码，更适合一眼扫（重构方案 §4）。
# 「学」回答"这个点建出来了没有"，「考」回答"我到底记没记住"，两条线分开看。
GREY, RED, AMBER, GREEN = "灰", "红", "黄", "绿"


def study_state(meta: dict | None) -> str:
    """学：灰=图里还没有、红=只有壳、绿=有正文。"""
    if meta is None or meta.get("virtual"):
        return GREY
    return RED if meta.get("stub") else GREEN


def exam_state(meta: dict | None, entry: dict | None, wrong: bool, today: dt.date) -> str:
    """考：灰=没考过、红=错过、黄=模糊或到期、绿=记得且没到期。

    **错题本压过自评**（重构方案 §4）。两个数据源可以合法地互相矛盾：
    `review-log` 的三档来自**我自评**，`quiz-log` 里模型判的 missed/wrong 才是错题本，
    而 F9.4 定过"模型判分只许降级、且要我点一下才生效"——所以完全可能出现
    「测验里模型判我答漏了，我自评点了『记得』」。这时显示绿就把错题盖住了，
    而错题本的全部价值恰恰在这里。
    """
    if meta is None or meta.get("virtual"):
        return GREY                         # 还没建出来，谈不上考
    if wrong:
        return RED
    reviews = (entry or {}).get("reviews") or []
    if not reviews:
        return GREY
    due = next_due_for(entry, meta.get("learned"))
    last = (reviews[-1] or {}).get("grade")
    if last == "忘了":
        return RED
    if last == "模糊" or (due and dt.date.fromisoformat(due[:10]) <= today):
        return AMBER
    return GREEN


def states_of(points: list[str], index: dict, log: dict, wrong: set[str],
              today: dt.date | None = None) -> dict:
    """一组点的学/考双态。和掌握度一样**全部现算、不落盘**。"""
    today = today or dt.date.today()
    by_id = {n["id"]: n for n in index["nodes"]}
    out = {}
    for pid in points:
        meta = by_id.get(pid)
        entry = (log.get("nodes") or {}).get(pid)
        out[pid] = {"study": study_state(meta),
                    "exam": exam_state(meta, entry, pid in wrong, today)}
    return out


def stage_points(stages: list[dict]) -> list[str]:
    """一组阶段里出现过的 id，按出现顺序去重。"""
    out, seen = [], set()
    for stage in stages or []:
        for p in stage.get("points") or []:
            pid = p.get("id")
            if pid and pid not in seen:
                seen.add(pid)
                out.append(pid)
    return out


def progress_of(stages: list[dict], index: dict, log: dict, today: dt.date | None = None,
                wrong: set[str] | None = None) -> dict:
    """一组阶段的进度：每个点一档，外加各档计数。**收 stages 而不是整个项目**——
    这样学习主线、面试清单、领域地图三种清单共用同一套算法，不需要各写一份。"""
    today = today or dt.date.today()
    by_id = {n["id"]: n for n in index["nodes"]}
    points = {pid: mastery_of(by_id.get(pid), log["nodes"].get(pid), today)
              for pid in stage_points(stages)}
    counts = {k: 0 for k in MASTERY_ORDER}
    for m in points.values():
        counts[m] = counts.get(m, 0) + 1
    built = len(points) - counts[UNBUILT] - counts[SHELL]
    return {"points": points, "counts": counts, "total": len(points), "built": built,
            "states": states_of(list(points), index, log, wrong or set(), today)}


def merge_progress(parts: list[dict]) -> dict:
    """把一个项目里各份清单的进度并起来。**同一个点只算一次**——
    它同时在学习主线和面试清单里是常态，算两次会让总数虚高。"""
    points: dict[str, str] = {}
    states: dict[str, dict] = {}
    for part in parts:
        points.update(part["points"])
        states.update(part.get("states") or {})
    counts = {k: 0 for k in MASTERY_ORDER}
    for m in points.values():
        counts[m] = counts.get(m, 0) + 1
    built = len(points) - counts[UNBUILT] - counts[SHELL]
    return {"points": points, "counts": counts, "total": len(points), "built": built,
            "states": states}


def progress_of_project(project: dict, index: dict, log: dict, today: dt.date | None = None,
                        wrong: set[str] | None = None) -> dict:
    parts = [progress_of(ls.get("stages") or [], index, log, today, wrong)
             for ls in lists_of(project)]
    return {"lists": parts, "all": merge_progress(parts)}


def all_progress(doc: dict, index: dict, log: dict, today: dt.date | None = None,
                 wrong: set[str] | None = None) -> dict:
    return {pid: progress_of_project(pr, index, log, today, wrong)
            for pid, pr in (doc.get("projects") or {}).items()}


def point_ids(doc: dict, project_id: str | None = None) -> list[str]:
    """项目里出现过的全部知识点 id，按出现顺序去重。出题范围选择器要用。"""
    out: list[str] = []
    seen = set()
    for pid, pr in (doc.get("projects") or {}).items():
        if project_id and pid != project_id:
            continue
        for ls in lists_of(pr):
            for nid in stage_points(ls.get("stages") or []):
                if nid not in seen:
                    seen.add(nid)
                    out.append(nid)
    return out


# ---------------------------------------------------------------- 难度档

# 学到什么份上。**这是一个维度，不是三套逻辑**：同一条链路，只是喂给模型的口径不同——
# 出题深浅、对话展开到哪一层、拆点拆多细，全由它一个字段决定。
#
# 为什么不用数字（1-5 星）：数字要靠脑补对应到"什么算合格"，模型和人都会各想各的；
# 三个词各自绑一句可判定的标准，写在 server/levels.py 里，改口径只改那一处。
#
# **一个项目一个档，就这一个旋钮。** 试过"项目默认 + 清单覆盖"两层，
# 界面上立刻多出一个长得差不多的下拉——两个都写着难度、还得想它们谁盖谁，
# 这种复杂度换来的表达力（同一个项目里两份清单深浅不同）远不值当：
# 真要分深浅，那本来就该是两个项目。
LEVELS = ("了解", "会用", "精通")
DEFAULT_LEVEL = "会用"


def level_of(project: dict | None) -> str:
    """这个项目按哪一档。没填、填错都落默认档。"""
    value = (project or {}).get("level")
    return value if value in LEVELS else DEFAULT_LEVEL


def _merge_levels(doc: dict) -> dict:
    """把清单上的旧 `level` 抬到项目上。

    两层合成一层之前存过的数据里，清单上可能有自己的档——直接丢掉等于把人填过的
    东西悄悄抹了，所以抬上去：项目还是默认档时，用清单里第一个明确写过的。
    """
    for project in (doc.get("projects") or {}).values():
        if not isinstance(project, dict):
            continue
        lifted = next((ls.get("level") for ls in project.get("lists") or []
                       if isinstance(ls, dict) and ls.get("level") in LEVELS), None)
        if lifted and project.get("level") in (None, "", DEFAULT_LEVEL):
            project["level"] = lifted
        for ls in project.get("lists") or []:
            if isinstance(ls, dict):
                ls.pop("level", None)
    return doc


# ---------------------------------------------------------------- 时间账

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


def schedule_of(ls: dict, done: set[str], weekly_hours: int = DEFAULT_WEEKLY_HOURS,
                today: dt.date | None = None) -> dict:
    """一份清单的时间账：每阶段排到哪天、装不装得下、落后了几个点。

    **和进度一样全部现算、不落盘**——存下来就是第二份真值。
    也**不调 LLM**：除法是确定性计算，模型只负责给每个点估一档负荷。

    截止日属于清单（面试有面试的日子，学习主线有自己的目标日），
    而每周投入属于项目——**你的时间只有一份，不会因为多开一份清单就变多**。
    """
    today = today or dt.date.today()
    weekly = max(1, int(weekly_hours or DEFAULT_WEEKLY_HOURS))
    daily = weekly / 7.0
    stages = ls.get("stages") or []

    total = sum(_stage_hours(s, done)[0] for s in stages)
    remaining = sum(_stage_hours(s, done)[1] for s in stages)
    left_points = sum(_stage_hours(s, done)[2] for s in stages)

    target = as_date(ls.get("target_date"))
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
    `behind` 只看**清单里写着的** deadline（人手填的或采纳提议时带下来的），
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


def done_ids(points: dict[str, str]) -> set[str]:
    """已经建出来、不再占用时间预算的点。只有「未建」和「只有壳」还欠着工时。"""
    return {nid for nid, m in points.items() if m not in (UNBUILT, SHELL)}


def all_schedules(doc: dict, progress: dict, today: dt.date | None = None) -> dict:
    """每份清单一份时间账，按项目分组。"""
    out = {}
    for pid, pr in (doc.get("projects") or {}).items():
        parts = (progress.get(pid) or {}).get("lists") or []
        rows = []
        for i, ls in enumerate(lists_of(pr)):
            points = parts[i]["points"] if i < len(parts) else {}
            rows.append(schedule_of(ls, done_ids(points), pr.get("weekly_hours"), today))
        out[pid] = {"lists": rows}
    return out
