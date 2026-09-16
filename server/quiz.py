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
from .levels import fragment as level_fragment
from .llm_call import ask, parse_json
from .paths import core

core_norm = core.pool_norm          # 判重用的规整题面：题库和批改必须用同一把尺子

log = logging.getLogger(__name__)

_PROMPTS: dict[str, str] = {}
MAX_NODES = 12          # 一次最多拿多少个节点去出题：再多 prompt 就超长且题会变水
MAX_BODY = 1200         # 每个节点的正文摘录上限
MAX_MY_ANSWER = 2000    # 我写的答案留档上限
MAX_SOURCE = 700        # 批改时每个节点带多少正文：够补出标准答案就行，不是把 md 整篇塞进去
NO_ANSWER = "（还没有标准答案——请照下面「考点的笔记正文」补一份，填进 ref_answer）"


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


def _pick_nodes(by_id: dict, node_ids: list[str]) -> tuple[list[dict], list[str]]:
    """从请求里挑出真能出题的节点，顺带记下跳过了谁。

    stub 和 virtual 节点没有正文，拿去出题只会让模型编——考没学过的内容，
    答不出来就不是"我忘了"，复习调度也就被记错了。
    """
    warnings: list[str] = []
    metas = []
    for nid in node_ids[:MAX_NODES]:
        meta = by_id.get(nid)
        if meta is None or meta.get("virtual") or meta.get("stub"):
            warnings.append(f"跳过 `{nid}`：不在索引里，或只是没有 md 的占位 stub")
            continue
        metas.append(meta)
    if len(node_ids) > MAX_NODES:
        warnings.append(f"一次最多考 {MAX_NODES} 个节点，其余的留到下一轮")
    return metas, warnings


def generate(vault: Path, req: QuizRequest) -> QuizSet:
    index = current_index(vault)
    by_id = {n["id"]: n for n in index["nodes"]}
    names = {n["id"]: (n.get("name") or n["id"]) for n in index["nodes"]}

    metas, warnings = _pick_nodes(by_id, req.node_ids)
    if not metas:
        return QuizSet(questions=[], index_revision=index["revision"], level=req.level, warnings=warnings)

    # 先用题库里现成的：聊天时教练已经问过我的那些题，是在我刚学完那一刻针对我提的，
    # 比事后看着 md 现编的贴身；而且它已经生成过一次了，再出一遍等于为同一件事付两次钱。
    ready = _from_pool(vault, [m["id"] for m in metas], req)
    if len(ready) >= req.count:
        warnings.append(f"这 {len(ready)} 题都来自你聊天时被问过的，没调模型")
        out = QuizSet(questions=ready[:req.count], index_revision=index["revision"],
                      level=req.level, warnings=warnings)
        _keep_open(vault, out, index, req)
        return out

    blocks = "\n\n".join(_node_block(vault, index, m, names) for m in metas)
    role, style = STYLES.get(req.style, STYLES["复习"])
    coach = f"，方向是{req.coach.strip()}" if req.coach.strip() else ""
    prompt = (_load_prompt_template()
              .replace("{{role}}", role.replace("{{coach}}", coach))
              .replace("{{style}}", style)
              .replace("{{nodes}}", blocks)
              .replace("{{level}}", level_fragment(req.level, "quiz"))
              .replace("{{count}}", str(req.count - len(ready))))
    raw = ask(vault, "review", prompt, op="quiz" if req.style == "复习" else "quiz-interview")
    questions, bad = _parse_questions(parse_json(raw, f"quiz {len(metas)} 节点", vault=vault), set(by_id))
    warnings.extend(bad)
    if not questions:
        # 这一次调用已经花了钱和时间。只留一句「没出出题来」的话，下次还是查不出为什么，
        # 所以把模型原文的开头一并留在问题流和 warnings 里。
        head = " ".join(raw.split())[:120]
        warnings.append(f"模型这次没给出可用题目，原文开头：{head or '（空）'}")
        core.record_issue(vault, "llm", f"出题没出来（{len(metas)} 个节点）", where="quiz",
                          detail=raw[:600])
    if ready:
        warnings.append(f"其中 {len(ready)} 题来自你聊天时被问过的")
    _stash(vault, questions)
    out = QuizSet(questions=ready + questions, index_revision=index["revision"],
                  level=req.level, warnings=warnings)
    _keep_open(vault, out, index, req)
    return out


