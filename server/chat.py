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
import threading

from pathlib import Path

from . import curation
from .contracts import ChatRequest, QuizRequest
from .index_service import current_index
from .llm_call import chat as llm_chat
from .paths import core
from .projects import read as read_projects
from .quiz import generate as generate_quiz


log = logging.getLogger(__name__)

MAX_STEPS = 4            # 一条消息里最多连续调几次工具：再多就是模型在原地打转
MAX_MESSAGES = 60        # 往回带几轮对话：更早的自己去 grep chat/*.jsonl
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
            f"\n领域分布：{tops or '（还没有领域）'}")


def _tool_search(vault: Path, args: dict) -> tuple[str, dict]:
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
             "field": n.get("field"), "只有壳": bool(n.get("stub"))} for n in hits[:limit]]
    body = json.dumps(rows, ensure_ascii=False) if rows else f"图里没有和「{q}」匹配的节点。"
    return body, {"hits": len(rows)}


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
    today = curation.coach_today(vault).model_dump()
    rows = [{"类型": it["kind"], "id": it["id"], "说明": it.get("detail")} for it in today["items"]]
    return json.dumps({"清单": rows, "计划": today["plans"]}, ensure_ascii=False), {"items": len(rows)}


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
    card = {"id": pid, "action": "update" if exists else "create",
            "name": str(args.get("name") or pid)[:120],
            "field": str(args.get("field") or "")[:120],
            "weekly_hours": int(args.get("weekly_hours") or 7),
            "daily_quota": int(args.get("daily_quota") or 2),
            "lists": lists}
    what = f"往「{exists.get('name') or pid}」里加 {len(lists)} 份清单" if exists else f"新建项目「{card['name']}"
    return (f"{what}」的卡片已经摆在他面前了。**还没建**，等他点「创建」。"
            f"建完之后清单还是空的——要填点，让他点「让 AI 拆一份」，或者你继续问清楚目标再提议。"), {"project": card}


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
    card = {"changes": changes, "base_revision": index["revision"],
            "files": [f.model_dump() for f in files]}
    return (f"变更卡已经摆在他面前了（{len(files)} 个文件）。**还没有写盘**，"
            f"等他点「写入」。你不要再说已经存好了。"), {"card": card}


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
    "propose_changes": """`propose_changes` 的 `changes` 和图谱的 ChangeSet 同一个形状：

```knowrary
{"tool": "propose_changes", "args": {"changes": [
  {"type": "create_node", "source": "自注意力", "path": "nodes/深度学习/自注意力.md",
   "fields": {"name": "自注意力", "field": "深度学习", "desc": "一句话摘要"},
   "body": "正文，讲清楚这个概念"},
  {"type": "add_edge", "source": "自注意力", "relation": "部件", "target": "Transformer"}
]}}
```""",
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
               stance: str | None = None) -> None:
    """一行一轮，按月分文件。**不建库、不切分、不做 embedding**（F10.7）：
    对话是过程不是知识，检索系统已经存在，就是那张图。找旧对话用 grep。

    `session` 只是行上的一个标签——**不建会话表、不存会话元数据**。
    会话列表是从这些行里聚合出来的，和进度、时间账一样是派生的（不落第二份真值）。
    """
    path = chat_log_path(vault, project)
    row = {"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "role": role, "text": text, "node_ids": node_ids or [], "session": session or "",
           "stance": stance or ""}
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
    for b in out:
        b["title"] = b["title"] or "（没说什么）"
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
    return [{"role": r["role"], "content": r["text"], "node_ids": r.get("node_ids") or []}
            for r in rows][-limit:]


# ---------------------------------------------------------------- 编排

def _prompt_file(name: str) -> str:
    if name not in _PROMPTS:
        path = Path(__file__).resolve().parents[1] / "tools" / "knowrary" / "prompts" / f"{name}.md"
        _PROMPTS[name] = path.read_text(encoding="utf-8")
    return _PROMPTS[name]


