"""对话式教练（阶段 12）：聊着学，学完一键入库。

**为什么要有这一层**：面板式的学习闭环（计划 → 今日 → 建节点 → 写正文 → 出题）每一步都对，
但启动成本高——想学一个点要在几个面板之间跳。聊天把这些收成一句话。
图仍然是产物，聊天只是入口：**对话是过程，知识住在 md 里**（F10.7）。

三条边界，一条都不松：

1. **不写 Markdown。** 模型只能 `propose_changes`，出的是一张预览卡（和 `/api/changes`
   的 dry_run 同一份代码），我看过 diff 点了才落盘（4.4「Agent 只提议不越权」）。
2. **复习判定只许降级**（F10.5）：对话里看出答错可以直接记「忘了」，看出答对**不准**记「记得」。
   判严了最多多复习一次，判宽了会让一个其实已经忘了的点从此不再出现。
3. **工具走文本协议，不用原生 function calling。** 默认 provider 是 `claude -p`（子进程），
   它没有结构化工具接口；文本协议是 claude-cli / anthropic / openai 三种后端唯一都通的路，
   也和现有 prompt「只输出 JSON」的做法同源。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import queue
import re

from difflib import SequenceMatcher
import threading

from pathlib import Path

from . import curation
from .contracts import ChatRequest, QuizRequest
from .index_service import current_index
from .levels import fragment as level_fragment
from .llm_call import chat as llm_chat
from .paths import core
from .projects import read as read_projects
from .quiz import generate as generate_quiz


log = logging.getLogger(__name__)

# 一条消息里最多连续调几次工具。**放宽到 8**：像"建一个项目"这种活儿本来就要走好几步——
# 看已有项目 → 搜图谱 → 读几个节点 → 提议项目 → 拆点，4 步根本走不完，
# 到头来只能让人自己去面板上接着干。同参调用会被短路（`seen_calls`），所以放宽不会变成原地打转。
MAX_STEPS = 8
# 往回带多少上下文。两道闸都要过：条数和字数。
# 字数那道是必须的——20 条里夹着几段长正文，光看条数会把 prompt 撑爆。
MAX_MESSAGES = 60
MAX_HISTORY_CHARS = 24000
SEARCH_TOP = 8
BODY_CHARS = 1200
_PROMPTS: dict[str, str] = {}

# ```knowrary {...}``` —— 非贪婪，只认第一个块（prompt 里要求一次一个工具）
_TOOL_RE = re.compile(r"```knowrary\s*(\{.*?\})\s*```", re.S)


class ChatRejected(Exception):
    """请求本身不合法。"""


# ---------------------------------------------------------------- 工具

def _overview(vault: Path) -> str:
    index = current_index(vault)
    real = [n for n in index["nodes"] if not n.get("virtual")]
    fields: dict[str, int] = {}
    for n in real:
        if n.get("field"):
            fields[n["field"]] = fields.get(n["field"], 0) + 1
    shells = sum(1 for n in real if n.get("stub"))
    tops = "、".join(f"{k}({v})" for k, v in sorted(fields.items(), key=lambda kv: -kv[1])[:8])
    return (f"{len(real)} 个节点、{len(index['edges'])} 条关系，其中 {shells} 个还只是壳。"
            f"\n领域分布：{tops or '（还没有领域）'}"
            f"\n\n**我已经有的项目**（建新项目前先看这里）：\n{_projects_brief(vault)}")


def _projects_brief(vault: Path) -> str:
    """已有项目一行一个：名字、id、每份清单的口径和点数。

    不喂这个，模型建新项目时就不知道你已经有什么——
    "我有 Transformer 项目了，又建一个 MHA"这种事它看不见，也就提不出
    "要不要在 Transformer 里加一份清单"。同 `known_points` 那条老规矩（F10.3b）。
    """
    doc = core.load_projects(vault)
    rows = []
    for pid, pr in (doc.get("projects") or {}).items():
        parts = []
        for ls in core.lists_of(pr):
            n = len(core.stage_points(ls.get("stages") or []))
            parts.append(f"{ls.get('name') or ls.get('kind')}[{ls.get('kind')}] {n} 个点")
        goal = next((ls.get("goal") for ls in core.lists_of(pr) if ls.get("goal")), "")
        rows.append(f"- `{pid}`「{pr.get('name') or pid}」：{'；'.join(parts) or '还没有清单'}"
                    + (f"　目标：{goal[:40]}" if goal else ""))
    return "\n".join(rows) or "（一个项目都还没有）"


def _tool_search(vault: Path, args: dict) -> tuple[str, dict]:
    """搜节点，**也搜项目清单里还没建出来的点**。

    只搜索引是不够的：一个项目常常 40 个点里 39 个还没建（它们只活在 projects.json 里），
    这时问"图里有没有多头注意力"会得到零命中，而 Transformer 项目里明明就列着它——
    于是又建一个重复的。计划里的点和已建节点是同一件事的两个阶段，搜的时候不该只看后一半。
    """
    q = str(args.get("q") or "").strip()
    if not q:
        return "没给关键字，搜不了。", {}
    limit = min(int(args.get("limit") or SEARCH_TOP), 20)
    index = current_index(vault)
    hits = []
    for n in index["nodes"]:
        if n.get("virtual"):
            continue
        hay = f"{n['id']} {n.get('name') or ''} {n.get('desc') or ''}"
        if q.lower() in hay.lower():
            hits.append(n)
    hits.sort(key=lambda n: -(n.get("degree") or 0))
    rows = [{"id": n["id"], "name": n.get("name"), "desc": n.get("desc"),
             "field": n.get("field"), "状态": "只有壳" if n.get("stub") else "已建"}
            for n in hits[:limit]]

    built = {n["id"] for n in index["nodes"] if not n.get("virtual")}
    planned = []
    for pid, pr in (core.load_projects(vault).get("projects") or {}).items():
        for ls in core.lists_of(pr):
            for stage in ls.get("stages") or []:
                for pt in stage.get("points") or []:
                    nid = pt.get("id") or ""
                    if nid in built or q.lower() not in f"{nid} {pt.get('name') or ''}".lower():
                        continue
                    planned.append({"id": nid, "name": pt.get("name"), "why": pt.get("why"),
                                    "状态": "计划里有、还没建",
                                    "在哪个项目": f"{pr.get('name') or pid}·{ls.get('name') or ls.get('kind')}"})
    planned = planned[:limit]

    if not rows and not planned:
        return f"图里和计划里都没有和「{q}」匹配的东西。", {"hits": 0}
    out = {}
    if rows:
        out["已建的节点"] = rows
    if planned:
        out["计划里列过、还没建出来的点"] = planned
    return json.dumps(out, ensure_ascii=False), {"hits": len(rows) + len(planned)}


def _tool_read(vault: Path, args: dict) -> tuple[str, dict]:
    nid = str(args.get("id") or "").strip()
    index = current_index(vault)
    meta = next((n for n in index["nodes"] if n["id"] == nid), None)
    if meta is None:
        return f"图里没有 `{nid}` 这个节点。", {}
    if meta.get("virtual") or not meta.get("path"):
        return f"`{nid}` 只是被别的节点引用的占位，还没有 md 文件。", {}
    try:
        raw = core.read(vault / meta["path"])
    except OSError as exc:
        return f"读不到 `{nid}` 的文件：{exc}", {}
    edges = {e["id"]: e for e in index["edges"]}
    rel = [f"{edges[i]['type']} → {edges[i]['target']}" for i in meta.get("out", []) if i in edges]
    rel += [f"{edges[i]['source']} → {edges[i]['type']} 本节点" for i in meta.get("in", []) if i in edges]
    return f"{raw[:BODY_CHARS]}\n\n（关系：{'；'.join(rel) or '还没有'}）", {"id": nid}


def _tool_overview(vault: Path, args: dict) -> tuple[str, dict]:
    return _overview(vault), {}


def _tool_today(vault: Path, args: dict) -> tuple[str, dict]:
    """今日清单。**跟着当前项目走**——在某个项目里聊天，就该看这个项目的今天。

    `today["projects"]`（一期把 `plans` 改成了这个名字；这里曾经漏改，
    表现是工具直接抛 KeyError，模型只能说一句"today 挂了"接着聊）。
    """
    today = curation.coach_today(vault, args.get("_project") or None).model_dump()
    rows = [{"类型": it["kind"], "id": it["id"], "说明": it.get("detail")} for it in today["items"]]
    out = {"清单": rows, "项目进度": today["projects"], "大概要花": f"{today['estimate_hours']} 小时"}
    if any((today.get("elsewhere") or {}).values()):
        out["别的项目还欠着"] = today["elsewhere"]
    return json.dumps(out, ensure_ascii=False), {"items": len(rows)}


def _tool_projects(vault: Path, args: dict) -> tuple[str, dict]:
    """项目、清单、进度与时间账。一个项目可能有好几份清单，逐份摊开——
    并成一条会把"主线学到哪了"和"面试准备到哪了"混成一个数字。"""
    data = read_projects(vault)
    out = {}
    for pid, project in data.doc.projects.items():
        prog = data.progress.get(pid) or {}
        scheds = (data.schedules.get(pid) or {}).get("lists") or []
        rows = []
        for i, ls in enumerate(project.lists):
            part = (prog.get("lists") or [{}])[i] if i < len(prog.get("lists") or []) else {}
            sched = scheds[i] if i < len(scheds) else {}
            rows.append({"清单": ls.name, "口径": ls.kind, "目标": ls.goal,
                         "目标日期": ls.target_date,
                         "进度": f"{part.get('built', 0)}/{part.get('total', 0)}",
                         "时间账": {"还剩": f"{sched.get('remaining_hours')} 小时",
                                    "判定": sched.get("verdict"),
                                    "现实日期": sched.get("suggested_target_date"),
                                    "落后": sched.get("behind")},
                         "各档": part.get("counts")})
        out[project.name or pid] = {"id": pid, "每周投入": project.weekly_hours, "清单": rows}
    return json.dumps(out, ensure_ascii=False) or "还没有项目。", {"projects": len(out)}


def _tool_quiz(vault: Path, args: dict) -> tuple[str, dict]:
    ids = [str(i) for i in (args.get("node_ids") or []) if str(i).strip()]
    if not ids:
        return "没给节点 id，出不了题。", {}
    quiz = generate_quiz(vault, QuizRequest(node_ids=ids, count=min(int(args.get("count") or 3), 10)))
    payload = quiz.model_dump()
    return json.dumps(payload["questions"], ensure_ascii=False), {"questions": len(payload["questions"]),
                                                                  "quiz": payload}


def _tool_review(vault: Path, args: dict) -> tuple[str, dict]:
    """只许降级（F10.5）：对话里判「忘了」可以直接记，判「记得」一律退回让人自己点。"""
    nid = str(args.get("id") or "").strip()
    grade = str(args.get("grade") or "").strip()
    if grade != "忘了":
        return ("只有「忘了」可以由你来记。你觉得我答对了的话，得我自己点一下"
                "——判宽了会让一个我其实已经忘了的点从此不再出现。"), {}
    try:
        done = curation.mark_reviewed(vault, nid, grade="忘了")
    except curation.PlaceRejected as exc:
        return str(exc), {}
    return (f"已记一次「忘了」：{nid} 明天再考（间隔序号归 {done.step}）。",
            {"id": nid, "grade": "忘了", "next_due": done.next_due})


def _tool_propose_points(vault: Path, args: dict) -> tuple[str, dict]:
    """把一份清单拆成知识点。**走的是面板上「让 AI 拆一份」同一条链路**——
    同一套模板、同一份时间账、同一个"别的项目已经列过"的标注，只是入口在对话里。

    这一步以前只能去面板点，于是"帮我建个项目"在对话里做到一半就断了。
    """
    from .contracts import PlanProposeRequest
    from .projects import propose as propose_points

    pid = str(args.get("project") or "").strip()
    doc = core.load_projects(vault)
    project = (doc.get("projects") or {}).get(pid)
    if project is None:
        return (f"没有 `{pid}` 这个项目。先用 `propose_project` 提议建一个，"
                f"等他点了「创建」再回来拆点。"), {}
    lists = core.lists_of(project)
    want = str(args.get("list") or "").strip()
    idx = next((i for i, ls in enumerate(lists) if (ls.get("name") or "") == want), 0 if lists else -1)
    if idx < 0:
        return f"项目「{project.get('name') or pid}」下面还没有清单。", {}
    ls = lists[idx]

    req = PlanProposeRequest(
        goal=str(args.get("goal") or ls.get("goal") or project.get("name") or pid)[:8000],
        plan_name=project.get("name") or pid,
        kind=ls.get("kind") if ls.get("kind") in core.KINDS else "学习",
        coach=str(ls.get("coach") or ""),
        target_date=ls.get("target_date"),
        weekly_hours=int(project.get("weekly_hours") or 7),
        mode="速学" if args.get("mode") == "速学" else "标准",
        project=pid,
        known_points=[p for p in core.stage_points(ls.get("stages") or [])][:200],
    )
    proposal = propose_points(vault, req).model_dump()
    n = sum(len(st["points"]) for st in proposal["stages"])
    if not n:
        return "这次没拆出点来——把目标说具体一点我再试。", {}
    dupes = len(proposal.get("in_projects") or {})
    extra = f"其中 {dupes} 个别的项目里也列过（重叠是合法的，掌握度还是同一个）。" if dupes else ""
    return (f"拆出 {n} 个点，卡片摆出来了。{extra}**还没进清单**，等他点「采纳」。"
            f"别在同一条消息里又拆一遍。"),\
           {"points": {"project": pid, "project_name": project.get("name") or pid,
                       "list": idx, "list_name": ls.get("name") or "", **proposal}}


def _tool_propose_project(vault: Path, args: dict) -> tuple[str, dict]:
    """提议建一个项目 / 往现有项目里加一份清单。**不写盘**，出一张卡等人点。

    和 `propose_changes` 同一个范式：模型只会提议，落盘由人按下（4.4）。
    项目 id 只允许 ASCII——它会成为对话留档的目录名和项目画布的文件名。
    """
    pid = str(args.get("id") or "").strip()
    if not core.ID_OK.match(pid):
        return ("项目 id 只能用 ASCII 字母、数字、`_`、`-`（它会成为文件名和目录名）。"
                "中文放 name 字段里。"), {}
    doc = core.load_projects(vault)
    exists = (doc.get("projects") or {}).get(pid)
    near = _near_projects(doc, str(args.get("name") or pid), args.get("lists") or [])
    lists = []
    for ls in args.get("lists") or []:
        if not isinstance(ls, dict):
            continue
        kind = ls.get("kind") if ls.get("kind") in core.KINDS else "学习"
        lists.append({"kind": kind, "name": str(ls.get("name") or f"{kind}清单")[:120],
                      "goal": str(ls.get("goal") or "")[:8000],
                      "coach": str(ls.get("coach") or "")[:120],
                      "field": str(ls.get("field") or "")[:120],
                      "target_date": ls.get("target_date"), "stages": []})
    if not lists and not exists:
        lists = [{"kind": "学习", "name": "主线", "goal": str(args.get("goal") or ""),
                  "coach": "", "field": "", "target_date": None, "stages": []}]
    card = {"id": pid, "action": "update" if exists else "create", "near": near,
            "name": str(args.get("name") or pid)[:120],
            "field": str(args.get("field") or "")[:120],
            "weekly_hours": int(args.get("weekly_hours") or 7),
            "daily_quota": int(args.get("daily_quota") or 2),
            "lists": lists}
    what = (f"往「{exists.get('name') or pid}」里加 {len(lists)} 份清单" if exists
            else f"新建项目「{card['name']}」")
    hint = ""
    if near and not exists:
        names = "、".join(f"「{n['name']}」" for n in near)
        hint = (f"\n\n**注意：他已经有{names}，看起来和这个是一回事或者是它的一部分。**"
                f"先问一句：是要独立一个项目，还是在那个项目里加一份清单？"
                f"（项目之间重叠是合法的——同一个点属于两个项目，掌握度还是同一个——"
                f"但没必要的话别平白多一个项目。）")
    return (f"{what}的卡片已经摆在他面前了。**还没建**，等他点「创建」。"
            f"建完之后清单还是空的——要填点，让他点「让 AI 拆一份」，或者你继续问清楚目标再提议。"
            + hint), {"project": card}


def _near_projects(doc: dict, name: str, lists: list) -> list[dict]:
    """名字或目标跟已有项目撞车的，列出来。**服务端算，不靠模型自觉**。

    只提示不阻止：项目是视角，重叠本来就合法（重构方案 §1）。
    但"又建了一个其实是子集的项目"这件事，人得在按下创建之前看见。
    """
    goals = " ".join(str(ls.get("goal") or "") for ls in lists if isinstance(ls, dict))
    hay = f"{name} {goals}"
    out = []
    for pid, pr in (doc.get("projects") or {}).items():
        other = pr.get("name") or pid
        other_goal = next((ls.get("goal") for ls in core.lists_of(pr) if ls.get("goal")), "")
        ratio = SequenceMatcher(None, name, other).ratio()
        hit = (ratio >= 0.5 or other in hay or (name and name in f"{other} {other_goal}")
               or (other_goal and any(w and w in hay for w in other_goal.split()[:6])))
        if hit:
            out.append({"id": pid, "name": other,
                        "why": f"名字接近（{other}）" if ratio >= 0.5 else "目标里提到了同样的东西"})
    return out[:3]


def _tool_propose(vault: Path, args: dict) -> tuple[str, dict]:
    """提议入库：算出 diff 给人看，**不写盘**。"""
    changes = args.get("changes")
    if not isinstance(changes, list) or not changes:
        return "没给 changes，没什么可提议的。", {}
    index = current_index(vault)
    try:
        files = curation.preview(vault, changes, index)
    except (core.ChangeRejected, core.WriteConflict) as exc:
        return f"这组变更过不了校验：{exc}", {}
    # 在某个项目下聊天时，新建的点顺手补进这个项目的清单——**服务端算，不问模型**。
    # 不补的话：节点建出来了、项目进度却不认它（清单只按 id 引用，没列就不算数）。
    # 仍然是同一次点击：写 md 和加进清单一起落，人看得见卡上写着要加到哪。
    born = [str(c.get("source") or "") for c in changes
            if isinstance(c, dict) and c.get("type") == "create_node" and c.get("source")]
    into = _into_list(vault, args.get("_project"), born)
    card = {"changes": changes, "base_revision": index["revision"],
            "files": [f.model_dump() for f in files], "into": into}
    tail = (f"写入时会顺手把 {'、'.join(into['points'])} 加进「{into['project_name']}·{into['list_name']}」清单。"
            if into else "")
    return (f"变更卡已经摆在他面前了（{len(files)} 个文件）。**还没有写盘**，"
            f"等他点「写入」。{tail}你不要再说已经存好了。"), {"card": card}


def _into_list(vault: Path, project: str | None, born: list[str]) -> dict | None:
    """新建的点该补进哪份清单：当前项目的第一份。已经列过的不重复加。"""
    if not project or not born:
        return None
    pr = (core.load_projects(vault).get("projects") or {}).get(project)
    lists = core.lists_of(pr or {})
    if not lists:
        return None
    listed = set(core.point_ids(core.load_projects(vault), project))
    fresh = [nid for nid in born if nid not in listed]
    if not fresh:
        return None
    return {"project": project, "project_name": pr.get("name") or project,
            "list": 0, "list_name": lists[0].get("name") or lists[0].get("kind") or "清单",
            "points": fresh}


# 工具表：名字 → (实现, 给模型看的一行说明)。口径只决定**给它看见哪几行**——
# 表里没有的调了会被退回去（`run` 里那句"没有 xx 这个工具"）。
TOOL_DOC = {
    "search_nodes": "`q`、`limit`（默认 8） | 按关键字找节点。**讲任何一个概念之前先搜一下**，看我图里有没有",
    "read_node": "`id` | 读某个节点的正文和关系。要引用我已有的笔记就先读它，别凭印象说「你笔记里写过」",
    "overview": "无 | 图谱概况：节点数、领域、还有多少壳",
    "today": "无 | 今日清单：错题 / 到期复习 / 计划里还没建的点",
    "projects": "无 | 我的项目、清单、进度和时间账（还剩多少、来不来得及）",
    "quiz": "`node_ids`、`count`（默认 3） | 按这些节点出题考我",
    "record_review": "`id`、`grade` | 记一次复习。**只能记「忘了」**，见下面的纪律",
    "propose_changes": "`changes` | 提议把学到的东西写进图谱。**只是提议**，会变成一张卡片等我点「写入」",
    "propose_project": "`id`、`name`、`field`、`lists` | 提议建一个项目，或往现有项目里加一份清单。同样只是卡片",
    "propose_points": "`project`、`list`、`goal` | 把某个项目的某份清单拆成知识点。走面板上「让 AI 拆一份」同一条链路，同样出卡片",
}

READ_ONLY = ("search_nodes", "read_node", "overview")

# 两个"要写东西"的工具得多给一段格式说明。**跟着白名单一起渲染**——
# 写死在基底里的话，面试口径的说明书上会白纸黑字写着一个它调不动的工具。
FORMAT_DOC = {
    "propose_project": """建项目长这样（`id` 只能 ASCII，它会成为文件名；中文放 `name`）：

