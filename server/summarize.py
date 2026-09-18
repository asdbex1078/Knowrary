"""概括节点起草：把画布上圈在一起的几个点，交给 learn 角色写一份上位节点的初稿（提议，不写盘）。

2026-09-18 用户选的是"模型先写、我再调"：草稿预填进新建对话框并挂「AI 建议」标记，
写入仍走 `/api/changes`（create_node + 每个子节点一条「包含」边），这里只负责"提议写什么"。
子节点的正文只给节选：概括要的是脉络，不是复述；也免得几十个点把上下文撑爆。
"""
from __future__ import annotations

from pathlib import Path

from .contracts import SummarizeRequest, SummaryDraft
from .index_service import current_index
from .llm_call import ask, clean_layer, clean_year, parse_json
from .paths import core

MAX_CHILDREN = 40      # 再多就不是"一块知识"了，先拆框
EXCERPT_CHARS = 500    # 每个子节点正文给多少

_PROMPT: str | None = None


def _prompt() -> str:
    global _PROMPT
    if _PROMPT is None:
        path = Path(__file__).resolve().parents[1] / "tools" / "knowrary" / "prompts" / "summarize.md"
        _PROMPT = path.read_text(encoding="utf-8")
    return _PROMPT


class SummarizeRejected(Exception):
    pass


def propose(vault: Path, req: SummarizeRequest) -> SummaryDraft:
    index = current_index(vault)
    by_id = {n["id"]: n for n in index["nodes"] if not n.get("virtual")}
    ids = list(dict.fromkeys(i for i in req.node_ids if i in by_id))
    if len(ids) < 2:
        raise SummarizeRejected("至少要两个已建出来的点才谈得上概括")
    if len(ids) > MAX_CHILDREN:
        raise SummarizeRejected(f"一次最多概括 {MAX_CHILDREN} 个点（给了 {len(ids)}）：先把框拆小")
    prompt = (_prompt().replace("{{name_hint}}", (req.name or "").strip() or "（没给，你起）")
              .replace("{{children}}", _children(vault, index, [by_id[i] for i in ids])))
    raw = ask(vault, "learn", prompt, op="summarize")
    data = parse_json(raw, f"summarize {','.join(ids[:5])}", vault=vault)
    body = str(data.get("body") or "").strip()
    if core.RE_REL_HEADER.search(body):
        body = body[:core.RE_REL_HEADER.search(body).start()].rstrip()
    return SummaryDraft(name=str(data.get("name") or req.name or "").strip(), desc=str(data.get("desc") or "").strip(),
                        body=body, layer=clean_layer(data.get("layer")) or None, year=clean_year(data.get("year")),
                        children=ids)


def _children(vault: Path, index: dict, nodes: list[dict]) -> str:
    edges = {e["id"]: e for e in index["edges"]}
    rows = []
    for n in nodes:
        try:
            text = core.read(vault / n["path"]) if n.get("path") else ""
        except OSError:
            text = ""
        body = core.split_sections(text)[1].strip() if text else ""
        rel = [f"{edges[i]['type']}→{edges[i]['target']}" for i in n.get("out", []) if i in edges]
        rows.append(f"- {n['id']}｜{n.get('desc') or ''}｜{_squash(body)[:EXCERPT_CHARS]}｜{'; '.join(rel) or '(无)'}")
    return "\n".join(rows)


def _squash(text: str) -> str:
    return " ".join(line.strip() for line in text.splitlines() if line.strip() and not line.startswith("# "))
