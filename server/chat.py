"""对话式教练（阶段 12）：聊着学，学完一键入库。

**为什么要有这一层**：面板式的学习闭环（计划 → 今日 → 建节点 → 写正文 → 出题）每一步都对，
但启动成本高——想学一个点要在几个面板之间跳。聊天把这些收成一句话。
图仍然是产物，聊天只是入口：**对话是过程，知识住在 md 里**（F10.7）。

三条边界，一条都不松：

1. **不写 Markdown。** 模型只能 `propose_changes`，出的是一张预览卡（和 `/api/changes`
   的 dry_run 同一份代码），我看过 diff 点了才落盘（4.4「Agent 只提议不越权」）。
2. **复习判定只许降级**（F10.5）：对话里看出答错可以直接记「忘了」，看出答对**不准**记「记得」。
   判严了最多多复习一次，判宽了会让一个其实已经忘了的点从此不再出现。
3. **工具协议有两套，按 provider 能力自动挑**——但这一层看不见。`_run` 永远把 schema 传下去、
   永远拿结构化的 `calls` 回来，**一行 `if 围栏 else 原生` 都没有**。anthropic / openai 走
   原生 tool use；`claude -p` 是子进程、没有结构化工具接口，由 `llm_backend` 用文本围栏适配。
   原生是默认，因为围栏**失败是静默的**（捞不到 JSON 就当没调工具，那段话直接成了答案）；
   围栏留着，因为 claude-cli 是零配置的默认 provider，砍了等于"想聊天先去开个 API key"。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import queue
import re

from difflib import SequenceMatcher
import threading

from pathlib import Path

from . import curation, turns
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
# read_node 的正文预算。**以前是 1200 字，那是个会吃掉笔记的数**：模型拿到的是截断过的原文，
# 再用 `update_body`（整段替换）写回去，超出那 1200 字的后半截就被抹掉了。
# 现在放到 8000，并且真截断时会在结果里直说、明令只准用 `append_body`。
READ_CHARS = 8000
READ_BUDGET = 16000      # 一次读多个节点时的总预算：省步数不能换来把 prompt 撑爆
READ_MAX_IDS = 5
_PROMPTS: dict[str, str] = {}

# ```knowrary {...}``` —— 兜底用：工具走原生协议了，模型偶尔仍会手写一个块出来，别让它进正文
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


# 多词查询的切分：空格、顿号、逗号、斜杠、加号都算词边界。
# **不切词就等于只认字面全串**——问一句「符号主义 连接主义」，
# 干草堆里得原样出现带那个空格的整串才算命中，于是必然零命中，
# 模型拿着这个假阴性下"图里没有"的结论，再跑去翻全量项目和今日清单核对。
SPLIT_Q = re.compile(r"[\s、,，/／+＋]+")


def _terms(q: str) -> list[str]:
    """查询拆成词；一个词也拆不出来时退回整串（单字/纯符号的 query 照旧）。"""
    got = [t for t in SPLIT_Q.split(q.lower()) if t]
    return got or [q.lower()]


def _match(hay: str, terms: list[str]) -> int:
    """命中几个词。0 = 不算命中；命中多的排前面（两个词都中的当然比只中一个的相关）。"""
    low = hay.lower()
    return sum(1 for t in terms if t in low)


def _tool_search(vault: Path, args: dict) -> tuple[str, dict]:
    """搜节点，**也搜项目清单里还没建出来的点**。

    只搜索引是不够的：一个项目常常 40 个点里 39 个还没建（它们只活在 projects.json 里），
    这时问"图里有没有多头注意力"会得到零命中，而 Transformer 项目里明明就列着它——
    于是又建一个重复的。计划里的点和已建节点是同一件事的两个阶段，搜的时候不该只看后一半。

    多词查询按词拆开、任一词命中即算（见 SPLIT_Q）。
    """
    q = str(args.get("q") or "").strip()
    if not q:
        return "没给关键字，搜不了。", {}
    terms = _terms(q)
    limit = min(int(args.get("limit") or SEARCH_TOP), 20)
    index = current_index(vault)
    hits = []
    for n in index["nodes"]:
        if n.get("virtual"):
            continue
        # tags / aliases 也在干草堆里：「线头」抄进 tags 就是为了以后建到那个点时能搜回来
        hay = " ".join([n["id"], n.get("name") or "", n.get("desc") or "",
                        *(n.get("tags") or []), *(n.get("aliases") or [])])
        got = _match(hay, terms)
        if got:
            hits.append((got, n))
    # 先按命中词数，再按度数：两个词都中的排在只中一个的前面，同分的看谁在图里更"中心"
    hits.sort(key=lambda pair: (-pair[0], -(pair[1].get("degree") or 0)))
    hits = [n for _, n in hits]
    rows = []
    for n in hits[:limit]:
        row = {"id": n["id"], "name": n.get("name"), "desc": n.get("desc"),
               "field": n.get("field"), "状态": "只有壳" if n.get("stub") else "已建"}
        chars = _body_chars(vault, n)
        if chars > READ_CHARS:
            # 模型看到这一行就知道别直接整篇读：先 outline 看目录，再按 section 读要的那一节
            row["长笔记"] = f"约 {chars} 字，整篇读会被截断；先带 `outline: true` 看目录，再按 `section` 读"
        rows.append(row)

    built = {n["id"] for n in index["nodes"] if not n.get("virtual")}
    planned = []
    for pid, pr in (core.load_projects(vault).get("projects") or {}).items():
        for ls in core.lists_of(pr):
            for stage in ls.get("stages") or []:
                for pt in stage.get("points") or []:
                    nid = pt.get("id") or ""
                    if nid in built or not _match(f"{nid} {pt.get('name') or ''}", terms):
                        continue
                    planned.append({"id": nid, "name": pt.get("name"), "why": pt.get("why"),
                                    "状态": "计划里有、还没建",
                                    "在哪个项目": f"{pr.get('name') or pid}·{ls.get('name') or ls.get('kind')}"})
    planned = planned[:limit]

    if not rows and not planned:
        # **只比了标题 / 描述 / 别名 / 标签，没比正文**。说清楚这一点，
        # 否则模型会把"没搜到"直接讲成"你图里没有"——而那个词很可能就写在某篇笔记的正文里。
        return (f"按 {'、'.join(terms)} 这几个词，标题 / 描述 / 别名 / 标签里都没有匹配的"
                f"（**正文没进检索**，所以这不等于图里没写过）。"
                f"换个更短的词再搜一次，或者直接问我。", {"hits": 0})
    out = {}
    if rows:
        out["已建的节点"] = rows
    if planned:
        out["计划里列过、还没建出来的点"] = planned
    return json.dumps(out, ensure_ascii=False), {"hits": len(rows) + len(planned)}


def _body_chars(vault: Path, meta: dict) -> int:
    """节点文件的字数；读不到就当 0（搜索结果里少个提示，不该让搜索本身失败）。"""
    if not meta.get("path"):
        return 0
    try:
        return len(core.read(vault / meta["path"]))
    except OSError:
        return 0


def _read_one(vault: Path, index: dict, nid: str, budget: int,
              section: str = "", outline: bool = False) -> tuple[str, bool]:
    """读一个节点的原文 + 关系。返回 (给模型看的文本, 是否只看到了一部分)。

    `outline=True` 只给目录不给正文；给了 `section` 就只读那一节（含它的子节）。
    上万字的长笔记整篇读会被截断，模型先花几百字看目录、再按节读要改的那一节，
    才不用为改一段话把整篇背进上下文。
    """
    meta = next((n for n in index["nodes"] if n["id"] == nid), None)
    if meta is None:
        return f"图里没有 `{nid}` 这个节点。", False
    if meta.get("virtual") or not meta.get("path"):
        return f"`{nid}` 只是被别的节点引用的占位，还没有 md 文件。", False
    try:
        raw = core.read(vault / meta["path"])
    except OSError as exc:
        return f"读不到 `{nid}` 的文件：{exc}", False
    cap = max(400, min(READ_CHARS, budget))
    edges = {e["id"]: e for e in index["edges"]}
    rel = [f"{edges[i]['type']} → {edges[i]['target']}" for i in meta.get("out", []) if i in edges]
    rel += [f"{edges[i]['source']} → {edges[i]['type']} 本节点" for i in meta.get("in", []) if i in edges]
    rel_line = f"\n\n（关系：{'；'.join(rel) or '还没有'}）"
    if outline:
        return _read_outline(nid, meta, raw) + rel_line, False
    if section:
        return _read_section(nid, raw, section, cap) + rel_line, True
    cut = len(raw) > cap
    warn = ""
    if cut:
        toc = core.describe_outline(raw)
        warn = ("\n\n**注意：这份原文被截断了，你看到的不是全文。**"
                "所以这个节点只准用 `append_body` 追加，**绝对不许 update_body**——"
                "整段替换会把你没看到的那部分永久删掉。"
                + (f"\n它的目录如下，要看哪一节就带 `section: \"标题\"` 再读一次：\n{toc}" if toc else ""))
    return f"{raw[:cap]}{warn}{rel_line}", cut


def _read_outline(nid: str, meta: dict, raw: str) -> str:
    """只给目录：desc、正文字数、全部小标题。不带正文，所以几百字就够。"""
    toc = core.describe_outline(raw) or "（正文里没有小标题，只能整篇读）"
    return (f"**{meta.get('name') or nid}**（`{nid}`）：{meta.get('desc') or '（没有 desc）'}\n"
            f"正文约 {len(raw)} 字" + ("，超过一次能读的上限，整篇读会被截断" if len(raw) > READ_CHARS else "")
            + f"。目录：\n{toc}\n\n（这只是目录，没有正文。要看哪一节就带 `section: \"标题\"` 再读；"
            "没读到正文之前不许 `update_body`。）")


def _read_section(nid: str, raw: str, section: str, cap: int) -> str:
    """按标题读一节。找不到就把目录给模型，让它挑一个真有的。"""
    hit = core.extract_section(raw, section)
    toc = core.describe_outline(raw) or "（正文里没有小标题）"
    if hit is None:
        return f"`{nid}` 里没有叫「{section}」的小节。它的目录：\n{toc}"
    head, text = hit
    cut = len(text) > cap
    return (f"{text[:cap]}"
            + ("\n\n**注意：这一节太长，也被截断了。**" if cut else "")
            + f"\n\n（以上只是 `{nid}` 的「{head.title}」这一节，不是全文，"
            "所以这个节点只准用 `append_body` 追加，**不许 update_body**。"
            f"其余小节：\n{toc}）")


def _tool_read(vault: Path, args: dict) -> tuple[str, dict]:
    """读节点正文。**支持一次读几个**：要往 5 个已有节点里补内容，一个一个读要花 5 步，
    而一轮总共只有 MAX_STEPS 步——模型会在读到第三个的时候放弃后面两个。"""
    raw_ids = args.get("ids") if isinstance(args.get("ids"), list) else []
    ids = [str(x).strip() for x in [*raw_ids, args.get("id")] if str(x or "").strip()]
    ids = list(dict.fromkeys(ids))[:READ_MAX_IDS]        # 去重后保序，多给的截掉
    if not ids:
        return "没给 id，不知道读哪个节点。", {}
    index = current_index(vault)
    section = str(args.get("section") or "").strip()
    outline = bool(args.get("outline"))
    budget, parts, cuts = READ_BUDGET, [], 0
    for nid in ids:
        text, cut = _read_one(vault, index, nid, budget, section, outline)
        cuts += int(cut)
        budget -= len(text)
        parts.append(f"### {nid}\n{text}" if len(ids) > 1 else text)
        if budget <= 0 and nid != ids[-1]:
            parts.append(f"（余下的 {len(ids) - len(parts)} 个没读：这一次的字数预算用完了，"
                         f"要的话分开再读一次。）")
            break
    meta = {"id": ids[0], "ids": ids, "truncated": cuts}
    if section:
        meta["section"] = section
    if outline:
        meta["outline"] = True
    return "\n\n".join(parts), meta


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
    并成一条会把"主线学到哪了"和"面试准备到哪了"混成一个数字。

    可选 `project`：只看这一个。**不传就还是全量**（向后兼容，也是冷启动时该看的）。
    以前只有全量这一种：模型想核对某个项目的一行清单，也得把所有项目连时间账一起拉进上下文，
    轨迹上看起来就像它跑去翻了个不相干的项目。
    """
    data = read_projects(vault)
    want = str(args.get("project") or "").strip().lower()
    out = {}
    for pid, project in data.doc.projects.items():
        if want and want not in (pid.lower(), (project.name or "").lower()):
            continue
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
    if want and not out:
        known = "、".join(f"`{pid}`" for pid in data.doc.projects) or "（一个都没有）"
        return f"没有叫 `{want}` 的项目。现有的是：{known}", {"projects": 0}
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