def _stash(vault: Path, questions: list[QuizQuestion]) -> None:
    """把模型刚编出来的题连标准答案一起收进题库。

    不收的话，这次调用生成的题答完就没了，下次考同一个节点还得再生成一遍——
    题库本来就是为了"同一件事不付两次钱"，而现编的题正是花了钱的那批。
    收进去之后它和聊天攒的题走同一条路：下次 `_from_pool` 优先拿现成的。
    """
    for q in questions:
        if not q.points:
            continue                     # 没考点的题进不了池子，_from_pool 是按节点找题的
        try:
            core.add_question(vault, q.stem, q.points, ref_answer=q.ref_answer, source="quiz")
        except OSError as exc:
            log.warning("题库没写上：%s", exc)
            return                       # 写不动就整批放弃，不逐条刷日志


def _from_pool(vault: Path, node_ids: list[str], req: QuizRequest) -> list[QuizQuestion]:
    """题库里挑几道。面试口径不用它——面试题要按岗位方向现问，攒下来的是复习题。"""
    if req.style != "复习":
        return []
    rows = core.pool_for_nodes(core.load_pool(vault), node_ids, limit=req.count)
    return [QuizQuestion(type="回忆题", stem=r["stem"], ref_answer=r.get("ref_answer") or "",
                         points=[p for p in r.get("points") or [] if p in set(node_ids)],
                         hint="") for r in rows if set(r.get("points") or []) & set(node_ids)]


def _keep_open(vault: Path, out: QuizSet, index: dict, req: QuizRequest) -> None:
    """出一次题是要花钱的：关掉对话框、刷新页面都不该让它没了。交卷或明确放弃才清。"""
    if not out.questions:
        return
    try:
        core.save_open(vault, {"questions": [q.model_dump() for q in out.questions],
                               "index_revision": index["revision"],
                               "style": req.style, "level": req.level or ""})
    except OSError as exc:
        log.warning("没答完的卷子没存上：%s", exc)


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
            # 模型偶尔会退回大众化的 `answer`，两个都认——认不出等于白烧一次出题调用
            ref_answer=str(item.get("ref_answer") or item.get("answer") or ""),
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
         "stem": a.question.stem, "ref_answer": a.question.ref_answer, "grade": a.grade,
         "my_answer": a.my_answer, "missed": a.missed, "wrong": a.wrong_points}
        for a in answers])

    # 答对过的题打上「学会」——下次抽查先紧着没把握的来（不是从此不考：间隔复习的意义
    # 正是"以为记住了的也要抽查"，什么时候抽仍由节点的 review-log 说了算）
    core.mark_questions(vault, [(a.question.stem, a.grade == core.GRADES[0]) for a in answers])

    reviewed, wrong = [], []
    for nid, g in sorted(_worst_grade_per_node(answers).items()):
        if nid not in known:
            continue
        entry = core.record_review(vault, nid, grade=g)
        reviewed.append({"id": nid, "grade": g, "step": entry["step"],
                         "lapses": entry["lapses"], "next_due": entry.get("next_due")})
        if g == core.GRADES[2]:
            wrong.append(nid)
    core.clear_open(vault)          # 这份卷子答完了
    return QuizGraded(reviewed=reviewed, wrong=wrong)


# ---------------------------------------------------------------- 比对诊断

def _answer_block(i: int, a: QuizAnswer, prior: dict[str, str]) -> str:
    """一道题喂给批改模型的那一段。`prior` 是题库里已有的完整答案，按题面索引。"""
    mine = (a.my_answer or "").strip()
    lines = [
        f"### 第 {i} 题（{a.question.type}）",
        f"- 题干：{a.question.stem}",
        f"- 标准答案：{a.question.ref_answer or NO_ANSWER}",
    ]
    # 这题以前答过，题库里已经攒下一版完整答案：带上它，让答案一轮轮长好，
    # 而不是每轮被重写成另一个随机版本。
    old = prior.get(core_norm(a.question.stem), "")
    if old:
        lines.append(f"- 上一轮写下的完整答案（沿用或改进它，没有实质改进就原样返回）：{old}")
    lines += ["- 我写的：", mine[:MAX_MY_ANSWER] if mine else "（空着没答）"]
    return "\n".join(lines)


