"""AI 建议：关系、去重、分类。调用 LLM（review 角色），可能耗时数秒。"""
from __future__ import annotations

import logging
from pathlib import Path

from .contracts import SuggestDuplicate, SuggestEdge, SuggestResult
from .index_service import current_index
from .layout_store import load_or_init
from .llm_call import ask, parse_json
from .paths import core

log = logging.getLogger(__name__)

_PROMPT_TEMPLATE: str | None = None
_MAX_CANDIDATES = 50


def _load_prompt_template() -> str:
    global _PROMPT_TEMPLATE
    if _PROMPT_TEMPLATE is None:
        path = Path(__file__).resolve().parents[1] / "tools" / "knowrary" / "prompts" / "suggest.md"
        _PROMPT_TEMPLATE = path.read_text(encoding="utf-8")
    return _PROMPT_TEMPLATE


def suggest(vault: Path, node_id: str) -> SuggestResult:
    index = current_index(vault)
    layout, _ = load_or_init(vault, index)
    plain = layout.model_dump()

    meta = next((n for n in index["nodes"] if n["id"] == node_id), None)
    if meta is None:
        return SuggestResult(node_id=node_id)

    rt = core.load_relation_types(vault)
    existing_edges = _existing_edges(index, node_id)
    candidates = _select_candidates(index, node_id, meta, _MAX_CANDIDATES)
    groups = _format_groups(plain)

    prompt = _build_prompt(meta, candidates, rt, existing_edges, groups)
    raw = _call_llm(vault, prompt)
    parsed = _parse_llm_response(raw, node_id, vault)

    gid = core.target_group(node_id, index, plain)
    gname = plain["groups"].get(gid, {}).get("name") if gid else None

    return SuggestResult(
        node_id=node_id,
        edges=parsed.get("edges", []),
        duplicates=parsed.get("duplicates", []),
        suggested_field=parsed.get("suggested_field"),
        suggested_group=gid,
        suggested_group_name=gname,
        raw_llm=raw,
    )


def _existing_edges(index: dict, node_id: str) -> list[dict]:
    out = []
    for e in index["edges"]:
        if e["source"] == node_id:
            out.append({"type": e["type"], "target": e["target"], "direction": "out"})
        elif e["target"] == node_id:
            out.append({"type": e["type"], "target": e["source"], "direction": "in"})
    return out


def _select_candidates(index: dict, node_id: str, meta: dict, limit: int) -> list[dict]:
    """选出最可能相关的候选节点：同 field 优先 + 度数高优先。"""
    node_field = meta.get("field")
    connected = set()
    for e in index["edges"]:
        if e["source"] == node_id:
            connected.add(e["target"])
        elif e["target"] == node_id:
            connected.add(e["source"])

    candidates = []
    for n in index["nodes"]:
        if n["id"] == node_id or n.get("stub"):
            continue
        same_field = 1 if n.get("field") == node_field and node_field else 0
        already_linked = 1 if n["id"] in connected else 0
        candidates.append({
            "id": n["id"],
            "name": n.get("name") or n["id"],
            "field": n.get("field") or "",
            "desc": n.get("desc") or "",
            "degree": n.get("degree", 0),
            "same_field": same_field,
            "already_linked": already_linked,
        })

    candidates.sort(key=lambda c: (-c["same_field"], -c["degree"]))
    return candidates[:limit]


def _format_groups(plain: dict) -> str:
    groups = plain.get("groups", {})
    if not groups:
        return "（暂无分组）"
    lines = []
    for gid, g in sorted(groups.items()):
        parent = f"  (parent: {g.get('parent')})" if g.get("parent") else ""
        lines.append(f"- {gid}: {g.get('name', gid)}{parent}")
    return "\n".join(lines)


def _build_prompt(meta: dict, candidates: list[dict], rt, existing_edges: list[dict],
                  groups: str) -> str:
    tmpl = _load_prompt_template()

    existing_text = "（暂无已有关系）"
    if existing_edges:
        lines = []
        for e in existing_edges:
            arrow = "→" if e["direction"] == "out" else "←"
            lines.append(f"- {e['type']} {arrow} {e['target']}")
        existing_text = "\n".join(lines)

    cand_lines = []
    for c in candidates:
        linked_tag = " [已连接]" if c["already_linked"] else ""
        cand_lines.append(f"- {c['id']} | name: {c['name']} | field: {c['field']} | "
                          f"desc: {c['desc']} | degree: {c['degree']}{linked_tag}")
    candidates_text = "\n".join(cand_lines) if cand_lines else "（暂无候选节点）"

    return (tmpl
            .replace("{{node_id}}", meta.get("id", ""))
            .replace("{{node_name}}", meta.get("name") or meta.get("id", ""))
            .replace("{{node_field}}", meta.get("field") or "（未设置）")
            .replace("{{node_desc}}", meta.get("desc") or "（未设置）")
            .replace("{{existing_edges}}", existing_text)
            .replace("{{candidates}}", candidates_text)
            .replace("{{relation_types}}", rt.describe())
            .replace("{{groups}}", groups))


def _call_llm(vault: Path, prompt: str) -> str:
    return ask(vault, "review", prompt, op="suggest")


def _parse_llm_response(raw: str, node_id: str, vault: Path | None = None) -> dict:
    data = parse_json(raw, f"node_id={node_id}", vault=vault)
    result: dict = {"edges": [], "duplicates": [], "suggested_field": None}

    for e in data.get("edges", []):
        if not isinstance(e, dict):
            continue
        edge_type = e.get("type", "")
        target = e.get("target", "")
        if not edge_type or not target:
            continue
        result["edges"].append(SuggestEdge(
            type=edge_type,
            target=target,
            direction=e.get("direction", "out"),
            reason=e.get("reason", ""),
            confidence=float(e.get("confidence", 0.8)),
        ))

    for d in data.get("duplicates", []):
        if not isinstance(d, dict):
            continue
        existing_id = d.get("existing_id", "")
        if not existing_id:
            continue
        result["duplicates"].append(SuggestDuplicate(
            existing_id=existing_id,
            reason=d.get("reason", ""),
            confidence=float(d.get("confidence", 0.8)),
        ))

    result["suggested_field"] = data.get("suggested_field") or None

    return result
