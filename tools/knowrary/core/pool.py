"""题库：聊天时教练随口问的那些检验问题，攒起来当考题用。

**为什么值得单独存一份**：这些题是在我刚学完那一刻、对着我当时的理解提出来的，
比事后让模型看着 md 现编的题贴身得多；而且它已经生成过一次了，
再出一遍等于为同一件事付两次钱。

**这里不存复习调度。** 什么时候该抽查，由**节点**的 review-log 说了算（第四份契约）——
题目再自带一套间隔，就成了第二份调度真值，两边迟早对不上。
这份文件只回答三个问题：这个知识点有哪些现成的题、哪些我已经答对过、这题最好的答案长什么样。

**`ref_answer` 和 `full_answer` 是两份数据，不许合并。** `ref_answer` 来自 vault 正文，是我自己写下的，
可信；`full_answer` 是批改时模型补出来的完整版，用来撑视野，可能有幻觉。合成一份的话，
模型补错的内容会被焊进题库，以后每次出题都按错的判——而我再也分不出那句是谁写的。
`beyond_vault` 记的正是 `full_answer` 里超出我笔记的那些点，既是免责标记，也是补卡候选。
"""
from __future__ import annotations

import datetime as dt
import re
from difflib import SequenceMatcher
from pathlib import Path

from .mdio import load_json, write_json_atomic

SCHEMA_VERSION = 1
MAX_STEM = 500
MAX_FULL = 1200        # 完整答案比标准答案长，单独给一档上限
KEEP = 2000            # 题攒到这个量级就该挑而不是全留；超了从最老、答对过的开始丢


def pool_path(vault: Path) -> Path:
    return vault / ".knowrary" / "question-pool.json"


def empty_pool() -> dict:
    return {"schema_version": SCHEMA_VERSION, "updated_at": None, "questions": []}


def load_pool(vault: Path) -> dict:
    path = pool_path(vault)
    if not path.exists():
        return empty_pool()
    try:
        data = load_json(path)
    except (ValueError, OSError):
        return empty_pool()
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        return empty_pool()
    for q in data["questions"]:                  # 改名前的题库里标准答案还叫 `answer`
        if isinstance(q, dict):
            if "answer" in q:
                q.setdefault("ref_answer", q.pop("answer"))
            if "answer_src" in q:
                q.setdefault("ref_src", q.pop("answer_src"))
    data["schema_version"] = SCHEMA_VERSION
    return data


