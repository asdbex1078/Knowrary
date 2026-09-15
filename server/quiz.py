"""测验：按节点出题（LLM，review 角色），以及记录我自评的三档结果。

和 suggest 的关系：同一套「组 prompt → 问 LLM → 解析 JSON → 只提议不落地」范式，
区别是 suggest 提议改图（要走 ChangeSet 确认），quiz 只改复习调度，永不碰 md。
"""
from __future__ import annotations

import logging
from pathlib import Path

from .contracts import (QuizAnswer, QuizDiagnosis, QuizDiagnosisItem, QuizGraded, QuizQuestion,
                        QuizRequest, QuizSet)

# 复习和面试考的是两件事：一个问"你还记不记得"，一个问"你讲不讲得出来、经不经得起追问"。
# 同一批节点用哪套口径由调用方指定，不猜。
STYLES = {
    "复习": ("你是我的个人知识图谱的复习考官。",
             "这是**再考一次**，不是再看一遍。题目要能逼我回忆，而不是读一遍就能答。\n"
             "判据是我**记没记住**，所以问事实、问机制、问方向。"),
    "面试": ("你是一位资深面试官{{coach}}，正在面我。",
             "这是**面试**，判据是我**讲不讲得出来**，不是记没记得住。所以：\n"
             "- 题干用面试官的问法（「说说……」「为什么不用……」「线上出现……你怎么查」），不要像填空题。\n"
             "- 每题的标准答案要写出**答到什么程度才算过**，并点出最常见的答漏之处。\n"
             "- 优先问那些**答一半就会露怯**的点，而不是背下来就能答的定义。"),
}
from .index_service import current_index
from .llm_call import ask, parse_json
from .paths import core

log = logging.getLogger(__name__)

_PROMPTS: dict[str, str] = {}
MAX_NODES = 12          # 一次最多拿多少个节点去出题：再多 prompt 就超长且题会变水
MAX_BODY = 1200         # 每个节点的正文摘录上限
MAX_MY_ANSWER = 2000    # 我写的答案留档上限


def _load_prompt_template(name: str = "quiz") -> str:
    if name not in _PROMPTS:
        path = Path(__file__).resolve().parents[1] / "tools" / "knowrary" / "prompts" / f"{name}.md"
        _PROMPTS[name] = path.read_text(encoding="utf-8")
    return _PROMPTS[name]


def _body_excerpt(vault: Path, meta: dict) -> str:
    """节点正文（`## 关系` 之前那段）的摘录。读不到就退回 desc。"""
    path = meta.get("path")
    if not path:
        return meta.get("desc") or ""
    try:
        _, body, _, _ = core.split_sections(core.read(vault / path))
    except OSError:
        return meta.get("desc") or ""
    text = body.strip()
    return text[:MAX_BODY] + ("…" if len(text) > MAX_BODY else "")


def _edge_lines(index: dict, node_id: str, names: dict[str, str]) -> list[str]:
    out = []
    for e in index["edges"]:
        if e["source"] == node_id:
            out.append(f"  - {e['type']} → {e['target']}（{names.get(e['target'], e['target'])}）")
        elif e["target"] == node_id:
            out.append(f"  - {e['source']}（{names.get(e['source'], e['source'])}） → {e['type']} → 本节点")
    return out


def _node_block(vault: Path, index: dict, meta: dict, names: dict[str, str]) -> str:
    edges = _edge_lines(index, meta["id"], names)
    return "\n".join([
        f"### {meta['id']}",
        f"- name: {meta.get('name') or meta['id']}",
        f"- field: {meta.get('field') or '（未设置）'}",
        f"- desc: {meta.get('desc') or '（未设置）'}",
        "- 正文：",
        _body_excerpt(vault, meta) or "（无正文）",
        "- 已有关系：",
        *(edges or ["  （这个节点还没有任何边，不要为它出关系题）"]),
    ])


def generate(vault: Path, req: QuizRequest) -> QuizSet:
    index = current_index(vault)
    by_id = {n["id"]: n for n in index["nodes"]}
    names = {n["id"]: (n.get("name") or n["id"]) for n in index["nodes"]}

    warnings: list[str] = []
    metas = []
    for nid in req.node_ids[:MAX_NODES]:
        meta = by_id.get(nid)
        if meta is None or meta.get("virtual") or meta.get("stub"):
            warnings.append(f"跳过 `{nid}`：不在索引里，或只是没有 md 的占位 stub")
            continue
        metas.append(meta)
    if len(req.node_ids) > MAX_NODES:
        warnings.append(f"一次最多考 {MAX_NODES} 个节点，其余的留到下一轮")
    if not metas:
        return QuizSet(questions=[], index_revision=index["revision"], warnings=warnings)

    blocks = "\n\n".join(_node_block(vault, index, m, names) for m in metas)
    role, style = STYLES.get(req.style, STYLES["复习"])
    coach = f"，方向是{req.coach.strip()}" if req.coach.strip() else ""
    prompt = (_load_prompt_template()
              .replace("{{role}}", role.replace("{{coach}}", coach))
              .replace("{{style}}", style)
              .replace("{{nodes}}", blocks)
              .replace("{{count}}", str(req.count)))
    raw = ask(vault, "review", prompt, op="quiz" if req.style == "复习" else "quiz-interview")
    questions, bad = _parse_questions(parse_json(raw, f"quiz {len(metas)} 节点"), set(by_id))
    warnings.extend(bad)
    return QuizSet(questions=questions, index_revision=index["revision"], warnings=warnings)