def _sources_block(vault: Path, answers: list[QuizAnswer]) -> str:
    """把「没有标准答案」的那几题的考点正文摘出来，喂给批改。

    聊天时教练随口问的题只带题干和考点（`pull_checks` 从没读过节点正文），所以 `answer` 是空的。
    判分总得有依据，而依据**只能是我自己的笔记**——让模型拿它自己知道的来判，
    我会被按一个我从没设定过的标准要求。所以这里把正文带上，让它照正文补出标准答案。

    只摘缺答案的那几题：有答案的题再带一遍正文纯属白烧 token，而正文在出题时已经读过一次了。
    """
    need = {p for a in answers if not (a.question.ref_answer or "").strip() for p in a.question.points}
    if not need:
        return ""
    try:
        index = current_index(vault)
    except (OSError, RuntimeError) as exc:
        log.warning("批改时取不到索引，这轮不补标准答案：%s", exc)
        return ""               # 补不出来最多是这题以后还没答案，不该因此连批改都做不成
    by_id = {n["id"]: n for n in index["nodes"]}
    blocks = []
    for nid in sorted(need):
        meta = by_id.get(nid)
        if not meta or meta.get("stub") or meta.get("virtual"):
            continue            # 没有 md 的占位节点没有正文可抄
        body = _body_excerpt(vault, meta)[:MAX_SOURCE].strip()
        if body:
            blocks.append(f"### {meta.get('name') or nid}（{nid}）\n{body}")
    if not blocks:
        return ""
    return "## 考点的笔记正文\n\n" + "\n\n".join(blocks) + "\n\n"


def _prior_full(vault: Path) -> dict[str, str]:
    """题库里已有的完整答案，按规整题面索引。读不动就当没有，不该因此挡住批改。"""
    try:
        pool = core.load_pool(vault)
    except OSError:
        return {}
    return {core_norm(q.get("stem", "")): q["full_answer"]
            for q in pool.get("questions") or [] if q.get("full_answer")}


def _level_of(vault: Path, given: str | None) -> str | None:
    """这轮按哪一档判。前端没传就从没交的那份卷子里取——出题时用的哪一档，判分就该用哪一档。"""
    if given:
        return given
    try:
        return (core.load_open(vault) or {}).get("level") or None
    except OSError:
        return None


def diagnose(vault: Path, answers: list[QuizAnswer], level: str | None = None) -> QuizDiagnosis:
    """整轮比对：我写的 vs 标准答案，给出漏掉点、记错点、建议档位和一份完整答案。

    整轮一次调用，不逐题调——逐题会让每道题都卡几秒，答题节奏全毁；
    而且诊断本来就该出现在"答完想看我缺在哪"的那一刻。

    **判分按档位**：出题认档位而批改不认的话，「了解就行」的点会被按「精通」的尺子
    判成模糊，白白多复习一轮。档位前端没传就退回这份卷子出题时用的那一档。
    """
    if not answers:
        return QuizDiagnosis()
    prior = _prior_full(vault)
    blocks = "\n\n".join(_answer_block(i, a, prior) for i, a in enumerate(answers, 1))
    prompt = (_load_prompt_template("quiz-review")
              .replace("{{level}}", level_fragment(_level_of(vault, level), "judge"))
              .replace("{{sources}}", _sources_block(vault, answers))
              .replace("{{answers}}", blocks))
    raw = ask(vault, "review", prompt, op="quiz-diagnose")
    out = _parse_diagnosis(parse_json(raw, f"diagnose {len(answers)} 题", vault=vault), len(answers))
    _enrich_pool(vault, answers, out)
    return out


def _enrich_pool(vault: Path, answers: list[QuizAnswer], out: QuizDiagnosis) -> None:
    """把这轮批出来的答案回灌题库：下次这题被抽到，答案已经在手上了。

    两份分开走（规矩写在 core/pool.py 的 `enrich` 里）：`full_answer` 是模型写的，每轮覆盖；
    `ref_answer` 是照我笔记正文补的，只填进本来空着的那一份，**已有的绝不覆盖**。
    回灌失败不该影响诊断本身——诊断已经拿到了，页面上那份照样能看。
    """
    rows = []
    for it in out.items:
        if not 1 <= it.n <= len(answers):
            continue
        a = answers[it.n - 1]
        # 已经有标准答案的题不回填：那一份是出题时照正文抄的，轮不到这里改
        fresh = it.ref_answer if not (a.question.ref_answer or "").strip() else ""
        if not (it.full_answer or fresh):
            continue
        rows.append({"stem": a.question.stem, "full_answer": it.full_answer,
                     "beyond_vault": it.beyond_vault, "ref_answer": fresh})
    if not rows:
        return
    try:
        core.enrich_questions(vault, rows)
    except OSError as exc:
        log.warning("完整答案没回灌进题库：%s", exc)


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
            beyond=bool(it.get("beyond")),
            next_gap=str(it.get("next_gap") or ""),
            full_answer=str(it.get("full_answer") or "").strip(),
            beyond_vault=[str(x) for x in (it.get("beyond_vault") or []) if str(x).strip()],
            ref_answer=str(it.get("ref_answer") or "").strip(),
        ))
    if len(items) < total:
        warnings.append(f"模型只诊断了 {len(items)} / {total} 题，缺的那几题按你的自评算")
    items.sort(key=lambda x: x.n)
    return QuizDiagnosis(items=items, warnings=warnings)