def _mint_card(vault: Path, kind: str, args: dict, detail: dict | None = None) -> str:
    """给一张卡发个 id，并记一笔「摆出来了」。

    采纳那一笔在**落盘那一侧**记（`/api/changes`、`PUT /api/projects`），不在这儿——
    这里只知道卡摆出去了，点没点是后来的事，可能隔好几天（见 core/cards.py）。
    """
    card_id = core.new_card_id()
    try:
        core.card_proposed(vault, card_id, kind, session=args.get("_session") or "",
                           project=args.get("_project") or "", turn=args.get("_turn") or "",
                           detail=detail)
    except OSError as exc:
        log.warning("卡片流水没记上：%s", exc)     # 旁路，绝不拖垮正经提卡
    return card_id


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
           {"points": {"card_id": _mint_card(vault, "points", args, {"points": n}),
                       "project": pid, "project_name": project.get("name") or pid,
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
    # 档位（了解 / 会用 / 精通）决定出题深浅、对话展开到哪一层、拆点拆多细。
    # 让模型给：它刚跟你聊完目标，"面试要用"还是"了解一下"它比默认值清楚；
    # 认不出就退回 core.DEFAULT_LEVEL，不瞎填——档位填错会一路影响出题和拆解。
    level = str(args.get("level") or "").strip()
    card = {"card_id": _mint_card(vault, "project", args, {"action": "update" if exists else "create"}),
            "id": pid, "action": "update" if exists else "create", "near": near,
            "name": str(args.get("name") or pid)[:120],
            "field": str(args.get("field") or "")[:120],
            "level": level if level in core.LEVELS else core.DEFAULT_LEVEL,
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


LIST_OPS = ("rename", "drop", "set")
# 清单条目上允许改的字段。**`id` 不在这里**——换 id 是 `rename`，它要连着查重名，
# 混进 `set` 会变成"悄悄换了一个点"，进度和复习全对不上。
POINT_FIELDS = ("name", "why", "load")


def _resolve_list(vault: Path, args: dict):
    """定位 (项目, 清单, 清单序号)。定位不到就返回一句**给模型看的话**，让它自己纠正。"""
    pid = str(args.get("project") or "").strip()
    project = (core.load_projects(vault).get("projects") or {}).get(pid)
    if project is None:
        return f"没有 `{pid}` 这个项目。先 `projects` 看一眼我有哪些。"
    lists = core.lists_of(project)
    if not lists:
        return f"项目「{project.get('name') or pid}」下面还没有清单。"
    want = str(args.get("list") or "").strip()
    if not want:
        return project, lists[0], 0
    idx = next((i for i, ls in enumerate(lists) if (ls.get("name") or "") == want), -1)
    if idx < 0:
        names = "、".join(ls.get("name") or ls.get("kind") or "?" for ls in lists)
        return f"「{project.get('name') or pid}」下面没有叫「{want}」的清单。有的是：{names}"
    return project, lists[idx], idx


def _one_list_edit(edit: dict, points: dict[str, dict], dropped: set[str]) -> tuple[dict | None, str]:
    """翻一条改动。返回 (能展示也能应用的那条, 退回给模型的话)——两者只会有一个。"""
    op = str(edit.get("op") or "").strip()
    nid = str(edit.get("id") or "").strip()
    if op not in LIST_OPS:
        return None, f"`{op or '(空)'}` 不是清单改动的类型，只有 {'、'.join(LIST_OPS)}。"
    if nid not in points:
        return None, f"清单里没有 `{nid}` 这个点，改不了。先 `projects` 核一眼清单里到底写的是哪个 id。"
    old = points[nid]
    if op == "drop":
        return {"op": "drop", "id": nid, "name": old.get("name") or nid}, ""
    if op == "rename":
        to = str(edit.get("to") or "").strip()
        if not to or not core.ID_OK.match(to):
            return None, f"`{nid}` 要改成的新 id（`to`）没给或者不合法（不能有空格和 / \\ : * ? \" < > |）。"
        if to in points and to not in dropped:
            return None, (f"`{to}` 在这份清单里已经有了——那这两条是重复，该 `drop` 掉一条，"
                          f"不是把 `{nid}` 改成它。")
        return {"op": "rename", "id": nid, "to": to, "name": old.get("name") or nid}, ""
    fields = {k: edit[k] for k in POINT_FIELDS if k in edit}
    if not fields:
        return None, f"`set {nid}` 一个字段都没给。能改的是：{'、'.join(POINT_FIELDS)}。"
    if "load" in fields and fields["load"] not in core.LOADS:
        return None, f"负荷只有 {'、'.join(core.LOADS)} 三档，`{fields['load']}` 不认识。"
    return {"op": "set", "id": nid, "name": old.get("name") or nid, "fields": fields,
            "before": {k: old.get(k) for k in fields}}, ""


def _tool_propose_list_edit(vault: Path, args: dict) -> tuple[str, dict]:
    """提议改清单里已有的条目：改 id / 删掉 / 改字段。**不写盘**，出一张卡等人点。

    **为什么要有它**（复盘 §11.4）：以前模型只能往清单里加，不能改已有的，
    于是撞上「同一个概念在两份清单里用了不同 id」「拆成两条之后旧的合并项还躺着」
    这两类事时，只能说一句"那条得你自己去面板删"——而它的提示词里明写着
    「不许把要不要做丢回来问我」。不是它不听话，是工具表里没有这条路。
    """
    found = _resolve_list(vault, args)
    if isinstance(found, str):
        return found, {}
    project, ls, idx = found
    points = {p["id"]: p for st in (ls.get("stages") or []) for p in (st.get("points") or [])
              if isinstance(p, dict) and p.get("id")}
    edits, refused = [], []
    dropped = {str(e.get("id") or "") for e in args.get("edits") or []
               if isinstance(e, dict) and e.get("op") == "drop"}
    for edit in args.get("edits") or []:
        if not isinstance(edit, dict):
            continue
        row, why = _one_list_edit(edit, points, dropped)
        (edits.append(row) if row else refused.append(why))
    if not edits:
        return ("这组改动一条都没立住：\n" + "\n".join(f"- {w}" for w in refused)
                if refused else "没给 edits，没什么可提议的。"), {}

    left = len(points) - sum(1 for e in edits if e["op"] == "drop")
    card = {"card_id": _mint_card(vault, "list_edit", args, {"edits": len(edits)}),
            "project": args.get("project"), "project_name": project.get("name") or args.get("project"),
            "list": idx, "list_name": ls.get("name") or ls.get("kind") or "清单",
            "edits": edits, "left": left, "empties": left <= 0}
    tail = "".join(f"\n- 退回：{w}" for w in refused)
    if left <= 0:
        # 清单清空了，项目进度和今日清单会跟着全空——这事得让他看见，不能悄悄发生
        tail += (f"\n\n**注意：这些删完「{card['list_name']}」就是空清单了**（{len(points)} 条全删）。"
                 f"卡上已经标红，但你最好在卡外面说一句为什么要清空。")
    return (f"清单卡摆在他面前了（{len(edits)} 条改动）。**还没改**，等他点「应用」。"
            f"这张卡只动 projects.json，不碰 md、不碰画布。{tail}"), {"list_edit": card}


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
    card = {"card_id": _mint_card(vault, "changes", args, {"files": len(files)}),
            "changes": changes, "base_revision": index["revision"],
            "files": [f.model_dump() for f in files], "into": into}
    tail = (f"写入时会顺手把 {'、'.join(into['points'])} 加进「{into['project_name']}·{into['list_name']}」清单。"
            if into else "")
    # 末尾这句是给"一轮一张卡"解锁的：链路本来就支持一轮摆好几张（每张一个「写入」按钮），
    # 但上一版的措辞只说"等他点写入"，模型读完就收尾了——于是互不相关的几件事被迫拆成好几轮。
    return (f"变更卡已经摆在他面前了（{len(files)} 个文件）。**还没有写盘**，"
            f"等他点「写入」。{tail}你不要再说已经存好了。"
            f"\n\n**还有别的事要提就接着提**——一轮里可以摆好几张卡，他一张一张点。"), {"card": card}


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
# 工具表：**一份数据，三个用途**——渲染给人看的说明书、生成给模型的 JSON Schema、当白名单。
#
# 原来只有说明书那一份（一段自然语言写的参数描述），因为工具协议是文本围栏：模型照着
# 说明书写一个 ```knowrary {...}``` 块，服务端用正则去捞。捞不着就当它没调工具，
# 那段话直接被当成答案——**失败是静默的**。现在走原生 tool use，参数形状由 schema 约束、
# 调用以结构化字段回来，所以参数必须是结构化的。
#
# §3.4 那条纪律照旧：说明书、schema、白名单是同一份数据。"表里写着能用、调了却说没有"
# 是最让人发火的那种 bug，两边各写一遍迟早对不上。
TOOLS_SPEC: dict[str, dict] = {
    "search_nodes": {
        "doc": "按关键字找节点。**讲任何一个概念之前先搜一下**，看我图里有没有",
        "params": {"q": ("string", "关键字，多个词用空格隔开，任一词命中即算"),
                   "limit": ("integer", "返回几条，默认 8")},
        "required": ["q"],
    },
    "read_node": {
        "doc": ("读节点的正文和关系。要引用我已有的笔记就先读它，别凭印象说「你笔记里写过」。"
                "**要往好几个节点补内容时一次把它们全读进来**，别一个一个读。"
                "搜索结果标了「长笔记」的，先 `outline` 看目录再按 `section` 读那一节，别整篇读了又被截断"),
        "params": {"id": ("string", "要读的节点 id"),
                   "ids": (["array", "string"], "一次读多个节点，最多 5 个"),
                   "outline": ("boolean", "只看目录，不读正文"),
                   "section": ("string", "只读某一节（填小标题）")},
        "required": [],
    },
    "overview": {"doc": "图谱概况：节点数、领域、还有多少壳", "params": {}, "required": []},
    "today": {"doc": "今日清单：错题 / 到期复习 / 计划里还没建的点", "params": {}, "required": []},
    "projects": {
        "doc": ("我的项目、清单、进度和时间账（还剩多少、来不来得及）。"
                "**要核对某一个项目就带上 `project`**，别把所有项目全拉进来；不传是全量"),
        "params": {"project": ("string", "项目 id 或名字；不传就是全量")},
        "required": [],
    },
    "quiz": {
        "doc": "按这些节点出题考我",
        "params": {"node_ids": (["array", "string"], "要考的节点 id"),
                   "count": ("integer", "出几道，默认 3，最多 10")},
        "required": ["node_ids"],
    },
    "record_review": {
        "doc": "记一次复习。**只能记「忘了」**，见下面的纪律",
        "params": {"id": ("string", "节点 id"),
                   "grade": ("string", "只能是「忘了」；判「记得」得我自己点")},
        "required": ["id", "grade"],
    },
    "propose_changes": {
        "doc": "提议把学到的东西写进图谱。**只是提议**，会变成一张卡片等我点「写入」",
        "params": {"changes": (["array", "object"], "变更集，形状见下面的格式说明")},
        "required": ["changes"],
    },
    "propose_project": {
        "doc": "提议建一个项目，或往现有项目里加一份清单。同样只是卡片",
        "params": {"id": ("string", "项目 id，只能 ASCII 字母数字 `_` `-`（它会成为文件名）"),
                   "name": ("string", "项目名，中文放这里"),
                   "field": ("string", "顶层领域"),
                   "level": ("string", "学到什么份上：了解 / 会用 / 精通"),
                   "weekly_hours": ("number", "每周投入几小时"),
                   "lists": (["array", "object"], "要建的清单，形状见下面的格式说明")},
        "required": ["id", "name", "field", "level"],
    },
    "propose_points": {
        "doc": "把某个项目的某份清单拆成知识点。走面板上「让 AI 拆一份」同一条链路，同样出卡片",
        "params": {"project": ("string", "项目 id"),
                   "list": ("string", "清单名"),
                   "goal": ("string", "这份清单要达成什么")},
        "required": ["project", "list"],
    },
    "propose_list_edit": {
        "doc": ("提议改清单里**已有**的条目：换 id / 删掉 / 改负荷说明。"
                "**看出清单里有错的、重复的、该删的，直接提这张卡**，别让我自己去面板改"),
        "params": {"project": ("string", "项目 id"),
                   "list": ("string", "清单名"),
                   "edits": (["array", "object"], "要做的改动，形状见下面的格式说明")},
        "required": ["project", "list", "edits"],
    },
}


def _param_brief(spec: dict) -> str:
    """说明书里那一列参数。必填的加星号——schema 会拦住，但人看表时也该一眼看出来。"""
    if not spec["params"]:
        return "无"
    req = set(spec["required"])
    return "、".join(f"`{k}`" + ("**（必填）**" if k in req else "") for k in spec["params"])


# 说明书那一份：`| 工具 | 参数 | 用途 |` 三列里的后两列，从 TOOLS_SPEC 渲染出来
TOOL_DOC = {name: f"{_param_brief(spec)} | {spec['doc']}" for name, spec in TOOLS_SPEC.items()}


def _json_type(t) -> dict:
    if isinstance(t, list):                       # ("array", 元素类型)
        return {"type": "array", "items": {"type": t[1]}}
    return {"type": t}


def tool_schemas(names) -> list[dict]:
    """白名单 → 给模型的工具定义（Anthropic 字段名；openai 那侧在 llm_backend 里翻译）。

    **只渲染白名单里的**：复习那一档关掉时 `quiz` / `record_review` 是真的被收走，
    不是留着工具再叮嘱一句"别用"——手段还在手里、只靠一句话拦着，那是压制不是关闭。
    """
    out = []
    for name in names:
        spec = TOOLS_SPEC.get(name)
        if not spec:
            continue
        props = {k: {**_json_type(t), "description": desc} for k, (t, desc) in spec["params"].items()}
        out.append({"name": name, "description": spec["doc"],
                    "input_schema": {"type": "object", "properties": props,
                                     "required": list(spec["required"])}})
    return out


READ_ONLY = ("search_nodes", "read_node", "overview")

# 三个"要写东西"的工具得多给一段格式说明。**跟着白名单一起渲染**——
# 写死在基底里的话，面试口径的说明书上会白纸黑字写着一个它调不动的工具。
#
# schema 只能说清参数的**形状**（类型、必填），说不清**什么时候该用哪个**
# （`append_body` 还是 `update_body`、几件事要不要拆成几张卡、正文该写到什么份上）。
# 那些是纪律，仍然只能用散文写，所以这一份没有被 schema 取代。
FORMAT_DOC = {
    "propose_project": """`propose_project` 的参数长这样（`id` 只能 ASCII，它会成为文件名；中文放 `name`）：

```json
{"id": "nlp", "name": "NLP 方向", "field": "AI", "level": "会用", "weekly_hours": 7,
 "lists": [{"kind": "学习", "name": "主线", "goal": "三个月吃透 Transformer 到 RLHF",
            "target_date": "2026-12-15"}]}
```

一个项目下可以有好几份清单，`kind` 决定怎么拆：`学习`（按依赖顺序）/ `面试`（按会怎么问）/
`领域`（按覆盖度铺地图）。

`level` 是**学到什么份上**，三档：`了解`（知道它是什么、解决什么问题就够）/
`会用`（能上手、讲得清取舍）/ `精通`（要能推导机制、答得住追问）。
它一路决定出题深浅、拆点拆多细——按他刚才说的目标挑，别一律给默认档。

**先建项目，再拆点**——拆点是另一步，别在同一条消息里全干完。""",
    "propose_list_edit": """改清单里**已有**的条目（只动 `projects.json`，不碰 md），参数长这样：

```json
{"project": "transformer", "list": "主线", "edits": [
  {"op": "rename", "id": "多头注意力", "to": "MHA"},
  {"op": "drop", "id": "符号主义与连接主义"},
  {"op": "set", "id": "MLA", "load": "重", "why": "要能推导才算过"}]}
```

- `rename` 换 id：**同一个概念在两份清单里写成了两个 id** 时用它（节点已经建成 `MHA`，
  清单里却还写着 `多头注意力`，于是那条永远显示「未建」）。新 id 在这份清单里已经有了的，
  说明这俩是重复，该 `drop` 一条而不是改。
- `drop` 删条目：一条被拆成两条之后，旧的那条合并项还躺在清单里，进度永远差一截。
- `set` 改字段：只能改 `name` / `why` / `load`（负荷三档 `轻 / 中 / 重`，它直接决定时间账）。

**这张卡不改 md，也不会把节点从图里删掉**——清单只是引用一组 id。真要动节点，那是 `propose_changes`。""",
    "propose_changes": """`propose_changes` 的 `changes` 和图谱的 ChangeSet 同一个形状，八种改动：

```json
{"changes": [
  {"type": "create_node", "source": "NPU", "path": "nodes/02-计算机硬件/NPU.md",
   "fields": {"name": "NPU", "field": "计算机系统", "layer": "硬件", "year": 2017,
              "desc": "一句话摘要（显示层：画布卡片上就这一句）",
              "tags": ["神经生理学", "数学"]},
   "body": "（笔记层：按下面的正文骨架写足，落盘时整篇保留）"},
  {"type": "append_body", "source": "GPU", "body": "## 和 NPU 的分工\\n（只写这次补的这一段）"},
  {"type": "update_body", "source": "GPU", "body": "（这个节点的**完整**新正文）"},
  {"type": "add_edge", "source": "NPU", "relation": "对比", "target": "GPU"},
  {"type": "remove_edge", "source": "GQA", "relation": "演化为", "target": "MLA"},
  {"type": "update_edge", "source": "GQA", "target": "MLA", "from_relation": "对比",
   "note": "两条路，不是一条线上的先后"},
  {"type": "update_frontmatter", "source": "内存墙", "fields": {"desc": "改过的一句话摘要"}},
  {"type": "set_fact", "source": "jieba", "key": "核心方法", "value": "词典 + HMM，一句话结论"}
]}
```

**往已有节点里补东西，默认用 `append_body`**：它只往正文尾部接一段，不动我原来写的字，
所以不要求你把全文背回来。补的那一段自己带个 `##` 小标题，让笔记看得出层次。

`update_body` 是**整段替换**，只在真要重写/合并/删错字时才用，而且**必须先 `read_node` 读到全文**——
`read_node` 说了原文被截断的、或者你只带 `outline` / `section` 看了目录或一节的，这个节点就只准 `append_body`，
替换会把你没看见的那部分永久删掉。搜索结果标了「长笔记」的，先 `outline` 看目录再按 `section` 读，别整篇读。

**图上错的东西你有权提议改掉，不是只能问我。** 后三种就是干这个的，和「补内容」完全对称：
同样只出卡片、同样要我点，所以**看出问题就直接提，别把选择题丢回给我**。

- `remove_edge`：这条边本来就不该在。`relation` + `target` 定位，同名的多条会一起删。
- `update_edge`：边的类型 / 年份 / 注写错了。`from_relation` 定位原来那条，`relation` 给新类型
  （不改类型就填一样的）；`year` 和 `note` **给了才动，没给就保持原样**。
- `update_frontmatter`：`fields` 里逐个字段给新值。能改的只有 `name / field / layer / params /
  type / status / year / start_year / end_year / aliases / tags / desc / learned / source / color /
  timeless`；
  `id` 和画布坐标（`x / y / w / h / group / collapsed / pinned`）永远改不了，提了整批退回。
  给空串等于删掉这一行。`status` 只能填 `active / deprecated / disputed / stub`；
  `params` 是参数量、按 `175B` / `340M` / `1.3万亿` 这样写（拿来在图上比大小，不是规格表）。
  `color` 只对 `type: 流派` 有用（历史视图里那条时间带的颜色，`#rrggbb`）——
  **别主动改它**：颜色是人用来认人的，今天蓝的明天绿的比没有颜色更糟。
  `timeless: true` 的意思是**「看过了，它本来就没有年份」**——结构概念（寄存器 / 栈帧）、
  外部学科入口、工程原则都属于这一类。它让这个点从「缺 year」的欠账里消失，
  **所以只在真的不该有年份时才标**；只是"我不知道是哪年"就别填，那是待办不是结论。
  `start_year / end_year` 同理只对流派有意义：`end_year` 留空表示还在延续，
  **不要为了"看起来完整"去猜一个结束年**——停摆由那条累计走势的平台期自己显示。

`move_node` 只换目录、**内容一个字不动**：`path` 给新位置，文件名必须还是 `<id>.md`
（文件名就是 id，改名是另一条链路的事）。什么时候用：stub 补成真节点之后还躺在
`nodes/_stubs/`、或者一个知识点改成 `type: 流派` / `对比组` 之后该挪进 `fields/`。
**它要单独提一次**，不能和改内容混在同一批——混着的话"改的是旧文件还是新文件"说不清。

`set_fact` 改的是正文 `## 速查` 里的**一行**（横向对比表的一格）：`key` 是维度名、
`value` 是一句话结论（30 字上下，长解释写进正文别处），`value` 给空就是删掉这一行。
它只碰那一行，所以**不要求你把全文背回来**——改一格表用 `update_body` 是拿整篇的风险
换一句话的收益。`年份` / `参数量` / `抽象层` 不走这里（它们的真相在 frontmatter），
改那三个用 `update_frontmatter`。

**`desc` 尤其要盯。** 正文改完、`desc` 还停在旧说法上，是这套图最容易攒下的烂账——
`update_body` / `append_body` **碰不到 frontmatter**，摘要只能靠 `update_frontmatter` 单独改。
所以每次动完正文回头看一眼那句摘要还对不对，不对就在同一张卡里一起改掉。

**删之前把理由说在卡外面。** 卡片上只看得见 diff，看不见你为什么这么想；
「这条边和 X 打架，所以建议删」这句话得你自己说出来，不然删了像误触。
拿不准的也照样提卡、把理由写足——**一张我可以不点的卡，比一个我得回答的问题省事**。

**互不相关的几件事，分开提成几张卡。** 一张卡是**整份写入**的：里面夹着一条我还没想好的，
整张卡就卡在那儿，已经想好的那几条跟着一起等。所以「删这条边」和「顺手清四个节点的 tags」
该是两张卡，不是一张——**一轮里连着提几张完全可以**，我一张一张点。
只有同一个主题下的几处改动（补正文 + 跟着改 `desc` + 补一条边）才并进同一张卡。

**正文要写成能过半年回看的笔记，不是一行标题。** 一个节点有两层：`desc` 是**显示层**，
画布卡片上只有这一句，所以短；`body` 是**笔记层**，落盘时整篇保留，按下面的档位写足。
「留白」只适用于「我的理解」那一节（没懂就写没懂）；**事实层不留白**——
人物、学科、年份、出处这些我口述里说过的词，一个都不能在入库时被精简掉，
少一个词以后就少一条能连的边（「神经生理学家 + 数学家」丢了，神经元和神经网络数学那两条线就接不上）。

{{note_level}}

新建节点的 `body` 按这个骨架写，哪一节这次没聊到就整节不要，别写占位话：

```
（开头一两句：它是什么、解决什么问题）

## 为什么需要它
没有它之前是怎么做的、卡在哪。

## 怎么运作
关键机制，能画就画（代码块 / 步骤 / 简图）。

## 容易搞混的
和哪个概念长得像、区别在哪。

## 我的理解
我当时是怎么想通的；**还没懂的地方直接写「没懂：…」留在这儿**，别替我编圆。

## 线头
- 神经生理学：McCulloch 的出身，「神经元」那条线以后接这里
- 数学 / 逻辑：Pitts 的出身，「神经网络的数学基础」接这里
```

**「线头」是给以后连边留的钩子，哪档都不能省。** 一行一个：口述里出现、这次没建成节点的
人物、学科、来源概念、相邻技术，后面写一句它为什么会跟这个点有关。同一批词再抄一份进
`fields.tags`，`search_nodes` 搜得到 tags——以后建到「神经元」时一搜就知道该接回来。

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
    "propose_list_edit": _tool_propose_list_edit,
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
               stance: str | None = None, trace: list[str] | None = None) -> str:
    """一行一轮，按月分文件。**不建库、不切分、不做 embedding**（F10.7）：
    对话是过程不是知识，检索系统已经存在，就是那张图。找旧对话用 grep。

    `session` 只是行上的一个标签——**不建会话表、不存会话元数据**。
    会话列表是从这些行里聚合出来的，和进度、时间账一样是派生的（不落第二份真值）。

    返回这一行的 `ts`：它是「梳理游标」唯一的坐标（见 `mark_tidied`），
    界面上要拿它来判断"这条在游标前还是游标后"。
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
    return row["ts"]


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


# ---------------------------------------------------------------- 梳理游标

def tidied_path(vault: Path, project: str | None = None) -> Path:
    slot = project if project and core.ID_OK.match(project) else SCRATCH
    return vault / ".knowrary" / "chat" / slot / "tidied.json"


def load_tidied(vault: Path, project: str | None = None) -> dict:
    """「这一段梳理到哪儿了」。**和 titles.json 一样是张贴纸，不是真值**：
    删掉它只会退回全量重梳，一个字的知识都不会丢。

    存在的理由很实在：梳理是整个应用里最贵的一次动作（MAX_STEPS 的工具循环，
    每一步都把整段对话再发一遍）。第二天打开同一段对话再点一次「梳理这段」，
    没有游标的话就是把昨天那笔钱原样再付一遍。
    """
    path = tidied_path(vault, project)
    if not path.exists():
        return {}
    try:
        data = core.load_json(path)
    except (ValueError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict) and v.get("upto")}


def _not_after(ts: str, other: str | None) -> bool:
    """`ts` 是不是没有越过 `other`。**按时间比，不按字符串比**——留档的 ts 带本地时区偏移，
    夏令时切换或换时区之后，字符串序和时间序就不是一回事了。"""
    if not other:
        return False
    try:
        return dt.datetime.fromisoformat(ts) <= dt.datetime.fromisoformat(other)
    except ValueError:
        return str(ts) <= str(other)


def mark_tidied(vault: Path, session: str, upto: str, project: str | None = None,
                turns: int = 0) -> dict:
    """推进某一段的梳理游标。**只进不退**：一次对话里先写入了后面那张卡、
    再回头写前面那张，游标不该被拖回去。
    """
    session = (session or "").strip()
    upto = (upto or "").strip()
    if not session or not upto:
        raise ChatRejected("推进游标要同时给 session 和 upto")
    marks = load_tidied(vault, project)
    old = marks.get(session) or {}
    if _not_after(upto, old.get("upto")):
        return old
    marks[session] = {"upto": upto, "turns": int(turns or 0),
                      "at": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    path = tidied_path(vault, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    core.write_json_atomic(path, marks)
    return marks[session]


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
    marks = load_tidied(vault, project)
    for b in out:
        b["auto"] = b["title"] or "（没说什么）"      # 自动取的那个：改名框里当占位符
        b["title"] = mine.get(b["id"]) or b["auto"]
        b["renamed"] = b["id"] in mine
        b["tidied"] = marks.get(b["id"]) or None    # 梳理到哪儿了；没梳理过就是 None
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
    # ts 从留档原样带出来：界面靠它判断"这条在梳理游标前还是后"，没有它就只能全量重梳
    return [{"role": r["role"], "content": r["text"], "node_ids": r.get("node_ids") or [],
             "trace": r.get("trace") or [], "ts": r.get("ts") or ""}
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


REVIEW_TOOLS = ("quiz", "record_review")   # 复习关掉时**真的收走**的工具


def stance_of(name: str | None) -> dict:
    return STANCES.get(name or DEFAULT_STANCE, STANCES[DEFAULT_STANCE])


def tools_of(stance: str | None, vault: Path) -> tuple[str, ...]:
    """这一轮到底给哪些工具。

    **复习关掉就把出题 / 记复习收走，而不是留着工具再叮嘱一句"别用"。**
    手段还在手里、只靠一句话拦着，那是压制不是关闭：模型照样在说明书上看见
    `quiz | 按这些节点出题考我`，随时可能自己去调；而"不要做 X"这种反向指令
    本身还在提醒它有 X 这件事。工具表是从白名单渲染的（说明书 = 实际权限，
    见 `_system_prompt`），白名单里没有，说明书上就不会出现。
    """
    tools = stance_of(stance)["tools"]
    if core.review_in_chat(vault):
        return tools
    return tuple(t for t in tools if t not in REVIEW_TOOLS)


def _project_level(vault: Path, project: str | None) -> str:
    """当前项目的难度档。不绑项目（全局对话）就用默认档。

    为什么按项目而不按清单：一次对话不属于某一份清单，但一定属于某个项目的视角。
    清单级的覆盖只影响出题与拆点，那两件事本来就是从某一份清单发起的。
    """
    if not project:
        return core.DEFAULT_LEVEL
    doc = core.load_projects(vault)
    return core.level_of((doc.get("projects") or {}).get(project))


# 提示词里"跟复习有关、可以整段关掉"的段落用这对标记包起来。
# 为什么做成标记块而不是另写一份提示词：复习的规矩散在基底和教练两份文件的不同位置
# （开场看 today、结尾出 check 题、只许降级……），拆成两份文件维护的话，
# 改一条规矩要记得改两处，迟早分叉。
REVIEW_OPEN, REVIEW_CLOSE = "<!--review-->", "<!--/review-->"
# 关掉时只补**一句事实**，不补"不要做 X"。规矩已经整段不渲染、工具已经收走，
# 再写一串禁令就是在提醒它有这回事；留这一句是为了他真开口问"考我一下"时，
# 模型知道该答"你在设置里关了复习"，而不是干巴巴甩一句"没有这个工具"。
# 两种关法要说两句不同的事实，**不能共用一句**：总闸关了是"整套都没有"，
# 只关聊天这一档是"功能还在，只是不该由你来考"——后者说成前者，
# 他问一句"我不是能在今日面板复习吗"，模型会跟着答"复习关着"，等于凭空多一次误导。
REVIEW_OFF_NOTE = ("\n（复习与出题整套在设置里关着：今日清单不含到期与错题，出题和记复习的工具"
                   "这一轮也没给。他要考试就请他去设置里打开。）")
CHAT_REVIEW_OFF_NOTE = ("\n（他把「教练考我」这一档关了：出题和记复习的工具这一轮没给，别催复习、"
                        "别在结尾出检验题。**复习本身还开着**——他在项目下的「今日」分栏自己复习，"
                        "那里照常有到期与错题。他问起就这么说，别说成复习关了。）")


def _apply_review_switch(text: str, in_chat: bool, overall: bool) -> str:
    """教练这一档关着时，把标记块整段剔掉，并在末尾补一句明确的事实。

    **只剔块、不改别处**：留着块里的字再叮嘱一句"别提复习"，等于同时给了正反两套指令，
    模型照着哪一套都说得通。

    补哪一句由**总闸**决定：整套关了和"只是不该由你来考"是两件事（见两个 NOTE 常量）。
    """
    if in_chat:
        return text.replace(REVIEW_OPEN, "").replace(REVIEW_CLOSE, "")
    out = []
    rest = text
    while REVIEW_OPEN in rest:
        head, _, tail = rest.partition(REVIEW_OPEN)
        out.append(head)
        _, _, rest = tail.partition(REVIEW_CLOSE)
    out.append(rest)
    note = REVIEW_OFF_NOTE if not overall else CHAT_REVIEW_OFF_NOTE
    return "".join(out).rstrip() + "\n" + note


def _system_prompt(vault: Path, stance: str | None, project: str | None = None) -> str:
    """基底一份 + 口径一份。工具表从白名单渲染——**说明书和实际权限是同一份数据**，
    两边各写一遍迟早对不上（"表里写着能用、调了却说没有"是最让人发火的那种 bug）。

    **图谱现状不在这里**，它单独走 `_graph_snapshot`（见那个函数的注释）。

    复习关着时，标记块里那几条规矩整段不渲染（见 `_apply_review_switch`）——
    开关只存浏览器的话界面安静了、教练照样每轮催，这就是它要放进 vault 的原因。
    """
    conf = stance_of(stance)
    body = _prompt_file(conf["file"])
    intro, _, rules = body.partition("## 规矩")
    tools = tools_of(stance, vault)          # 复习关掉时这里已经少了 quiz / record_review
    table = "\n".join(f"| `{t}` | {TOOL_DOC[t]} |" for t in tools if t in TOOL_DOC)
    formats = "\n\n".join(FORMAT_DOC[t] for t in tools if t in FORMAT_DOC)
    text = (_prompt_file("chat")
            .replace("{{level}}", level_fragment(_project_level(vault, project), "chat"))
            .replace("{{tools}}", table)
            .replace("{{formats}}", formats)
            # 在 formats 之后替换：{{note_level}} 住在 FORMAT_DOC 里，先渲染进基底才替得到
            .replace("{{note_level}}", level_fragment(_project_level(vault, project), "note"))
            .replace("{{relations}}", _relations_brief(vault))
            .replace("{{me}}", _me_brief(vault, project))
            .replace("{{stance_intro}}", intro.strip())
            .replace("{{stance_rules}}", rules.strip() or "（没有额外规矩）"))
    return _apply_review_switch(text, core.review_in_chat(vault), core.review_on(vault))


SNAP_HEAD = "## 我的图谱现在是什么样"


def _graph_snapshot(vault: Path) -> str:
    """图谱现状：节点数、边数、领域分布、已有项目。**单独一条 system 消息，挂在 messages 队尾。**

    它原来就写在 `prompts/chat.md` 中间。问题是这几个数字**会变**——教练的整个用途
    就是聊着聊着把新点入库，一旦采纳了一张变更卡，节点数就变了。而提示词缓存认的是
    **逐字节的前缀**：中间插一个会变的数字，等于每次图谱一动，它后面那 9000 字静态
    指令（工具表、格式、关系类型表、教练侧写）全部作废重买。

    第一版把它拆成第二条顶层 system，断点打在两者之间。但**顶层 system 整体排在
    所有 messages 之前**：静态那块是保住了，图谱一动整段对话的缓存照样全丢。
    所以现在它是 mid-conversation system message，坐在历史之后（见 _run 里的 append
    与 llm_backend._split_system）——变了只作废它自己这 261 字。

    顺带一个安全性收益：user 消息里的「（系统提示）」谁都能伪造，`role: "system"` 不能，
    它是不可冒充的操作指令通道。
    """
    return f"{SNAP_HEAD}\n\n{_overview(vault)}"


def strip_tools(text: str) -> str:
    """把工具块从要给人看的文本里摘掉。**现在是兜底，不是协议的一部分。**

    工具调用已经走原生 tool use，正文里本不该再出现 ```knowrary 块。但模型见过太多
    这种写法，偶尔还是会手写一个出来——那既不会被执行，也不该让人在屏幕上看见一段 JSON。
    前端按同一条正则过滤（`stripToolBlocks`），**两边用同一个形状**。
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


def llm_session_key(req: ChatRequest) -> str:
    """续接缓存（`server/turns.py`）认这一段对话的钥匙。

    和留档用的 `req.session` 不是一回事：口径换了系统提示词就换了，不能续同一段；
    项目换了图的范围也变了。三样拼起来才是"同一段上下文"。
    """
    return f"{req.project or '_scratch'}|{req.session or '_'}|{req.stance or DEFAULT_STANCE}"


def _stream(vault: Path, messages: list[dict], tools: list[dict], op: str = "chat",
            session: str | None = None):
    """在后台线程里跑一次 LLM 调用，把增量从队列里取出来往外 yield。

    生成器里没法从回调 yield，所以只能用队列过一道。
    收尾那条 `_done` 带着 `calls`：模型这一步调了哪几个工具，**结构化地**回来。
    底下用的是原生 tool use 还是文本围栏，由 provider 的能力决定，这里看不见——
    适配关在 `llm_backend` 里，循环只有一条代码路径（见那边的 §多轮对话 + 工具协议）。
    """
    q: queue.Queue = queue.Queue()
    box: dict = {}

    def work() -> None:
        try:
            box["text"], box["calls"], box["usage"] = llm_chat(
                vault, "learn", messages, tools=tools, op=op, on_delta=lambda t: q.put(t),
                session=session)
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
    yield {"type": "_done", "text": box.get("text") or "", "calls": box.get("calls") or [],
           "usage": box.get("usage") or {}}


def run(vault: Path, req: ChatRequest):
    """一条消息的完整回合：可能夹着几次工具调用。逐个 yield 事件给 SSE。

    这一轮要是炸了（模型不通、配置写错），**留档里也要留个记号**：
    只记提问不记结果的话，失败五次就攒出五条没人答的问题，
    下次接着聊时全被读回去当上下文。

    **中断不是出错，两条路要分开。** 点「停止」或关掉页面走的是生成器被关闭
    （`GeneratorExit`，异步那侧是 `CancelledError`），它既不是模型的锅也没什么可查的：
    - 留档里写成"被我中断了"，而不是"没答成：" —— 后者冒号后面是空的，读留档的人只会以为是个 bug；
    - **不进出错流水**。`issues.jsonl` 是用来回答"这东西为什么老出问题"的，
      把人主动按的停止算进去，那张表就没法看了。
    """
    try:
        yield from _run(vault, req)
    except ChatRejected:
        raise
    except (GeneratorExit, asyncio.CancelledError):
        append_log(vault, "assistant", "（这一轮被我中断了）",
                   project=req.project, session=req.session, stance=req.stance or DEFAULT_STANCE)
        raise
    except BaseException as exc:
        append_log(vault, "assistant", f"（这一轮没答成：{str(exc)[:200]}）",
                   project=req.project, session=req.session, stance=req.stance or DEFAULT_STANCE)
        core.record_issue(vault, "llm", str(exc), where="chat",
                          detail={"stance": req.stance or DEFAULT_STANCE})
        raise


def _last_snapshot(messages: list[dict]) -> str | None:
    """这段上下文里最后贴的那份图谱快照；一次都没贴过就是 None。"""
    for m in reversed(messages):
        text = m.get("content") or ""
        if m.get("role") == "system" and text.startswith(SNAP_HEAD):
            return text
    return None


def _rebuild(vault: Path, req: ChatRequest, history: list[dict], dropped: int) -> list[dict]:
    """从零拼一段上下文。续不上时走这条（也是复盘之前唯一的一条）。"""
    messages = [{"role": "system", "content": _system_prompt(vault, req.stance, req.project)},
                *history]
    if dropped:
        # **截断要说出来**，不能让它默默失忆：模型不知道自己少了上下文时，
        # 会拿半截记忆当完整的用，比直接说"我没看到"糟得多。
        # 真正的长期记忆本来就不该是上下文窗口——聊清楚的东西应该已经进 md 了，
        # 所以这里顺便告诉它：缺的部分去图里查，或者问我。
        messages.insert(1, {"role": "user", "content":
            f"（提醒：这一段之前还有 {dropped} 轮没带过来。你缺的上下文别猜——"
            f"先 `search_nodes` / `read_node` 去图里找，找不到就直接问我。）"})
    return messages


def _assemble(vault: Path, req: ChatRequest, history: list[dict], dropped: int) -> list[dict]:
    """拼这一轮要发的消息。**能续就只追加最后那句话**，续不上才整段重建（复盘 §11.2）。

    要守住的性质只有一条：**只增不改**。anthropic 靠缓存断点、openai 靠自动前缀缓存——
    两条路要的是同一件事，前面那一截一个字都别动。
    前端回传的可见轮次不再用来重建上下文，只用来证明"这一段没被改过"（见 turns.resume）。
    """
    prior = turns.resume(vault, llm_session_key(req), history)
    messages = [*prior, history[-1]] if prior else _rebuild(vault, req, history, dropped)
    # 图谱现状挂在**队尾**，不进顶层 system（见 _graph_snapshot）。
    # 工具循环随后往后追加 assistant / 工具结果，它就夹在中间——这是允许的位置
    # （mid-conversation system message 要么是最后一条，要么后面跟着 assistant）。
    # **没变就不重复贴**：贴一条就动一次前缀，而"建一个节点就换一次前缀"正是这么来的。
    snapshot = _graph_snapshot(vault)
    if _last_snapshot(messages) != snapshot:
        messages.append({"role": "system", "content": snapshot})
    return messages


def _invoke(vault: Path, name: str, args: dict, allowed: set, cached: str | None) -> tuple[str, dict]:
    """跑一个工具。**三条非正常路径一条都不抛**，全都把话说给模型听，对话继续往下走。

    1. 同参数重复调用：真实对话里模型会用一模一样的参数再搜一遍，每重复一次白烧一个来回。
       第二次直接还回上次的结果，并明说别再调了。
    2. 调了白名单外的工具：回一句"这一档没有它，可用的是…"，不报错。
    3. 工具自己炸了：告诉模型它炸了，同时记一笔——工具出错原来只在那一轮对话里闪一下，
       "这东西为什么老出问题"没有任何地方能回答（layer 那个白名单 bug 就是这么藏了一阵）。
    """
    if cached is not None:
        return (f"这次调用和刚才那次一模一样，结果没变，不再跑一遍：\n{cached}"
                f"\n\n**直接用这个结果回答，不要再调工具了。**"), {}
    fn = TOOLS.get(name) if name in allowed else None
    if fn is None:
        return f"这一档口径下没有 `{name}` 这个工具。可用的是：{'、'.join(sorted(allowed))}。", {}
    try:
        return fn(vault, args)
    except Exception as exc:
        log.warning("工具 %s 失败：%s", name, exc)
        core.record_issue(vault, "tool", f"{type(exc).__name__}: {exc}", where=name,
                          detail={"args": json.dumps(args, ensure_ascii=False)[:200]})
        return f"工具 `{name}` 执行失败：{exc}", {}


def _tool_events(name: str, args: dict, result: str, extra: dict):
    """一次工具调用要往 SSE 上推的那几条。**事件是有类型的**，前端按类型渲染不解析文本。"""
    yield {"type": "tool", "name": name, "args": args,
           "summary": result[:200], **{k: v for k, v in extra.items() if k in ("card", "quiz")}}
    for field, key in (("card", "card"), ("project", "project"), ("points", "points"),
                       ("list_edit", "list_edit")):
        if extra.get(field):
            yield {"type": key, key: extra[field]}
    if extra.get("id") and name == "record_review":
        yield {"type": "review", "id": extra["id"], "next_due": extra.get("next_due")}


def _step_calls(vault: Path, req: ChatRequest, calls: list[dict], turn: dict):
    """跑完这一步的全部工具调用，`yield` 事件，返回要接到消息列表尾巴上的那几条结果。

    **一步里可以有好几个工具调用。** 文本围栏时代只能一次一个（一个块、然后停下），
    于是"搜一下再读三个节点"要烧掉四个来回。原生协议本来就允许并行，这里跟着放开。

    `turn` 是这一轮的随身物：`allowed`（白名单）、`seen_calls`（同参去重）、`user_ts`（留档锚点）。
    """
    results: list[dict] = []
    for call in calls:
        name, raw_args = call["name"], call.get("args") or {}
        key = f"{name}:{json.dumps(raw_args, ensure_ascii=False, sort_keys=True)}"
        # 当前项目 / 会话 / 这一轮的锚点跟着一起传进工具：在某个项目里聊天，today 和出题范围
        # 都该是这个项目的；卡片流水要靠 session 和 turn 把卡和回合对上。
        # 放在 key 之后算，免得它们进了去重键。
        args = {**raw_args, "_project": req.project or "", "_session": req.session or "",
                "_turn": turn["user_ts"] or ""}
        result, extra = _invoke(vault, name, args, turn["allowed"], turn["seen_calls"].get(key))
        turn["seen_calls"].setdefault(key, result)
        yield from _tool_events(name, args, result, extra)
        results.append({"role": "tool", "tool_call_id": call.get("id") or "",
                        "name": name, "content": result})
    return results


def _wrap_up(vault: Path, req: ChatRequest, history: list[dict], turn: dict):
    """收尾：算高亮、摘检验题、存续接缓存、留档，最后推 `done`。

    `turn` 带着循环跑出来的东西：`said` / `answer` / `last_raw` / `messages` / `usage` / `user_ts`。
    """
    trace = [t for t in turn["said"] if t.strip()]
    # 高亮按"这一轮提到过谁"算，所以连过程一起看——图上该亮的节点常常是查出来的那个
    touched = _mentioned(vault, "\n\n".join([*trace, turn["answer"]]))
    # 讲完一段随口问的那个检验问题：攒进题库。它是**在我刚学完那一刻、对着我当时的理解**
    # 提出来的，比事后让模型看着 md 现编的题贴身；而且它已经生成过一次了，别再付第二次钱。
    answer, checks = pull_checks(turn["answer"], touched)
    for c in checks:
        try:
            row = core.add_question(vault, c["stem"], c["points"], source="chat")
        except OSError as exc:
            log.warning("题库没写上：%s", exc)
            continue
        if row:
            yield {"type": "question", "stem": row["stem"], "points": row["points"]}
    # 记下这一轮，下一轮就能只发增量（复盘 §11.2）。两份列表各有用处：
    # `visible` 是下一轮用来证明"这一段没被改过"的，必须和前端会回传的内容**逐字一致**——
    # 所以存的是 pull_checks 之后的 answer（```check 围栏已经摘掉，前端拿到的就是它）。
    # 步数用尽那一轮不存：它的内部列表结尾是半截工具结果，接着往下发没有意义。
    if turn["last_raw"] and answer.strip():
        turns.remember(vault, llm_session_key(req),
                       visible=[*history, {"role": "assistant", "content": answer}],
                       messages=[*turn["messages"], {"role": "assistant", "content": turn["last_raw"]}])
    ts = append_log(vault, "assistant", answer, node_ids=touched, project=req.project,
                    session=req.session, stance=req.stance or DEFAULT_STANCE, trace=trace)
    # node_ids 从调试信息升级成了界面契约：「聊到哪、图上亮哪」靠它（重构方案 §8 第 4 条）
    # ts / user_ts 同理：梳理游标就停在某一条留档上，界面得知道这两条各自是哪一条。
    yield {"type": "done", "text": answer, "trace": trace, "usage": turn["usage"],
           "node_ids": touched, "ts": ts, "user_ts": turn["user_ts"]}


def _run(vault: Path, req: ChatRequest):
    history, dropped = _fit_history([m.model_dump() for m in req.messages])
    if not history or history[-1]["role"] != "user":
        raise ChatRejected("最后一条必须是我说的话")
    allowed = tools_of(req.stance, vault)        # 和说明书、schema 同一份数据，复习关掉就真的调不动
    turn = {
        "user_ts": append_log(vault, "user", history[-1]["content"], project=req.project,
                              session=req.session, stance=req.stance or DEFAULT_STANCE),
        "allowed": set(allowed),
        # 同一轮里同参数的工具调用只真跑一次：模型确实会连着用一模一样的参数再搜一遍
        # （真实对话里观察到的），每重复一次就白烧一个来回。
        "seen_calls": {},
        "said": [],            # 过程：每一次"还要接着调工具"的那段话
        "answer": "",
        "last_raw": "",        # 收尾那段的**原文**：存进续接缓存的是它，不是剥过工具块的版本
        "messages": _assemble(vault, req, history, dropped),
        "usage": {},
    }
    tools = tool_schemas(allowed)
    for _ in range(MAX_STEPS):
        text, calls = "", []
        for ev in _stream(vault, turn["messages"], tools, op=f"chat-{req.stance or DEFAULT_STANCE}",
                          session=llm_session_key(req)):
            if ev["type"] == "delta":
                yield ev
            else:
                text, calls, turn["usage"] = ev["text"], ev["calls"], ev["usage"]
        step_text = strip_tools(text)
        if not calls:
            # 不再调工具 = 这一段就是答案本身。前面那些"我先查一下""工具挂了"是过程，
            # 拼进正文的话，每次都要在一堆过程里找那几句有营养的（真实使用里最费时间的一点）。
            turn["answer"], turn["last_raw"] = step_text, text
            break
        turn["said"].append(step_text)
        results = yield from _step_calls(vault, req, calls, turn)
        turn["messages"] = turn["messages"] + [{"role": "assistant", "content": text,
                                                "tool_calls": calls}] + results
    else:
        yield {"type": "tool", "name": "（停）", "args": {},
               "summary": f"连着调了 {MAX_STEPS} 次工具还没给出回答，这一轮到此为止。"}
        # 用尽了步数：最后说的那段当答案
        turn["answer"] = turn["said"].pop() if turn["said"] else ""

    yield from _wrap_up(vault, req, history, turn)


def _mentioned(vault: Path, text: str) -> list[str]:
    """这一轮聊到了哪些节点。留档时标上，回溯"这个点当时怎么讲的"直接按 id grep。"""
    if not text:
        return []
    ids = [n["id"] for n in current_index(vault)["nodes"] if not n.get("virtual")]
    return [nid for nid in ids if nid in text][:20]