def stance_of(name: str | None) -> dict:
    return STANCES.get(name or DEFAULT_STANCE, STANCES[DEFAULT_STANCE])


def _system_prompt(vault: Path, stance: str | None) -> str:
    """基底一份 + 口径一份。工具表从白名单渲染——**说明书和实际权限是同一份数据**，
    两边各写一遍迟早对不上（"表里写着能用、调了却说没有"是最让人发火的那种 bug）。"""
    conf = stance_of(stance)
    body = _prompt_file(conf["file"])
    intro, _, rules = body.partition("## 规矩")
    table = "\n".join(f"| `{t}` | {TOOL_DOC[t]} |" for t in conf["tools"] if t in TOOL_DOC)
    formats = "\n\n".join(FORMAT_DOC[t] for t in conf["tools"] if t in FORMAT_DOC)
    return (_prompt_file("chat")
            .replace("{{overview}}", _overview(vault))
            .replace("{{tools}}", table)
            .replace("{{formats}}", formats)
            .replace("{{stance_intro}}", intro.strip())
            .replace("{{stance_rules}}", rules.strip() or "（没有额外规矩）"))


def strip_tools(text: str) -> str:
    """把工具块从要给人看的文本里摘掉。

    流式增量里也会带着这个块，前端按同一条正则过滤——**两边用同一个形状**，
    否则会出现"聊天记录里没有、屏幕上闪过一段 JSON"这种事。
    """
    return _TOOL_RE.sub("", text or "").strip()


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
    """一条消息的完整回合：可能夹着几次工具调用。逐个 yield 事件给 SSE。"""
    history = [m.model_dump() for m in req.messages][-MAX_MESSAGES:]
    if not history or history[-1]["role"] != "user":
        raise ChatRejected("最后一条必须是我说的话")
    append_log(vault, "user", history[-1]["content"], project=req.project, session=req.session,
               stance=req.stance or DEFAULT_STANCE)

    conf = stance_of(req.stance)
    allowed = set(conf["tools"])
    messages = [{"role": "system", "content": _system_prompt(vault, req.stance)}] + history
    said: list[str] = []
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
        said.append(strip_tools(text))
        call = _parse_tool(text)
        if not call:
            break

        name, args = call
        key = f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
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
                result, extra = f"工具 `{name}` 执行失败：{exc}", {}
        seen_calls.setdefault(key, result)
        yield {"type": "tool", "name": name, "args": args,
               "summary": result[:200], **{k: v for k, v in extra.items() if k in ("card", "quiz")}}
        if extra.get("card"):
            yield {"type": "card", "card": extra["card"]}
        if extra.get("project"):
            yield {"type": "project", "project": extra["project"]}
        if extra.get("id") and name == "record_review":
            yield {"type": "review", "id": extra["id"], "next_due": extra.get("next_due")}

        messages = messages + [{"role": "assistant", "content": text},
                               {"role": "user", "content": f"[工具 {name} 的结果]\n{result}"}]
    else:
        yield {"type": "tool", "name": "（停）", "args": {},
               "summary": f"连着调了 {MAX_STEPS} 次工具还没给出回答，这一轮到此为止。"}

    answer = "\n\n".join(t for t in said if t.strip())
    touched = _mentioned(vault, answer)
    append_log(vault, "assistant", answer, node_ids=touched, project=req.project,
               session=req.session, stance=req.stance or DEFAULT_STANCE)
    # node_ids 从调试信息升级成了界面契约：「聊到哪、图上亮哪」靠它（重构方案 §8 第 4 条）
    yield {"type": "done", "text": answer, "usage": usage, "node_ids": touched}


def _mentioned(vault: Path, text: str) -> list[str]:
    """这一轮聊到了哪些节点。留档时标上，回溯"这个点当时怎么讲的"直接按 id grep。"""
    if not text:
        return []
    ids = [n["id"] for n in current_index(vault)["nodes"] if not n.get("virtual")]
    return [nid for nid in ids if nid in text][:20]
