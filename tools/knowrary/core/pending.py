"""待审边（`.knowrary/pending.json`）：LLM 推测出来、还没被人认下的关系。

为什么单独一份文件而不进 md：md 里的 `- 类型:: [[目标]]` 没地方挂"来源 / 置信度 / 待审核"这些
机器状态，硬塞进去要么污染关系解析器、要么让 Obsidian 里看着莫名其妙。推测出来的东西不进真值源
（和幽灵占位、复习记录同一条纪律），审核通过那一刻才走 `/api/changes` 的 add_edge 写回 md。

它和 md / index / layout / review 并列，是第五份契约；只由导入与审核两条链路写。

**驳回的留一笔**（`rejected`，2026-09-29）：不留的话，下一篇文章又推出同一条（同源、同类型、同目标），
它会原样回到待审里让人再驳一次。留下的记录能撤销（回到待审）。
"""
from __future__ import annotations

import datetime as dt
import functools
import threading
from pathlib import Path

from .mdio import load_json, write_json_atomic

SCHEMA_VERSION = 1
# 所有「读整份 → 改一处 → 写回整份」都在这把锁里（2026-09-29）：服务端的请求在线程池里并发，
# 连着点「清掉」「驳回」时，两个请求都读到旧的那份、各改各的写回去，先写的那次就丢了。
# 可重入：服务端处理一批决定时整段持锁（先写 md 再改这里），里面再调这些函数不会自己把自己锁死。
LOCK = threading.RLock()


def _locked(fn):
    @functools.wraps(fn)
    def run(*args, **kwargs):
        with LOCK:
            return fn(*args, **kwargs)
    return run
STATUS_PENDING = "pending"
STATUS_REJECTED = "rejected"


def pending_path(vault: Path) -> Path:
    return vault / ".knowrary" / "pending.json"


def empty_pending() -> dict:
    return {"schema_version": SCHEMA_VERSION, "updated_at": None, "edges": [], "homes": [], "rejected": [],
            "types": [], "rejected_types": [], "curated": {}}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_pending(vault: Path) -> dict:
    path = pending_path(vault)
    if not path.exists():
        return empty_pending()
    doc = load_json(path) or {}
    if not isinstance(doc, dict) or not isinstance(doc.get("edges"), list):
        return empty_pending()
    doc.setdefault("schema_version", SCHEMA_VERSION)
    for key in ("homes", "rejected", "types", "rejected_types"):
        if not isinstance(doc.get(key), list):
            doc[key] = []
    if not isinstance(doc.get("curated"), dict):
        doc["curated"] = {}
    return doc


def edge_key(source: str, relation: str, target: str) -> str:
    """和 index 里边的 id 同一个写法（`a->b#依赖`），审核通过后好对得上。"""
    return f"{source}->{target}#{relation}"


@_locked
def add_pending(vault: Path, edges: list[dict], origin: dict) -> list[dict]:
    """追加一批待审边。同一条（源、类型、目标）已在待审里就不重复记，只保留先到的那条。

    `origin` 记这批边从哪来（文章名、导入日期），审核时才知道"模型当时是看着什么说的"。
    返回真正新增的那几条。
    """
    doc = load_pending(vault)
    # 已经驳回过的也算"有了"：同一条错边不该换一篇文章就回来让人再驳一次
    have = {e.get("id") for e in doc["edges"]} | {e.get("id") for e in doc["rejected"]}
    now = _now()
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


@_locked
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


@_locked
def reject_pending(vault: Path, ids: list[str]) -> int:
    """驳回：从待审挪进 `rejected`，留着原样（撤销时原样放回去）。返回挪了几条。"""
    doc = load_pending(vault)
    gone = set(ids)
    moved = [e for e in doc["edges"] if e.get("id") in gone]
    if not moved:
        return 0
    now = _now()
    doc["edges"] = [e for e in doc["edges"] if e.get("id") not in gone]
    doc["rejected"] += [{**e, "status": STATUS_REJECTED, "rejected_at": now} for e in moved]
    doc["updated_at"] = now
    write_json_atomic(pending_path(vault), doc)
    return len(moved)