```knowrary
{"tool": "propose_project", "args": {
  "id": "nlp", "name": "NLP 方向", "field": "AI", "weekly_hours": 7,
  "lists": [{"kind": "学习", "name": "主线", "goal": "三个月吃透 Transformer 到 RLHF",
             "target_date": "2026-12-15"}]}}
```

一个项目下可以有好几份清单，`kind` 决定怎么拆：`学习`（按依赖顺序）/ `面试`（按会怎么问）/
`领域`（按覆盖度铺地图）。**先建项目，再拆点**——拆点是另一步，别在同一条消息里全干完。""",
    "propose_changes": """`propose_changes` 的 `changes` 和图谱的 ChangeSet 同一个形状，三种改动：

```knowrary
{"tool": "propose_changes", "args": {"changes": [
  {"type": "create_node", "source": "NPU", "path": "nodes/02-计算机硬件/NPU.md",
   "fields": {"name": "NPU", "field": "计算机系统", "layer": "硬件", "year": 2017,
              "desc": "一句话摘要"},
   "body": "正文：讲清楚这个概念"},
  {"type": "update_body", "source": "GPU", "body": "（这个节点的**完整**新正文）"},
  {"type": "add_edge", "source": "NPU", "relation": "对比", "target": "GPU"}
]}}
```

