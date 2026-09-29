"""校验图里已经在的边（2026-09-29，开发实施计划 §10 #2 的后一半）。

review 角色逐条判：方向反了 / 类型不对（给建议类型）/ 不成立 / 没问题 / 拿不准。
**一批一次调用、前端一批一批地发**：审一个领域可能要好几分钟，做成一个长请求的话，
中间几分钟屏幕上什么都没有（「模型调用要看得见」那条纪律）。所以先 `plan` 出要审哪些、分几批，
再逐批 `run`，每批回来就能看到结论。

结论存 `.knowrary/edge-checks.json`（core.edge_checks），边没改过就不重审；修正走写回层，写前备份。
"""
from __future__ import annotations

from pathlib import Path

from .contracts import (EdgeCheck, EdgeCheckPlan, EdgeCheckPlanned, EdgeCheckRan, EdgeChecksRead, EdgeFix,
                        EdgeFixed)
from .index_service import current_index, invalidate
from .llm_call import ask_meta, parse_json
from .paths import core

BATCH = 20                 # 一次调用最多审这么多条：再多模型容易漏条、也更容易一条错全批返工
EXCERPT = 200              # 每个端点给多少字正文：够判断是什么东西，又不至于一批就上万字


class EdgeCheckRejected(ValueError):
    """这次请求不成立：范围里没有边、边不在了、修正不合法。"""


def _prompt() -> str:
    path = Path(__file__).resolve().parents[1] / "tools" / "knowrary" / "prompts" / "edge-check.md"
    return path.read_text(encoding="utf-8")


def plan(vault: Path, req: EdgeCheckPlan) -> EdgeCheckPlanned:
    """要审哪些边、分几批。审过而且没改过的（含你点过「忽略」的）跳过，除非 `force`。"""
    index = current_index(vault)
    nodes = {n["id"]: n for n in index["nodes"]}
    edges = [e for e in index["edges"] if _in_scope(e, nodes, req)]
    if not edges:
        raise EdgeCheckRejected("这个范围里一条边都没有")
    fresh = {} if req.force else core.fresh_edge_checks(vault, index)
    todo = [e["id"] for e in edges if e["id"] not in fresh]
    return EdgeCheckPlanned(total=len(todo), skipped=len(edges) - len(todo),
                            batches=[todo[i:i + BATCH] for i in range(0, len(todo), BATCH)])


def _in_scope(edge: dict, nodes: dict, req: EdgeCheckPlan) -> bool:
    if req.node:
        return req.node in (edge["source"], edge["target"])
    if req.field:
        return any((nodes.get(x) or {}).get("field") == req.field for x in (edge["source"], edge["target"]))
    return True


def run(vault: Path, ids: list[str]) -> EdgeCheckRan:
    """审一批（≤ BATCH 条）。模型漏掉的那几条不记、原样报出来；结论不合格的降成「拿不准」。"""
    index = current_index(vault)
    edges = {e["id"]: e for e in index["edges"]}
    batch = [edges[i] for i in dict.fromkeys(ids) if i in edges][:BATCH]
    if not batch:
        raise EdgeCheckRejected("这批边都不在图里了（可能刚被改过），重新发起一次")
    nodes = {n["id"]: n for n in index["nodes"]}
    rt = core.load_relation_types(vault)
    prompt = (_prompt().replace("{{relation_types}}", rt.describe())
              .replace("{{edges}}", "\n\n".join(_describe(vault, e, nodes) for e in batch)))
    raw, meta = ask_meta(vault, "review", prompt, op="edge-check")
    data = parse_json(raw, "edge-check", vault=vault)
    # 提示词里 id 写在 [ ] 里，模型偶尔连括号一起抄回来
    got = {str(r.get("id") or "").strip().strip("[]").strip(): r
           for r in (data.get("checks") or []) if isinstance(r, dict)} if data else {}
    results = [{**_clean(got[e["id"]], rt), "id": e["id"]} for e in batch if e["id"] in got]
    kept = core.record_edge_checks(vault, index, results, meta)
    return EdgeCheckRan(results=[_view(r, edges, nodes) for r in kept],
                        missing=[e["id"] for e in batch if e["id"] not in got], **meta)