def _parse_questions(data: dict, known_ids: set[str]) -> tuple[list[QuizQuestion], list[str]]:
    """把 LLM 的输出收成题目列表。考点必须是真实存在的节点 id——填错等于把复习记到别人头上。"""
    questions, warnings = [], []
    for item in data.get("questions") or []:
        if not isinstance(item, dict):
            continue
        stem = str(item.get("stem") or "").strip()
        if not stem:
            continue
        raw_points = [str(p) for p in (item.get("points") or [])]
        points = [p for p in raw_points if p in known_ids]
        missing = [p for p in raw_points if p not in known_ids]
        if missing:
            warnings.append(f"丢弃不存在的考点 {'、'.join(missing)}（题目：{stem[:24]}…）")
        if not points:
            warnings.append(f"跳过一道没有有效考点的题：{stem[:24]}…")
            continue
        questions.append(QuizQuestion(
            type=str(item.get("type") or "回忆题"),
            stem=stem,
            answer=str(item.get("answer") or ""),
            points=points,
            hint=str(item.get("hint") or ""),
        ))
    return questions, warnings


def _worst_grade_per_node(answers: list[QuizAnswer]) -> dict[str, str]:
    """一个节点在同一轮里被考到多次时，取最差的一档。

    否则一轮答三道题就给这个节点记三次复习，间隔序号会凭空跳三级。
    """
    rank = {core.GRADES[0]: 0, core.GRADES[1]: 1, core.GRADES[2]: 2}   # 记得 < 模糊 < 忘了
    worst: dict[str, str] = {}
    for ans in answers:
        for nid in ans.question.points:
            if nid not in worst or rank[ans.grade] > rank[worst[nid]]:
                worst[nid] = ans.grade
    return worst


def grade(vault: Path, answers: list[QuizAnswer]) -> QuizGraded:
    """记一轮测验：答题明细进 quiz-log，每个考点按最差档位推进一次复习调度。"""
    index = current_index(vault)
    known = {n["id"] for n in index["nodes"] if not n.get("virtual")}

    core.append_answers(vault, [
        {"points": [p for p in a.question.points if p in known], "type": a.question.type,
         "stem": a.question.stem, "answer": a.question.answer, "grade": a.grade,
         "my_answer": a.my_answer, "missed": a.missed, "wrong": a.wrong_points}
        for a in answers])

    reviewed, wrong = [], []
    for nid, g in sorted(_worst_grade_per_node(answers).items()):
        if nid not in known:
            continue
        entry = core.record_review(vault, nid, grade=g)
        reviewed.append({"id": nid, "grade": g, "step": entry["step"],
                         "lapses": entry["lapses"], "next_due": entry.get("next_due")})
        if g == core.GRADES[2]:
            wrong.append(nid)
    return QuizGraded(reviewed=reviewed, wrong=wrong)


# ---------------------------------------------------------------- 比对诊断

def _answer_block(i: int, a: QuizAnswer) -> str:
    mine = (a.my_answer or "").strip()
    return "\n".join([
        f"### 第 {i} 题（{a.question.type}）",
        f"- 题干：{a.question.stem}",
        f"- 标准答案：{a.question.answer or '（没有标准答案，这题只按我的作答判）'}",
        "- 我写的：",
        mine[:MAX_MY_ANSWER] if mine else "（空着没答）",
    ])


def diagnose(vault: Path, answers: list[QuizAnswer]) -> QuizDiagnosis:
    """整轮比对：我写的 vs 标准答案，给出漏掉点、记错点和建议档位。

    整轮一次调用，不逐题调——逐题会让每道题都卡几秒，答题节奏全毁；
    而且诊断本来就该出现在"答完想看我缺在哪"的那一刻。
    """
    if not answers:
        return QuizDiagnosis()
    blocks = "\n\n".join(_answer_block(i, a) for i, a in enumerate(answers, 1))
    prompt = _load_prompt_template("quiz-review").replace("{{answers}}", blocks)
    raw = ask(vault, "review", prompt, op="quiz-diagnose")
    return _parse_diagnosis(parse_json(raw, f"diagnose {len(answers)} 题"), len(answers))


def _parse_diagnosis(data: dict, total: int) -> QuizDiagnosis:
    items, warnings, seen = [], [], set()
    for it in data.get("items") or []:
        if not isinstance(it, dict):
            continue
        try:
            n = int(it.get("n"))
        except (TypeError, ValueError):
            continue
        if not 1 <= n <= total or n in seen:
            warnings.append(f"丢弃对不上号的第 {it.get('n')} 题诊断")
            continue
        seen.add(n)
        grade = it.get("suggested_grade")
        items.append(QuizDiagnosisItem(
            n=n,
            missed=[str(x) for x in (it.get("missed") or []) if str(x).strip()],
            wrong=[str(x) for x in (it.get("wrong") or []) if str(x).strip()],
            suggested_grade=grade if grade in core.GRADES else "模糊",
            comment=str(it.get("comment") or ""),
        ))
    if len(items) < total:
        warnings.append(f"模型只诊断了 {len(items)} / {total} 题，缺的那几题按你的自评算")
    items.sort(key=lambda x: x.n)
    return QuizDiagnosis(items=items, warnings=warnings)
