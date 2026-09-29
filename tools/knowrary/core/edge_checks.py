"""已有边的校验结论（`.knowrary/edge-checks.json`，2026-09-29）。

写入审核只管「这次要写的」边；图里已经在的 253 条从来没人查过对不对（开发实施计划 §10 #2）。
review 角色审完的结论存在这里，**带着这条边当时的样子**（指纹：类型 / 方向 / 年份 / 备注）——
边没改过就不重审，不重复花钱；改过了（换了类型、改了备注）旧结论自动不算数。

和 md 的关系：结论不进 md（推测出来的东西不进真值源）；**修正**走写回层（`fix_changes` 翻成
ChangeSet），写前备份。

索引里的边是归一过方向的（`A 属于 B` 记成 `B 包含 A`），而 md 里那一行写在哪个文件、原本是什么类型
都在 `declared_in` / `raw_types` 里——修正时要换算回 md 上的那一行，不然删不掉、改不动。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import threading
from pathlib import Path

from .mdio import load_json, write_json_atomic

SCHEMA_VERSION = 1
VERDICTS = ("ok", "reverse", "retype", "drop", "unsure")
LOCK = threading.RLock()


def checks_path(vault: Path) -> Path:
    return vault / ".knowrary" / "edge-checks.json"


def fingerprint(edge: dict) -> str:
    """这条边现在的样子。类型、方向、年份、备注任何一样变了，旧结论就不算数。"""
    raw = "|".join(str(edge.get(k) or "") for k in ("source", "type", "target", "year", "note"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def load_checks(vault: Path) -> dict:
    path = checks_path(vault)
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "checks": {}}
    try:
        doc = load_json(path)
    except (ValueError, OSError):
        return {"schema_version": SCHEMA_VERSION, "checks": {}}
    if not isinstance(doc, dict) or not isinstance(doc.get("checks"), dict):
        return {"schema_version": SCHEMA_VERSION, "checks": {}}
    return doc


def fresh_checks(vault: Path, index: dict) -> dict[str, dict]:
    """还算数的结论：{边 id: 结论}。边已经不在、或者改过了的不算。"""
    edges = {e["id"]: e for e in index.get("edges") or []}
    out = {}
    for eid, row in load_checks(vault)["checks"].items():
        edge = edges.get(eid)
        if edge is not None and row.get("fp") == fingerprint(edge):
            out[eid] = row
    return out


def record_checks(vault: Path, index: dict, results: list[dict], meta: dict) -> list[dict]:
    """记下一批结论（覆盖同一条边的旧结论）。`meta` 是那次调用的回执（model / ms）。返回记下的那几条。"""
    edges = {e["id"]: e for e in index.get("edges") or []}
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    kept = []
    with LOCK:
        doc = load_checks(vault)
        for r in results:
            edge = edges.get(r.get("id"))
            if edge is None:
                continue
            row = {"fp": fingerprint(edge), "verdict": r["verdict"], "suggest": r.get("suggest") or "",
                   "why": r.get("why") or "", "at": now, **meta}
            doc["checks"][edge["id"]] = row
            kept.append({"id": edge["id"], **row})
        write_json_atomic(checks_path(vault), doc)
    return kept


def mark_check(vault: Path, edge_id: str, **fields) -> None:
    """给一条结论加标记（`ignored=True`：你觉得它看走眼了；`fixed=True`：按它改过了）。"""
    with LOCK:
        doc = load_checks(vault)
        if edge_id in doc["checks"]:
            doc["checks"][edge_id].update(fields)
            write_json_atomic(checks_path(vault), doc)


def raw_form(edge: dict, nodes: dict) -> tuple[str, str, str]:
    """这条边在 md 里那一行：(写在谁的文件里, 原本的类型, 指向谁)。"""
    declared = (edge.get("declared_in") or [""])[0]
    owner = next((nid for nid, n in nodes.items() if n.get("path") == declared), edge["source"])
    raw_type = (edge.get("raw_types") or [edge["type"]])[0]
    other = edge["target"] if owner == edge["source"] else edge["source"]
    return owner, raw_type, other


def fix_changes(edge: dict, nodes: dict, action: str, relation: str = "") -> list[dict]:
    """按结论改这条边，翻成 ChangeSet。
    - drop：删掉 md 里那一行；
    - reverse：删掉那一行，在目标那头写一行反方向的（同类型）；
    - retype：删掉那一行，在源那头写一行新类型的。年份和备注跟着走。"""
    owner, raw_type, other = raw_form(edge, nodes)
    out = [{"type": "remove_edge", "source": owner, "relation": raw_type, "target": other}]
    if action == "drop":
        return out
    src, tgt, rel = edge["source"], edge["target"], edge["type"]
    if action == "reverse":
        src, tgt = tgt, src
    elif action == "retype":
        rel = relation
    else:
        raise ValueError(f"不认识的修正：{action}")
    add = {"type": "add_edge", "source": src, "relation": rel, "target": tgt}
    for k in ("year", "note"):
        if edge.get(k):
            add[k] = edge[k]
    return out + [add]