def _describe(vault: Path, e: dict, nodes: dict) -> str:
    line = f"[{e['id']}]\n「{_label(nodes, e['source'])}」 —{e['type']}→ 「{_label(nodes, e['target'])}」"
    if e.get("year"):
        line += f"（{e['year']}）"
    if e.get("note"):
        line += f"\n  备注：{e['note']}"
    for end in (e["source"], e["target"]):
        line += f"\n  「{_label(nodes, end)}」：{_gist(vault, nodes.get(end) or {})}"
    return line


def _label(nodes: dict, nid: str) -> str:
    return (nodes.get(nid) or {}).get("name") or nid


def _gist(vault: Path, node: dict) -> str:
    """一个端点是什么：一句话摘要 + 正文开头一段（不含 frontmatter 和关系区）。"""
    desc = node.get("desc") or ""
    if not node.get("path"):
        return desc or "（图里只有个名字，还没写）"
    try:
        _, body = core.split_frontmatter(core.read(vault / node["path"]))
    except (OSError, ValueError):
        return desc
    body = body.split("## 关系")[0]
    text = " ".join(ln.strip() for ln in body.splitlines() if ln.strip() and not ln.startswith("#"))
    return f"{desc}｜{text[:EXCERPT]}" if text else desc


def _clean(r: dict, rt) -> dict:
    """结论不合格的降成「拿不准」：动作不认识、建议的类型没登记、报了问题却说不出依据。"""
    verdict = str(r.get("verdict") or "").strip()
    why = str(r.get("why") or "").strip()
    suggest = str(r.get("suggest") or "").strip()
    if verdict not in core.EDGE_VERDICTS:
        verdict = "unsure"
    if verdict == "retype" and not rt.known(suggest):
        why = f"{why}（建议的类型「{suggest}」不在类型表里）".strip()
        verdict, suggest = "unsure", ""
    if verdict in ("reverse", "retype", "drop") and not why:
        verdict = "unsure"
    return {"verdict": verdict, "suggest": suggest if verdict == "retype" else "", "why": why}


def _view(row: dict, edges: dict, nodes: dict) -> EdgeCheck:
    e = edges.get(row["id"]) or {}
    return EdgeCheck(id=row["id"], source=e.get("source", ""), target=e.get("target", ""), type=e.get("type", ""),
                     family=e.get("family", ""), source_name=_label(nodes, e.get("source", "")),
                     target_name=_label(nodes, e.get("target", "")), symmetric=bool(e.get("symmetric")),
                     **{k: row.get(k) for k in ("verdict", "suggest", "why", "model", "ms", "at", "ignored")
                        if row.get(k) is not None})


def read(vault: Path) -> EdgeChecksRead:
    """还算数的结论（边没改过的），前端按节点 / 按问题筛。"""
    index = current_index(vault)
    edges = {e["id"]: e for e in index["edges"]}
    nodes = {n["id"]: n for n in index["nodes"]}
    rows = core.fresh_edge_checks(vault, index)
    return EdgeChecksRead(checks=[_view({"id": k, **v}, edges, nodes) for k, v in rows.items()],
                          fields=sorted({n.get("field") for n in index["nodes"] if n.get("field")}))


def fix(vault: Path, req: EdgeFix) -> EdgeFixed:
    """按结论改：reverse / retype（给 relation）/ drop 走写回层；ignore 只打标记，边没改过就不再报。"""
    index = current_index(vault)
    edge = next((e for e in index["edges"] if e["id"] == req.id), None)
    if edge is None:
        raise EdgeCheckRejected("这条边已经不在了（可能刚被改过）")
    if req.action == "ignore":
        core.mark_edge_check(vault, req.id, ignored=True)
        return EdgeFixed(action="ignore")
    if req.action == "reverse" and edge.get("symmetric"):
        raise EdgeCheckRejected(f"「{edge['type']}」是无方向的，没有反不反一说")
    if req.action == "retype" and not core.load_relation_types(vault).known(req.relation or ""):
        raise EdgeCheckRejected(f"类型「{req.relation}」不在类型表里")
    nodes = {n["id"]: n for n in index["nodes"]}
    try:
        changes = core.edge_fix_changes(edge, nodes, req.action, req.relation or "")
        edits = core.plan(vault, changes, index)
    except (ValueError, core.ChangeRejected, core.WriteConflict) as exc:
        raise EdgeCheckRejected(str(exc)) from exc
    backup = core.commit(vault, edits) or None
    invalidate(vault)
    return EdgeFixed(action=req.action, backup=backup, files=[e.rel for e in edits])
