"""待审边（`.knowrary/pending.json`）：LLM 推测出来、还没被人认下的关系。

为什么单独一份文件而不进 md：md 里的 `- 类型:: [[目标]]` 没地方挂"来源 / 置信度 / 待审核"这些
机器状态，硬塞进去要么污染关系解析器、要么让 Obsidian 里看着莫名其妙。推测出来的东西不进真值源
（和幽灵占位、复习记录同一条纪律），审核通过那一刻才走 `/api/changes` 的 add_edge 写回 md。

它和 md / index / layout / review 并列，是第五份契约；只由导入与审核两条链路写。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .mdio import load_json, write_json_atomic

SCHEMA_VERSION = 1
STATUS_PENDING = "pending"


def pending_path(vault: Path) -> Path:
    return vault / ".knowrary" / "pending.json"


def empty_pending() -> dict:
    return {"schema_version": SCHEMA_VERSION, "updated_at": None, "edges": [], "homes": []}


def load_pending(vault: Path) -> dict:
    path = pending_path(vault)
    if not path.exists():
        return empty_pending()
    doc = load_json(path) or {}
    if not isinstance(doc, dict) or not isinstance(doc.get("edges"), list):
        return empty_pending()
    doc.setdefault("schema_version", SCHEMA_VERSION)
    if not isinstance(doc.get("homes"), list):
        doc["homes"] = []
    return doc


def edge_key(source: str, relation: str, target: str) -> str:
    """和 index 里边的 id 同一个写法（`a->b#依赖`），审核通过后好对得上。"""
    return f"{source}->{target}#{relation}"


def add_pending(vault: Path, edges: list[dict], origin: dict) -> list[dict]:
    """追加一批待审边。同一条（源、类型、目标）已在待审里就不重复记，只保留先到的那条。

    `origin` 记这批边从哪来（文章名、导入日期），审核时才知道"模型当时是看着什么说的"。
    返回真正新增的那几条。
    """
    doc = load_pending(vault)
    have = {e.get("id") for e in doc["edges"]}
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    added: list[dict] = []
    for e in edges:
        key = edge_key(e["source"], e["relation"], e["target"])
        if key in have:
            continue
        have.add(key)
        entry = {"id": key, "source": e["source"], "relation": e["relation"], "target": e["target"],
                 "year": e.get("year"), "note": (e.get("note") or "").strip() or None,
                 "confidence": float(e.get("confidence", 0.0)), "status": STATUS_PENDING,
                 "origin": dict(origin), "created_at": now}
        doc["edges"].append(entry)
        added.append(entry)
    if added:
        doc["updated_at"] = now
        write_json_atomic(pending_path(vault), doc)
    return added


def remove_pending(vault: Path, ids: list[str]) -> int:
    """审核完（不论采纳还是驳回）就从待审里拿掉；采纳的那条由调用方另行走 add_edge 写回 md。"""
    doc = load_pending(vault)
    gone = set(ids)
    kept = [e for e in doc["edges"] if e.get("id") not in gone]
    removed = len(doc["edges"]) - len(kept)
    if removed:
        doc["edges"] = kept
        doc["updated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        write_json_atomic(pending_path(vault), doc)
    return removed


# ---------------------------------------------------------------- 归属建议（孤立节点该去哪）

def add_home(vault: Path, node_ids: list[str], home: dict, origin: dict) -> dict | None:
    """记一条"这几个孤立节点该归到哪"的建议：导入时模型顺手给的 `suggest_home`，不另调模型。

    只是建议，Inbox 上显示给人看；人把节点放上画布之后它就没用了（读的时候按"还在 Inbox 里"过滤）。
    同一批节点已经有建议就覆盖——后一篇文章的判断更新。
    """
    ids = sorted({str(i) for i in node_ids if i})
    name = str(home.get("name") or "").strip()
    if not ids or not name:
        return None
    doc = load_pending(vault)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    entry = {"node_ids": ids, "kind": str(home.get("kind") or "none"), "name": name,
             "why": str(home.get("why") or "").strip() or None, "origin": dict(origin), "created_at": now}
    doc["homes"] = [h for h in doc["homes"] if set(h.get("node_ids") or []) != set(ids)] + [entry]
    doc["updated_at"] = now
    write_json_atomic(pending_path(vault), doc)
    return entry


def load_homes(vault: Path) -> dict[str, dict]:
    """{节点 id: 建议}，同一个节点出现在多条里取最新的。"""
    out: dict[str, dict] = {}
    for h in load_pending(vault)["homes"]:
        for nid in h.get("node_ids") or []:
            out[nid] = h
    return out
