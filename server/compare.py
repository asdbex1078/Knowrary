"""对比组的两个只读接口：一份目录、一张表。

都是现算的——**没有第二份数据**。目录从 index 里按 `type: 对比组` 过滤出来，表由
`core.compare_table` 现读成员 md。改名、删除自动跟着走，不用维护任何同步。

写回不在这里：改格子走 `/api/changes`（那条路才有 diff 预览、备份和指纹校验）。
"""
from __future__ import annotations

from pathlib import Path

from .contracts import CompareCellSuggestion, CompareProposal
from .index_service import current_index
from .llm_call import ask, parse_json
from .paths import core


def directory(vault: Path) -> dict:
    """目录：每个对比组一行，带成员数、列数、空格子数。

    空格子数要真算一遍表才知道，所以这里会把每个组的表都跑一遍。对比组是手工建的，
    个位数量级，读几个 md 的开销远小于"为了省这一下再存一份计数"带来的对不上。
    """
    index = current_index(vault)
    items = []
    for g in core.compare_groups(index):
        t = core.compare_table(vault, index, g["id"])
        items.append({**g, "members": len(t["rows"]), "gaps": t["gaps"], "cells": t["cells"],
                      "unbuilt": sum(1 for r in t["rows"] if not r["built"])})
    return {"count": len(items), "items": items}


def table(vault: Path, group_id: str) -> dict | None:
    """一个对比组的完整表。不是对比组就返回 None，由路由转 404。"""
    return core.compare_table(vault, current_index(vault), group_id)


# ---------------------------------------------------------------- 补空格子（一次调用）

MAX_CELLS = 40        # 一次最多问这么多格：再多就该分两轮，别把上下文撑爆
MAX_BODY = 700        # 每个成员给多少字摘要：够模型知道这个技术在讲什么就行
MIN_CONFIDENCE = 0.6  # 低于这个默认不勾选（仍然列出来，让人自己判断）


_PROMPT: str | None = None
CELL_SEP = "\u0000"       # `节点 id + 维度名` 拼成一个格子的 key；用 NUL 是因为两者都可能带中文标点


def _prompt() -> str:
    global _PROMPT
    if _PROMPT is None:
        path = Path(__file__).resolve().parents[1] / "tools" / "knowrary" / "prompts" / "compare.md"
        _PROMPT = path.read_text(encoding="utf-8")
    return _PROMPT


def _gap_keys(t: dict) -> list[str]:
    """这张表所有空格子，按行序 × 列序。还没建出来的成员不问——它连正文都没有。"""
    return [f"{r['id']}{CELL_SEP}{dim}" for r in t["rows"] if r["built"] and not r["stub"]
            for dim in t["columns"] if r["cells"][dim]["value"] is None]


def _filled_sample(t: dict) -> str:
    """已经填好的格子：给模型当口径参照。**同一列的先摆在一起**，句式才对得齐。"""
    rows = [f"- {dim} | {r['name']}：{r['cells'][dim]['value']}"
            for dim in t["columns"] for r in t["rows"] if r["cells"][dim]["value"]]
    return "\n".join(rows[:40]) or "（这张表还一个格子都没填，你来定这一版的口径）"


def _members_digest(vault: Path, index: dict, t: dict) -> str:
    """每个成员的摘要：desc + 正文开头。**不整篇给**——一次问一张表，整篇会把上下文撑爆。"""
    meta = {n["id"]: n for n in index["nodes"]}
    out = []
    for row in t["rows"]:
        m = meta.get(row["id"]) or {}
        body = ""
        path = m.get("path")
        if path and (vault / path).exists():
            _, body, _, _ = core.split_sections(core.read(vault / path))
        out.append(f"### {row['name']}（id: {row['id']}）\n"
                   f"{m.get('desc') or ''}\n\n{body.strip()[:MAX_BODY]}")
    return "\n\n".join(out)


def propose(vault: Path, group_id: str, cells: list[str] | None = None) -> CompareProposal:
    """一次调用，把这张表的空格子问一遍。只读，不写任何文件。

    **为什么不一格一问**：5 个成员 × 4 列就是 20 次调用，而"这张表在比什么"这件事
    每次都要重讲一遍。一次给整张表，模型还能横着看——同一列已经填好的那几格就是口径，
    这正是逐格问拿不到的东西（README 里 year 批量回填那条是同一个道理）。

    写回仍然走 `/api/changes`（`set_fact`），所以 diff 预览、备份、写前指纹校验一样不少。
    """
    index = current_index(vault)
    t = core.compare_table(vault, index, group_id)
    if t is None:
        return None
    pool = _gap_keys(t)
    if cells:
        want = set(cells)
        pool = [c for c in pool if c in want]
    asked = pool[:MAX_CELLS]
    if not asked:
        return CompareProposal(group=group_id, asked=0, remaining=0)

    by_id = {r["id"]: r for r in t["rows"]}
    notes = "\n".join(f"- {d}" for d in t["columns"])
    prompt = (_prompt()
              .replace("{{group}}", t["name"])
              .replace("{{desc}}", t["desc"] or "（没写说明）")
              .replace("{{columns}}", "、".join(t["columns"]))
              .replace("{{dimension_notes}}", f"每一列的名字就是键，**原样使用**：\n{notes}")
              .replace("{{filled}}", _filled_sample(t))
              .replace("{{gaps}}", "\n".join(
                  f"- {by_id[c.split(CELL_SEP)[0]]['name']}（id: {c.split(CELL_SEP)[0]}） "
                  f"缺「{c.split(CELL_SEP)[1]}」" for c in asked))
              .replace("{{members}}", _members_digest(vault, index, t)))
    raw = ask(vault, "review", prompt, op="compare")
    data = parse_json(raw, "对比表补格", vault=vault)

    allowed = set(asked)
    seen: set[str] = set()
    out: list[CompareCellSuggestion] = []
    for row in data.get("cells") or []:
        if not isinstance(row, dict):
            continue
        nid, key = str(row.get("id") or ""), str(row.get("key") or "").strip()
        ckey = f"{nid}{CELL_SEP}{key}"
        # 只认**我们问过的那些格子**：模型偶尔会顺手填一个没问的格（甚至自造一个维度名），
        # 放它过去就等于凭一句话往 md 里写字段，而自造的维度名根本进不了这张表
        if ckey not in allowed or ckey in seen:
            continue
        value = " ".join(str(row.get("value") or "").split())
        if not value:
            continue
        seen.add(ckey)
        try:
            conf = float(row.get("confidence", 0.8))
        except (TypeError, ValueError):
            conf = 0.8
        conf = max(0.0, min(1.0, conf))
        out.append(CompareCellSuggestion(
            id=nid, name=by_id[nid]["name"], key=key, value=value[:400],
            confidence=conf, why=str(row.get("why") or "")[:200], picked=conf >= MIN_CONFIDENCE))
    out.sort(key=lambda s: (-s.confidence, s.id, s.key))
    return CompareProposal(
        group=group_id, asked=len(asked), remaining=max(0, len(pool) - len(asked)),
        skipped=[c.replace(CELL_SEP, "·") for c in asked if c not in seen], suggestions=out)
