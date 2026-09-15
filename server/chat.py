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
from .plans import read as read_plans
from .quiz import generate as generate_quiz


log = logging.getLogger(__name__)

MAX_STEPS = 4            # 一条消息里最多连续调几次工具：再多就是模型在原地打转
MAX_MESSAGES = 60        # 往回带几轮对话：更早的自己去 grep chat/*.jsonl
SEARCH_TOP = 8
BODY_CHARS = 1200
_PROMPT: str | None = None

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


def _tool_plans(vault: Path, args: dict) -> tuple[str, dict]:
    data = read_plans(vault)
    out = {}
    for pid, plan in data.doc.plans.items():
        sched = data.schedules.get(pid) or {}
        prog = data.progress.get(pid) or {}
        out[plan.name or pid] = {
            "类型": plan.kind, "目标": plan.goal, "目标日期": plan.target_date,
            "进度": f"{prog.get('built', 0)}/{prog.get('total', 0)}",
            "时间账": {"还剩": f"{sched.get('remaining_hours')} 小时", "判定": sched.get("verdict"),
                       "现实日期": sched.get("suggested_target_date"), "落后": sched.get("behind")},
            "各档": prog.get("counts")}
    return json.dumps(out, ensure_ascii=False) or "还没有学习计划。", {"plans": len(out)}


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


TOOLS = {
    "search_nodes": _tool_search,
    "read_node": _tool_read,
    "overview": _tool_overview,
    "today": _tool_today,
    "plans": _tool_plans,
    "quiz": _tool_quiz,
    "record_review": _tool_review,
    "propose_changes": _tool_propose,
}


# ---------------------------------------------------------------- 对话留档（F10.7）

def chat_log_path(vault: Path, today: dt.date | None = None) -> Path:
    today = today or dt.date.today()
    return vault / ".knowrary" / "chat" / f"{today:%Y-%m}.jsonl"


def append_log(vault: Path, role: str, text: str, node_ids: list[str] | None = None) -> None:
    """一行一轮，按月分文件。**不建库、不切分、不做 embedding**（F10.7）：
    对话是过程不是知识，检索系统已经存在，就是那张图。找旧对话用 grep。"""
    path = chat_log_path(vault)
    row = {"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "role": role, "text": text, "node_ids": node_ids or []}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:                       # 留档是旁路，坏了不能拖垮对话
        log.warning("对话没记上：%s", exc)


# ---------------------------------------------------------------- 编排

def _system_prompt(vault: Path) -> str:
    global _PROMPT
    if _PROMPT is None:
        path = Path(__file__).resolve().parents[1] / "tools" / "knowrary" / "prompts" / "chat.md"
        _PROMPT = path.read_text(encoding="utf-8")
    return _PROMPT.replace("{{overview}}", _overview(vault))


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


def _stream(vault: Path, messages: list[dict]):
    """在后台线程里跑一次 LLM 调用，把增量从队列里取出来往外 yield。

    生成器里没法从回调 yield，所以只能用队列过一道。
    """
    q: queue.Queue = queue.Queue()
    box: dict = {}

    def work() -> None:
        try:
            box["text"], box["usage"] = llm_chat(vault, "learn", messages, op="chat",
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
    append_log(vault, "user", history[-1]["content"])

    messages = [{"role": "system", "content": _system_prompt(vault)}] + history
    said: list[str] = []
    # 同一轮里同参数的工具调用只真跑一次：模型确实会连着用一模一样的参数再搜一遍
    # （真实对话里观察到的），每重复一次就白烧一个来回。
    seen_calls: dict[str, str] = {}
    for step in range(MAX_STEPS):
        text = ""
        usage: dict = {}
        for ev in _stream(vault, messages):
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
        fn = TOOLS.get(name)
        if key in seen_calls:
            result, extra = (f"这次调用和刚才那次一模一样，结果没变，不再跑一遍：\n{seen_calls[key]}"
                             f"\n\n**直接用这个结果回答，不要再调工具了。**"), {}
        elif fn is None:
            result, extra = f"没有 `{name}` 这个工具。可用的是：{'、'.join(TOOLS)}。", {}
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
        if extra.get("id") and name == "record_review":
            yield {"type": "review", "id": extra["id"], "next_due": extra.get("next_due")}

        messages = messages + [{"role": "assistant", "content": text},
                               {"role": "user", "content": f"[工具 {name} 的结果]\n{result}"}]
    else:
        yield {"type": "tool", "name": "（停）", "args": {},
               "summary": f"连着调了 {MAX_STEPS} 次工具还没给出回答，这一轮到此为止。"}

    answer = "\n\n".join(t for t in said if t.strip())
    append_log(vault, "assistant", answer, node_ids=_mentioned(vault, answer))
    yield {"type": "done", "text": answer, "usage": usage}


def _mentioned(vault: Path, text: str) -> list[str]:
    """这一轮聊到了哪些节点。留档时标上，回溯"这个点当时怎么讲的"直接按 id grep。"""
    if not text:
        return []
    ids = [n["id"] for n in current_index(vault)["nodes"] if not n.get("virtual")]
    return [nid for nid in ids if nid in text][:20]