@_locked
def restore_rejected(vault: Path, ids: list[str]) -> int:
    """撤销驳回：原样放回待审。返回放回几条。"""
    doc = load_pending(vault)
    back = set(ids)
    moved = [e for e in doc["rejected"] if e.get("id") in back]
    if not moved:
        return 0
    have = {e.get("id") for e in doc["edges"]}
    doc["rejected"] = [e for e in doc["rejected"] if e.get("id") not in back]
    doc["edges"] += [{**{k: v for k, v in e.items() if k != "rejected_at"}, "status": STATUS_PENDING}
                     for e in moved if e.get("id") not in have]
    doc["updated_at"] = _now()
    write_json_atomic(pending_path(vault), doc)
    return len(moved)


# ---------------------------------------------------------------- 提议的新关系类型

@_locked
def add_type_proposals(vault: Path, proposals: list[dict], origin: dict, known: set[str]) -> list[dict]:
    """记下导入时模型提的新关系类型（2026-09-29 之前只是导入卡上一条提醒，写完就没了）。

    按类型名去重：已经登记的（`known`）、已经在待审里的、驳回过的，都不再记。返回真正新记的几条。
    """
    doc = load_pending(vault)
    have = known | {t.get("name") for t in doc["types"]} | {t.get("name") for t in doc["rejected_types"]}
    now = _now()
    added: list[dict] = []
    for p in proposals:
        name = str(p.get("type") or p.get("name") or "").strip()
        if not name or name in have:
            continue
        have.add(name)
        entry = {"name": name, "family": str(p.get("family") or "").strip(),
                 "why": str(p.get("why") or "").strip() or None, "origin": dict(origin), "created_at": now}
        doc["types"].append(entry)
        added.append(entry)
    if added:
        doc["updated_at"] = now
        write_json_atomic(pending_path(vault), doc)
    return added


@_locked
def settle_type(vault: Path, name: str, action: str) -> bool:
    """一条类型提议的去向：`registered`（登记了，拿掉）/ `reject`（挪进驳回，以后不再提）/
    `restore`（撤销驳回，放回待审）。返回有没有动到。"""
    doc = load_pending(vault)
    src, dst = ("rejected_types", "types") if action == "restore" else ("types", "rejected_types")
    hit = [t for t in doc[src] if t.get("name") == name]
    if not hit:
        return False
    doc[src] = [t for t in doc[src] if t.get("name") != name]
    if action == "reject":
        doc[dst] += [{**t, "rejected_at": _now()} for t in hit]
    elif action == "restore":
        doc[dst] += [{k: v for k, v in t.items() if k != "rejected_at"} for t in hit]
    doc["updated_at"] = _now()
    write_json_atomic(pending_path(vault), doc)
    return True


# ---------------------------------------------------------------- 归属建议（孤立节点该去哪）

@_locked
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


@_locked
def add_curated(vault: Path, node_ids: list[str], suggestion: dict) -> None:
    """记下「问 AI 该放哪」的结论：{节点 id: {group, why, model, ms, at}}。一批点共用一条。
    只是建议——放不放还是人点；节点放上画布之后它就没用了（Inbox 读的时候只看还在 Inbox 里的）。"""
    doc = load_pending(vault)
    now = _now()
    for nid in node_ids:
        doc["curated"][nid] = {**suggestion, "ids": list(node_ids), "at": now}
    doc["updated_at"] = now
    write_json_atomic(pending_path(vault), doc)


def load_curated(vault: Path) -> dict[str, dict]:
    return load_pending(vault)["curated"]


def load_homes(vault: Path) -> dict[str, dict]:
    """{节点 id: 建议}，同一个节点出现在多条里取最新的。"""
    out: dict[str, dict] = {}
    for h in load_pending(vault)["homes"]:
        for nid in h.get("node_ids") or []:
            out[nid] = h
    return out