def save_pool(vault: Path, pool: dict) -> None:
    pool["schema_version"] = SCHEMA_VERSION
    pool["updated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) \
        .isoformat().replace("+00:00", "Z")
    write_json_atomic(pool_path(vault), pool)


def norm(stem: str) -> str:
    """判重用的规整形式：空白和标点不同不算两道题。"""
    return re.sub(r"[\s，。、？?！!,.：:；;「」“”\"'()（）]+", "", stem).lower()


# 判"换个说法问的同一道题"的相似度阈值。**这个数是量出来的，不是拍的**：
# 真实题库里那 5 道 XOR 同义题两两相似度 0.68～0.97，而真正不同的题彼此最高只有 0.19
# （2026-09-19 复盘 §11.3）。0.6 落在中间那段空白里，两边都有很宽的余量。
SAME_Q = 0.6


def _same_question(q: dict, stem: str, points: list[str]) -> bool:
    """是不是"换个说法问的同一道"。

    **不能只看考点集合。** 考点是 `_mentioned` 按当轮提到的节点算的，图在长、集合就在变——
    那 5 道 XOR 同义题的考点集合**每次都不一样**（`['达特茅斯会议']`、`['连接主义']`、
    `['感知器','连接主义']`…），按集合相等去并一条都并不掉。真正稳定的信号是题面本身。
    """
    if set(q.get("points") or []) == set(points):
        return True
    return SequenceMatcher(None, norm(q.get("stem", "")), norm(stem)).ratio() >= SAME_Q


def add(vault: Path, stem: str, points: list[str], ref_answer: str = "",
        source: str = "chat") -> dict | None:
    """收一道题。同一道只留一份，考点取并集。返回落盘的那条。

    **「同一道」有两把尺子，缺了第二把就会攒出一堆同义题。** 第一把是题面规整后完全相同；
    第二把是**考点集合相同、而且那条还没答过**——教练问了一道题、人没答，下一轮它会换个说法
    再问一次，规整后当然不一样，于是每问一次就入库一条。真实题库里 10 道题有 5 道是
    同一道 XOR 题的不同措辞（2026-09-19 复盘 §11.3），而全部 `asked` 都是 0。

    换句话说：**教练随口问的那些题里，没答过、而且问的是同一件事的只留最新一条**（措辞以最新为准，
    它每次都在照着当时的理解重新问，后问的通常更贴）。答过的那些不动——它们已经是历史了。

    **只对 `source="chat"` 生效。** 出题那一路（`source="quiz"`）一轮本来就会围绕同几个节点
    出好几道**不同的**题，那是设计如此，不是重复；两条路共用一把尺子会把一整轮测验并成一道。
    """
    stem = (stem or "").strip()[:MAX_STEM]
    points = [p for p in dict.fromkeys(points or []) if p]
    if not stem or not points:
        return None                      # 没考点的题进不了池子：抽查是按节点找题的
    pool = load_pool(vault)
    key = norm(stem)
    for q in pool["questions"]:
        if norm(q.get("stem", "")) == key:
            q["points"] = list(dict.fromkeys([*q.get("points", []), *points]))
            q["ref_answer"] = q.get("ref_answer") or ref_answer[:MAX_STEM]
            save_pool(vault, pool)
            return q
    if source == "chat":
        for q in pool["questions"]:
            if q.get("source") == "chat" and not q.get("asked") and _same_question(q, stem, points):
                q["stem"] = stem                              # 换个说法问的同一道：以最新措辞为准
                q["points"] = list(dict.fromkeys([*q.get("points", []), *points]))
                q["ref_answer"] = ref_answer[:MAX_STEM] or q.get("ref_answer") or ""
                save_pool(vault, pool)
                return q
    row = {"id": f"q{len(pool['questions']) + 1}-{key[:12] or 'x'}",
           "stem": stem, "ref_answer": (ref_answer or "")[:MAX_STEM], "points": points,
           "full_answer": "", "beyond_vault": [],
           "source": source, "created": dt.date.today().isoformat(),
           "asked": 0, "right": 0, "wrong": 0, "last": "", "learned": False}
    pool["questions"].append(row)
    if len(pool["questions"]) > KEEP:
        # 先丢答对过的老题：没答对过的那些正是最该再问一次的
        pool["questions"].sort(key=lambda q: (q.get("learned") is True, q.get("created") or ""))
        pool["questions"] = pool["questions"][-KEEP:]
    save_pool(vault, pool)
    return row


def for_nodes(pool: dict, node_ids: list[str] | set[str], limit: int = 5) -> list[dict]:
    """给这些节点挑几道现成的题。

    排序：没问过的 > 答错过的 > 答对过的（答对过的排最后，但不是永不再问——
    间隔复习的意义正是"以为记住了的也要抽查"，只是先紧着没把握的来）。
    """
    want = set(node_ids)
    hit = [q for q in pool.get("questions") or [] if want & set(q.get("points") or [])]
    hit.sort(key=lambda q: (q.get("last") or ""))              # 同档里最久没问的排前面
    hit.sort(key=lambda q: (bool(q.get("learned")), q.get("asked", 0)))
    return hit[:limit]


def mark(vault: Path, stems: list[tuple[str, bool]]) -> int:
    """按题面回填作答结果：`[(题干, 答对了吗)]`。返回对上号的题数。

    用题面而不是 id 对号：题可能是模型现编的（不在池子里），也可能是池子里拿的，
    交卷那一头不该为了这件事多带一个字段。
    """
    pool = load_pool(vault)
    index = {norm(q.get("stem", "")): q for q in pool.get("questions") or []}
    today = dt.date.today().isoformat()
    n = 0
    for stem, ok in stems:
        q = index.get(norm(stem or ""))
        if not q:
            continue
        q["asked"] = q.get("asked", 0) + 1
        q["last"] = today
        if ok:
            q["right"] = q.get("right", 0) + 1
            q["learned"] = True          # 「学会」只是个排序提示，不是"以后不考了"
        else:
            q["wrong"] = q.get("wrong", 0) + 1
            q["learned"] = False
        n += 1
    if n:
        save_pool(vault, pool)
    return n


def pool_stats(pool: dict) -> dict:
    qs = pool.get("questions") or []
    return {"total": len(qs),
            "learned": sum(1 for q in qs if q.get("learned")),
            "fresh": sum(1 for q in qs if not q.get("asked"))}


def enrich(vault: Path, rows: list[dict]) -> int:
    """按题面回填批改的产出。每条 row：`{stem, full_answer, beyond_vault, ref_answer}`，后三个都可省。

    **两份答案两套规矩，这正是这个函数存在的理由：**

    - `full_answer`（模型写的）**每轮覆盖**。同一道题答第三遍时模型看到的上下文更全，
      留新的那版比留第一版好。
    - `ref_answer`（我笔记正文里的标准答案）**只填空，绝不覆盖**。它是这道题的判分依据，
      一旦被模型的话顶掉，以后每一轮都按模型的标准判我——而我再也分不出那句是谁写的。
      聊天攒的题一开始没有 ref_answer（`pull_checks` 只摘题干和考点，没读过正文），
      批改时照着正文补出来的那一份就填在这里，来源仍然是我自己的笔记。
    """
    if not rows:
        return 0
    pool = load_pool(vault)
    index = {norm(q.get("stem", "")): q for q in pool.get("questions") or []}
    n = 0
    for row in rows:
        q = index.get(norm(row.get("stem") or ""))
        if not q:
            continue
        touched = False
        full = (row.get("full_answer") or "").strip()
        if full:
            q["full_answer"] = full[:MAX_FULL]
            q["beyond_vault"] = [str(x)[:120] for x in (row.get("beyond_vault") or [])
                                 if str(x).strip()][:8]
            q["full_src"] = "llm"      # 这一份是模型写的，回看时要能一眼分得出来
            touched = True
        ref = (row.get("ref_answer") or "").strip()
        if ref and not (q.get("ref_answer") or "").strip():
            q["ref_answer"] = ref[:MAX_STEM]
            q["ref_src"] = "vault"     # 照笔记正文补的，和模型自由发挥的那份不是一回事
            touched = True
        n += 1 if touched else 0
    if n:
        save_pool(vault, pool)
    return n