**`update_body` 会整段替换正文，不是追加。** 所以改之前**必须先 `read_node`**，
把原文一字不落地带上，再把这次聊出来的东西补进去——直接写一段新的会把我以前记的东西抹掉。
改哪儿也要克制：只补真正聊清楚了的那一点，别顺手重写整篇。

`create_node` 的 `fields` 里带上 `layer`（`理论 / 硬件 / 体系结构 / 汇编接口 / 系统软件 /
高级语言 / AI应用`）和 `year`（有确切年份的技术才填），这两个字段决定它在历史视图里站哪儿。""",
}

# 三档口径。**这不是三个 agent**，是同一套链路上的三套提示词 + 三份工具白名单
# （同 F10.3b「拆解口径不同 ≠ 架构不同」）。多开一套运行时要付会话路由、上下文传递、
# 状态同步和翻倍 token 的代价，而收益只是换一套系统提示词。
STANCES = {
    "教练": {"file": "chat-coach", "tools": tuple(TOOL_DOC)},
    # 面试不给入库和建项目：**边考边改图谱等于开卷**，也会把面试节奏打断
    "面试": {"file": "chat-interview", "tools": (*READ_ONLY, "quiz", "record_review")},
    # 聊天只想把事情讲清楚：不给调度类工具（today/projects），免得每句话都被催进度
    "聊天": {"file": "chat-talk", "tools": (*READ_ONLY, "propose_changes")},
}
DEFAULT_STANCE = "教练"

TOOLS = {
    "search_nodes": _tool_search,
    "read_node": _tool_read,
    "overview": _tool_overview,
    "today": _tool_today,
    "projects": _tool_projects,
    "quiz": _tool_quiz,
    "record_review": _tool_review,
    "propose_changes": _tool_propose,
    "propose_project": _tool_propose_project,
    "propose_points": _tool_propose_points,
}


# ---------------------------------------------------------------- 对话留档（F10.7）

SCRATCH = "_scratch"     # 没绑项目的对话落这里：「对话」是默认入口，冷启动时一个项目都还没有
TITLE_CHARS = 28         # 会话列表上标题截多长：第一句话就是最好的标题


def chat_log_path(vault: Path, project: str | None = None, today: dt.date | None = None) -> Path:
    """按项目分目录、按月分文件。项目 id 只允许 ASCII（core.ID_OK 挡住），
    所以可以直接当目录名；认不出的一律归到 `_scratch`，绝不让它拼出路径。"""
    today = today or dt.date.today()
    slot = project if project and core.ID_OK.match(project) else SCRATCH
    return vault / ".knowrary" / "chat" / slot / f"{today:%Y-%m}.jsonl"


def append_log(vault: Path, role: str, text: str, node_ids: list[str] | None = None,
               project: str | None = None, session: str | None = None,
               stance: str | None = None, trace: list[str] | None = None) -> None:
    """一行一轮，按月分文件。**不建库、不切分、不做 embedding**（F10.7）：
    对话是过程不是知识，检索系统已经存在，就是那张图。找旧对话用 grep。

    `session` 只是行上的一个标签——**不建会话表、不存会话元数据**。
    会话列表是从这些行里聚合出来的，和进度、时间账一样是派生的（不落第二份真值）。
    """
    path = chat_log_path(vault, project)
    row = {"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "role": role, "text": text, "node_ids": node_ids or [], "session": session or "",
           "stance": stance or ""}
    # 过程（"我先查一下…"、工具报错）另存一栏：它和答案混在一行里，回看时得整段重读一遍
    if trace:
        row["trace"] = trace
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:                       # 留档是旁路，坏了不能拖垮对话
        log.warning("对话没记上：%s", exc)


def _read_rows(vault: Path, project: str | None, months: int = 2) -> list[dict]:
    """把最近几个月的留档按时间顺序读出来。跨月的第一天只剩很短一截，所以默认往前翻两个月。"""
    rows: list[dict] = []
    today = dt.date.today()
    day = today
    for _ in range(max(1, months)):
        path = chat_log_path(vault, project, day)
        if path.exists():
            got = []
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []
            for line in lines:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("role") in ("user", "assistant") and (row.get("text") or "").strip():
                    got.append(row)
            rows = got + rows
        day = day.replace(day=1) - dt.timedelta(days=1)
    return rows


def titles_path(vault: Path, project: str | None = None) -> Path:
    slot = project if project and core.ID_OK.match(project) else SCRATCH
    return vault / ".knowrary" / "chat" / slot / "titles.json"


def load_titles(vault: Path, project: str | None = None) -> dict:
    """人手改过的会话名。**这不是会话表**，是一张「id → 我给它起的名字」的贴纸：
    没改过的会话在这里一行都没有，删掉这个文件也只是回到自动取的标题。"""
    path = titles_path(vault, project)
    if not path.exists():
        return {}
    try:
        data = core.load_json(path)
    except (ValueError, OSError):
        return {}
    return {str(k): str(v)[:TITLE_CHARS * 2] for k, v in data.items()} if isinstance(data, dict) else {}


def rename_session(vault: Path, session: str, title: str, project: str | None = None) -> str:
    """给一段对话改名。留空 = 撕掉贴纸，回到自动取的标题。"""
    titles = load_titles(vault, project)
    title = " ".join((title or "").split())[:TITLE_CHARS * 2]
    if title:
        titles[session] = title
    else:
        titles.pop(session, None)
    path = titles_path(vault, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    core.write_json_atomic(path, titles)
    return title


def sessions(vault: Path, project: str | None = None) -> list[dict]:
    """会话列表，**从留档行聚合出来**，不存第二份。

    一条会话 = 一串带同一个 `session` 标签的行。标题取第一句我说的话——
    让人自己给会话起名字，十有八九是不起，然后过两天谁也认不出哪条是哪条。
    没有标签的老行归到一个 `legacy` 段里，不丢。
    """
    buckets: dict[str, dict] = {}
    for i, row in enumerate(_read_rows(vault, project)):
        sid = row.get("session") or "legacy"
        b = buckets.setdefault(sid, {"id": sid, "started": row.get("ts"), "last": row.get("ts"),
                                     "turns": 0, "title": "", "seq": i})
        b["last"] = row.get("ts") or b["last"]
        b["seq"] = i                    # 按**留档里的出现顺序**排，不按时间戳
        b["turns"] += 1
        if not b["title"] and row.get("role") == "user":
            text = " ".join((row.get("text") or "").split())
            b["title"] = text[:TITLE_CHARS] + ("…" if len(text) > TITLE_CHARS else "")
    # 时间戳精确到秒，一来一回常常同秒；同秒时按 ts 排会退化成不稳定顺序，
    # 而追加顺序本身就是真正的"最近"。
    out = sorted(buckets.values(), key=lambda b: b["seq"], reverse=True)
    mine = load_titles(vault, project)
    for b in out:
        b["auto"] = b["title"] or "（没说什么）"      # 自动取的那个：改名框里当占位符
        b["title"] = mine.get(b["id"]) or b["auto"]
        b["renamed"] = b["id"] in mine
    return out


def history(vault: Path, project: str | None = None, limit: int = 40,
            session: str | None = None) -> list[dict]:
    """把某一段对话读回来，让刷新页面能接着聊。

    **这不是"会话管理"，是"别把上下文弄丢"**：会话状态在前端，刷一下原本就没了，
    而留档一直都在。不给 `session` 就取**最近那一段**——绝大多数时候人想接着的就是它。
    """
    rows = _read_rows(vault, project)
    if session:
        rows = [r for r in rows if (r.get("session") or "legacy") == session]
    elif rows:
        newest = rows[-1].get("session") or "legacy"
        rows = [r for r in rows if (r.get("session") or "legacy") == newest]
    return [{"role": r["role"], "content": r["text"], "node_ids": r.get("node_ids") or [],
             "trace": r.get("trace") or []}
            for r in rows][-limit:]


# ---------------------------------------------------------------- 编排

COACH_FILE = "coach.md"            # 全局侧写
COACH_DIR = "coaches"              # 项目级：.knowrary/coaches/<项目 id>.md


def _me_brief(vault: Path, project: str | None) -> str:
    """"我是谁、要什么口气、笔记怎么写"——**人可以改的那部分，放在 vault 里，不在代码里**。

    分界是故意的：`prompts/chat*.md` 是**程序行为**（工具协议、纪律、卡片规则），
    改了要跟代码一起测；`.knowrary/coach.md` 是**你的数据**，随便改，坏了也只影响口气。
    这是"md 是真值源、程序是程序"那条纪律的延伸。

    **每次现读，不缓存**：你会在服务跑着的时候改它，改完下一句话就该生效。
    项目级接在全局之后——后面的话语气更近，自然覆盖前面的。
    """
    parts = []
    for path in (vault / ".knowrary" / COACH_FILE,
                 (vault / ".knowrary" / COACH_DIR / f"{project}.md") if project else None):
        if path is None or not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            parts.append(f"（来自 `.knowrary/{path.relative_to(vault / '.knowrary')}`）\n{text}")
    if project:
        pr = (core.load_projects(vault).get("projects") or {}).get(project) or {}
        hints = [ls.get("coach") for ls in core.lists_of(pr) if (ls.get("coach") or "").strip()]
        if hints:
            parts.append("这个项目的教练方向：" + "、".join(dict.fromkeys(hints)))
    return "\n\n".join(parts) or (
        f"（还没写。想让我知道点什么——你是谁、要什么口气、笔记想长成什么样——"
        f"就写进 `.knowrary/{COACH_FILE}`；某个项目单独的规矩写 "
        f"`.knowrary/{COACH_DIR}/<项目 id>.md`，它会盖过全局那份。）")


def _relations_brief(vault: Path) -> str:
    """登记过的关系类型，按族列出来。

    §8 契约 2 早就定了要给，一直漏着——后果是模型只能**猜类型名**：
    真实对话里它写过 `提出者::`，那个类型没登记，解析时落进「弱关联」还带一条警告。
    "发展方向"这件事全靠 `演化` 那一族（演化为 / 源自 / 被激活），不给表就连不对。
    """
    try:
        table = core.load_relation_types(vault)
    except Exception:                            # 表读不出来不该拖垮对话
        return "（关系类型表读不到，连边时用「相关」最稳）"
    by_family: dict[str, list[str]] = {}
    for name, spec in (table.types or {}).items():
        # canonical 的是"会被归一掉的反向写法"，给模型看正向那个就够了
        if spec.get("canonical"):
            continue
        by_family.setdefault(spec.get("family") or table.default_family, []).append(name)
    if not by_family:
        return "（关系类型表是空的）"
    lines = [f"- **{fam}**：{'、'.join(f'`{t}`' for t in by_family[fam])}"
             for fam in table.families if by_family.get(fam)]
    return "\n".join(lines)


def _prompt_file(name: str) -> str:
    if name not in _PROMPTS:
        path = Path(__file__).resolve().parents[1] / "tools" / "knowrary" / "prompts" / f"{name}.md"
        _PROMPTS[name] = path.read_text(encoding="utf-8")
    return _PROMPTS[name]


def stance_of(name: str | None) -> dict:
    return STANCES.get(name or DEFAULT_STANCE, STANCES[DEFAULT_STANCE])


def _project_level(vault: Path, project: str | None) -> str:
    """当前项目的难度档。不绑项目（全局对话）就用默认档。

    为什么按项目而不按清单：一次对话不属于某一份清单，但一定属于某个项目的视角。
    清单级的覆盖只影响出题与拆点，那两件事本来就是从某一份清单发起的。
    """
    if not project:
        return core.DEFAULT_LEVEL
    doc = core.load_projects(vault)
    return core.level_of((doc.get("projects") or {}).get(project))


def _system_prompt(vault: Path, stance: str | None, project: str | None = None) -> str:
    """基底一份 + 口径一份。工具表从白名单渲染——**说明书和实际权限是同一份数据**，
    两边各写一遍迟早对不上（"表里写着能用、调了却说没有"是最让人发火的那种 bug）。"""
    conf = stance_of(stance)
    body = _prompt_file(conf["file"])
    intro, _, rules = body.partition("## 规矩")
    table = "\n".join(f"| `{t}` | {TOOL_DOC[t]} |" for t in conf["tools"] if t in TOOL_DOC)
    formats = "\n\n".join(FORMAT_DOC[t] for t in conf["tools"] if t in FORMAT_DOC)
    return (_prompt_file("chat")
            .replace("{{level}}", level_fragment(_project_level(vault, project), "chat"))
            .replace("{{overview}}", _overview(vault))
            .replace("{{tools}}", table)
            .replace("{{formats}}", formats)
            .replace("{{relations}}", _relations_brief(vault))
            .replace("{{me}}", _me_brief(vault, project))
            .replace("{{stance_intro}}", intro.strip())
            .replace("{{stance_rules}}", rules.strip() or "（没有额外规矩）"))


def strip_tools(text: str) -> str:
    """把工具块从要给人看的文本里摘掉。

    流式增量里也会带着这个块，前端按同一条正则过滤——**两边用同一个形状**，
    否则会出现"聊天记录里没有、屏幕上闪过一段 JSON"这种事。
    """
    return _TOOL_RE.sub("", text or "").strip()


# 教练每轮末尾问的那个检验问题。用一个轻量围栏标出来，而不是让它调工具——
# 调工具要多一个来回（多一次计费、多等几秒），而这件事没有任何需要服务端算的东西。
_CHECK_RE = re.compile(r"```check\s*\n(.*?)```", re.S)


def pull_checks(text: str, default_points: list[str]) -> tuple[str, list[dict]]:
    """把 ```check 块从答案里摘出来，返回 (还给人看的文本, 题目列表)。

    题干仍然留在正文里——那句问话本来就是对话的一部分，摘掉会让最后一段没头没尾；
    摘掉的只是围栏和「考点:」那一行。
    """
    out: list[dict] = []

    def take(m: re.Match) -> str:
        body = m.group(1).strip()
        stem_lines, points = [], []
        for line in body.splitlines():
            hit = re.match(r"^\s*(?:考点|points)\s*[:：]\s*(.+)$", line)
            if hit:
                points += [p.strip() for p in re.split(r"[,，、\s]+", hit.group(1)) if p.strip()]
            else:
                stem_lines.append(line)
        stem = "\n".join(stem_lines).strip()
        if stem:
            out.append({"stem": stem, "points": points or list(default_points)})
        return stem

    return _CHECK_RE.sub(take, text or "").strip(), out


def _fit_history(msgs: list[dict]) -> tuple[list[dict], int]:
    """按条数和字数两道闸裁上下文，返回 (留下的, 丢掉几轮)。

    **从最早的开始丢**，末尾那几轮一定留着——正在说的话比开头重要。
    """
    kept = msgs[-MAX_MESSAGES:]
    total = sum(len(m.get("content") or "") for m in kept)
    while len(kept) > 1 and total > MAX_HISTORY_CHARS:
        total -= len(kept[0].get("content") or "")
        kept = kept[1:]
    return kept, len(msgs) - len(kept)


def _parse_tool(text: str) -> tuple[str, dict] | None:
    m = _TOOL_RE.search(text or "")
    if not m:
        return None
    try:
        call = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    name = str(call.get("tool") or "")
    return (name, call.get("args") or {}) if name else None


def _stream(vault: Path, messages: list[dict], op: str = "chat"):
    """在后台线程里跑一次 LLM 调用，把增量从队列里取出来往外 yield。

    生成器里没法从回调 yield，所以只能用队列过一道。
    """
    q: queue.Queue = queue.Queue()
    box: dict = {}

    def work() -> None:
        try:
            box["text"], box["usage"] = llm_chat(vault, "learn", messages, op=op,
                                                 on_delta=lambda t: q.put(t))
        except BaseException as exc:             # SystemExit 是 llm_backend 的报错方式
            box["error"] = str(exc)
        finally:
            q.put(None)

    th = threading.Thread(target=work, daemon=True)
    th.start()
    while True:
        piece = q.get()
        if piece is None:
            break
        yield {"type": "delta", "text": piece}
    th.join()
    if box.get("error"):
        raise RuntimeError(box["error"])
    yield {"type": "_done", "text": box.get("text") or "", "usage": box.get("usage") or {}}


def run(vault: Path, req: ChatRequest):
    """一条消息的完整回合：可能夹着几次工具调用。逐个 yield 事件给 SSE。

    这一轮要是炸了（模型不通、配置写错），**留档里也要留个记号**：
    只记提问不记结果的话，失败五次就攒出五条没人答的问题，
    下次接着聊时全被读回去当上下文。
    """
    try:
        yield from _run(vault, req)
    except ChatRejected:
        raise
    except BaseException as exc:
        append_log(vault, "assistant", f"（这一轮没答成：{str(exc)[:200]}）",
                   project=req.project, session=req.session, stance=req.stance or DEFAULT_STANCE)
        core.record_issue(vault, "llm", str(exc), where="chat",
                          detail={"stance": req.stance or DEFAULT_STANCE})
        raise


def _run(vault: Path, req: ChatRequest):
    history, dropped = _fit_history([m.model_dump() for m in req.messages])
    if not history or history[-1]["role"] != "user":
        raise ChatRejected("最后一条必须是我说的话")
    append_log(vault, "user", history[-1]["content"], project=req.project, session=req.session,
               stance=req.stance or DEFAULT_STANCE)

    conf = stance_of(req.stance)
    allowed = set(conf["tools"])
    messages = [{"role": "system",
                 "content": _system_prompt(vault, req.stance, req.project)}] + history
    if dropped:
        # **截断要说出来**，不能让它默默失忆：模型不知道自己少了上下文时，
        # 会拿半截记忆当完整的用，比直接说"我没看到"糟得多。
        # 真正的长期记忆本来就不该是上下文窗口——聊清楚的东西应该已经进 md 了，
        # 所以这里顺便告诉它：缺的部分去图里查，或者问我。
        messages.insert(1, {"role": "user", "content":
            f"（提醒：这一段之前还有 {dropped} 轮没带过来。你缺的上下文别猜——"
            f"先 `search_nodes` / `read_node` 去图里找，找不到就直接问我。）"})
    said: list[str] = []      # 过程：每一次"还要接着调工具"的那段话
    answer = ""
    # 同一轮里同参数的工具调用只真跑一次：模型确实会连着用一模一样的参数再搜一遍
    # （真实对话里观察到的），每重复一次就白烧一个来回。
    seen_calls: dict[str, str] = {}
    for step in range(MAX_STEPS):
        text = ""
        usage: dict = {}
        for ev in _stream(vault, messages, op=f"chat-{req.stance or DEFAULT_STANCE}"):
            if ev["type"] == "delta":
                yield ev
            else:
                text, usage = ev["text"], ev["usage"]
        step_text = strip_tools(text)
        call = _parse_tool(text)
        if not call:
            # 不再调工具 = 这一段就是答案本身。前面那些"我先查一下""工具挂了"是过程，
            # 拼进正文的话，每次都要在一堆过程里找那几句有营养的（真实使用里最费时间的一点）。
            answer = step_text
            break
        said.append(step_text)

        name, args = call
        key = f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
        # 当前项目跟着一起传进工具：在某个项目里聊天，today / 出题范围都该是这个项目的。
        # 放在 key 之后算，免得它进了去重键。
        args = {**args, "_project": req.project or ""}
        fn = TOOLS.get(name) if name in allowed else None
        if key in seen_calls:
            result, extra = (f"这次调用和刚才那次一模一样，结果没变，不再跑一遍：\n{seen_calls[key]}"
                             f"\n\n**直接用这个结果回答，不要再调工具了。**"), {}
        elif fn is None:
            result, extra = (f"这一档口径下没有 `{name}` 这个工具。"
                             f"可用的是：{'、'.join(sorted(allowed))}。"), {}
        else:
            try:
                result, extra = fn(vault, args)
            except Exception as exc:             # 工具炸了也要让对话继续，把错误告诉模型
                log.warning("工具 %s 失败：%s", name, exc)
                # 记一笔：工具出错原来只在那一轮对话里闪一下，
                # "这东西为什么老出问题"没有任何地方能回答（layer 那个白名单 bug 就是这么藏了一阵）
                core.record_issue(vault, "tool", f"{type(exc).__name__}: {exc}", where=name,
                                  detail={"args": json.dumps(args, ensure_ascii=False)[:200]})
                result, extra = f"工具 `{name}` 执行失败：{exc}", {}
        seen_calls.setdefault(key, result)
        yield {"type": "tool", "name": name, "args": args,
               "summary": result[:200], **{k: v for k, v in extra.items() if k in ("card", "quiz")}}
        if extra.get("card"):
            yield {"type": "card", "card": extra["card"]}
        if extra.get("project"):
            yield {"type": "project", "project": extra["project"]}
        if extra.get("points"):
            yield {"type": "points", "points": extra["points"]}
        if extra.get("id") and name == "record_review":
            yield {"type": "review", "id": extra["id"], "next_due": extra.get("next_due")}

        messages = messages + [{"role": "assistant", "content": text},
                               {"role": "user", "content": f"[工具 {name} 的结果]\n{result}"}]
    else:
        yield {"type": "tool", "name": "（停）", "args": {},
               "summary": f"连着调了 {MAX_STEPS} 次工具还没给出回答，这一轮到此为止。"}
        answer = said.pop() if said else ""      # 用尽了步数：最后说的那段当答案

    trace = [t for t in said if t.strip()]
    # 高亮按"这一轮提到过谁"算，所以连过程一起看——图上该亮的节点常常是查出来的那个
    touched = _mentioned(vault, "\n\n".join([*trace, answer]))
    # 讲完一段随口问的那个检验问题：攒进题库。它是**在我刚学完那一刻、对着我当时的理解**
    # 提出来的，比事后让模型看着 md 现编的题贴身；而且它已经生成过一次了，别再付第二次钱。
    answer, checks = pull_checks(answer, touched)
    for c in checks:
        try:
            row = core.add_question(vault, c["stem"], c["points"], source="chat")
        except OSError as exc:
            log.warning("题库没写上：%s", exc)
            continue
        if row:
            yield {"type": "question", "stem": row["stem"], "points": row["points"]}
    append_log(vault, "assistant", answer, node_ids=touched, project=req.project,
               session=req.session, stance=req.stance or DEFAULT_STANCE, trace=trace)
    # node_ids 从调试信息升级成了界面契约：「聊到哪、图上亮哪」靠它（重构方案 §8 第 4 条）
    yield {"type": "done", "text": answer, "trace": trace, "usage": usage, "node_ids": touched}


def _mentioned(vault: Path, text: str) -> list[str]:
    """这一轮聊到了哪些节点。留档时标上，回溯"这个点当时怎么讲的"直接按 id grep。"""
    if not text:
        return []
    ids = [n["id"] for n in current_index(vault)["nodes"] if not n.get("virtual")]
    return [nid for nid in ids if nid in text][:20]
