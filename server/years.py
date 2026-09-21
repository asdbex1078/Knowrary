"""year 批量回填：一次调用，把缺 year 的节点补齐（提议，不写盘）。

**为什么单独开一条路，而不是对每个节点跑一遍 `/api/suggest`**：实盘上 84 个节点里
44 个没有 year，一个一个问就是 44 次调用（按今天的账本约 $9）。而判断"这个概念是哪年
出现的"用不着候选节点列表、用不着关系类型表——把一批名字和摘要一起给模型，它一次就能
全答完，一次调用几毛钱。**一次一个点的那条路（suggest）仍然留着**：在检查器里顺手补
一个 year 用它，成批清欠账用这里。

写回走的仍然是 `/api/changes` 这唯一入口（`update_frontmatter`），
所以 diff 预览、备份、写前指纹校验一样不少——这里只负责"提议填什么"。
"""
from __future__ import annotations

import logging
from pathlib import Path

from .contracts import YearProposal, YearSuggestion
from .index_service import current_index
from .llm_call import ask, clean_year, parse_json

log = logging.getLogger(__name__)

MAX_NODES = 60        # 一次最多问这么多：再多就该分两轮，别把上下文撑爆
KNOWN_SAMPLE = 25     # 给模型看几个已经填好的当口径参照
MIN_CONFIDENCE = 0.6  # 低于这个默认不勾选（仍然列出来，让人自己判断）

_PROMPT: str | None = None


def _prompt() -> str:
    global _PROMPT
    if _PROMPT is None:
        path = Path(__file__).resolve().parents[1] / "tools" / "knowrary" / "prompts" / "years.md"
        _PROMPT = path.read_text(encoding="utf-8")
    return _PROMPT


def missing(index: dict) -> list[dict]:
    """缺 year 的已建节点，按 rank 排（重要的先补，一次问不完时不至于净问边角料）。"""
    # 和 core.no_year 同一条口径：聚合文档没有"诞生年份"，让模型去猜只会猜出一个假的
    rows = [n for n in index["nodes"]
            if not n.get("virtual") and not n.get("stub") and n.get("path")
            and not n.get("year") and not n.get("aggregate") and not n.get("timeless")]
    rows.sort(key=lambda n: (-(n.get("rank") or 0), n["id"]))
    return rows


def _known(index: dict) -> list[dict]:
    rows = [n for n in index["nodes"] if n.get("year") and not n.get("virtual")]
    rows.sort(key=lambda n: n["year"])
    return rows[:KNOWN_SAMPLE]


def _render(nodes: list[dict]) -> str:
    return "\n".join(
        f"- {n['id']} | name: {n.get('name') or n['id']} | field: {n.get('field') or ''} "
        f"| desc: {(n.get('desc') or '')[:120]}" for n in nodes) or "（没有）"


def propose(vault: Path, node_ids: list[str] | None = None) -> YearProposal:
    """给缺 year 的节点提议年份。只读，不写任何文件。"""
    index = current_index(vault)
    pool = missing(index)
    if node_ids:
        want = set(node_ids)
        pool = [n for n in pool if n["id"] in want]
    asked = pool[:MAX_NODES]
    if not asked:
        return YearProposal(asked=0, remaining=0, suggestions=[])

    known = _known(index)
    prompt = (_prompt()
              .replace("{{nodes}}", _render(asked))
              .replace("{{known}}", "\n".join(
                  f"- {n['id']}：{n['year']}" for n in known) or "（图里还没有填过 year 的节点）"))
    raw = ask(vault, "review", prompt, op="years")
    data = parse_json(raw, "year 批量回填", vault=vault)

    by_id = {n["id"]: n for n in asked}
    out: list[YearSuggestion] = []
    seen: set[str] = set()
    for row in data.get("years") or []:
        if not isinstance(row, dict):
            continue
        nid = str(row.get("id") or "")
        # 只认**我们问过的那些**：模型偶尔会顺手给一个没问过的节点（甚至编一个不存在的 id），
        # 放它过去就等于凭一句话往 md 里写字段。
        if nid not in by_id or nid in seen:
            continue
        year = clean_year(row.get("year"))
        if year is None:
            continue
        seen.add(nid)
        try:
            conf = float(row.get("confidence", 0.8))
        except (TypeError, ValueError):
            conf = 0.8
        out.append(YearSuggestion(
            id=nid, name=by_id[nid].get("name") or nid, year=year,
            confidence=max(0.0, min(1.0, conf)), why=str(row.get("why") or "")[:200],
            picked=conf >= MIN_CONFIDENCE))
    out.sort(key=lambda s: (-s.confidence, s.id))
    return YearProposal(asked=len(asked), remaining=max(0, len(pool) - len(asked)),
                        skipped=[n["id"] for n in asked if n["id"] not in seen],
                        suggestions=out)
