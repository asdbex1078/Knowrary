"""待审边的审核（2026-09-29）：导入时模型没把握的那些边，终于有地方看、有地方处理了。

以前 `.knowrary/pending.json` 只有导入往里写，**没有任何地方读**——进了待审就再也看不见。
这里补上读和处理两半：

- 列出来时顺手判一下它还算不算数：两端节点还在不在（`missing`）、md 里是不是已经连上了（`linked`）；
- 采纳走的还是写回层那一条路（`core.plan` → `core.commit`，写前自动备份），**写成了才从待审里拿掉**；
  可以改类型、换方向再采纳——模型常常是"该连，但类型选错了 / 方向反了"；
- 驳回留一笔（`core.reject_pending`），以后别的文章再推出同一条就不再进待审；能撤销。
"""
from __future__ import annotations

from pathlib import Path

from .contracts import PendingDecide, PendingDecided, PendingItem, PendingRead, PendingSkip
from .index_service import current_index, invalidate
from .paths import core

ACTIONS = ("adopt", "reject", "clear", "restore")


class PendingRejected(ValueError):
    """这一批决定整批不合法（没有决定、动作不认识）。"""


def _article_of(vault: Path, origin: dict) -> str:
    """这条边出自的那篇原文（在库里的话）：导入时记下的路径优先，老记录按文章名去原文目录里找。"""
    rel = str(origin.get("article") or "")
    if rel and (vault / rel).is_file():
        return rel
    name = str(origin.get("source") or "").strip()
    if not name:
        return ""
    guess = f"{core.load_vault_config(vault)['articles_dir']}/{core.safe_name(name)}.md"
    return guess if (vault / guess).is_file() else ""


def _linked(index: dict, rt, e: dict) -> bool:
    """md 里是不是已经有这条了（后来手动连上的，或者另一篇文章直接写进去了）。
    索引里的边是归一过方向的，所以同一对节点、类型对得上（含反向类型的规范名）都算。"""
    s, t, rel = e["source"], e["target"], e["relation"]
    canon = rt.canonical(rel)
    for edge in index["edges"]:
        if edge["source"] == s and edge["target"] == t and edge["type"] in (rel, canon):
            return True
        if canon and edge["source"] == t and edge["target"] == s and edge["type"] == canon:
            return True
    return False


def _item(e: dict, nodes: dict, index: dict, rt, vault: Path) -> PendingItem:
    src, tgt = nodes.get(e["source"]), nodes.get(e["target"])
    real = src is not None and not src.get("virtual") and tgt is not None
    state = "missing" if not real else ("linked" if _linked(index, rt, e) else "ok")
    origin = e.get("origin") or {}
    return PendingItem(
        id=e["id"], source=e["source"], relation=e["relation"], target=e["target"], year=e.get("year"),
        note=e.get("note"), confidence=float(e.get("confidence") or 0), origin=origin,
        created_at=e.get("created_at"), rejected_at=e.get("rejected_at"), state=state,
        family=rt.family(e["relation"]),
        source_name=(src or {}).get("name") or e["source"], source_desc=(src or {}).get("desc") or "",
        target_name=(tgt or {}).get("name") or e["target"], target_desc=(tgt or {}).get("desc") or "",
        article=_article_of(vault, origin))


def read(vault: Path) -> PendingRead:
    doc = core.load_pending(vault)
    index = current_index(vault)
    rt = core.load_relation_types(vault)
    nodes = {n["id"]: n for n in index["nodes"]}
    return PendingRead(edges=[_item(e, nodes, index, rt, vault) for e in doc["edges"]],
                       rejected=[_item(e, nodes, index, rt, vault) for e in doc["rejected"]])


def decide(vault: Path, req: PendingDecide) -> PendingDecided:
    """一批决定。**先写 md、写成了再改 pending.json**：写不进去的那几条原样留在待审里。
    整段持 pending 的锁：两批决定并发的话，一批读到的「待审里有哪些」会被另一批改掉（同一条被采纳两次）。"""
    with core.PENDING_LOCK:
        return _decide(vault, req)


def _decide(vault: Path, req: PendingDecide) -> PendingDecided:
    if not req.decisions:
        raise PendingRejected("没有要处理的边")
    bad = [d.action for d in req.decisions if d.action not in ACTIONS]
    if bad:
        raise PendingRejected(f"不认识的动作：{'、'.join(bad)}（只有 {' / '.join(ACTIONS)}）")
    doc = core.load_pending(vault)
    waiting = {e["id"]: e for e in doc["edges"]}
    out = PendingDecided()
    changes, adopt_ids = _adoptions(vault, req, waiting, out)
    if changes:
        index = current_index(vault)
        try:
            edits = core.plan(vault, changes, index)
        except (core.ChangeRejected, core.WriteConflict) as exc:
            # 一批里有一条写不进去就整批不写：它们是同一次点击，半截落盘比全不落更难收拾
            out.skipped += [PendingSkip(id=i, why=str(exc)) for i in adopt_ids]
            adopt_ids = []
        else:
            out.backup = core.commit(vault, edits) or None
            invalidate(vault)
    if adopt_ids:
        core.remove_pending(vault, adopt_ids)
        out.adopted = adopt_ids
    out.rejected = _ids(req, "reject", waiting)
    out.cleared = _ids(req, "clear", waiting)
    core.reject_pending(vault, out.rejected)
    core.remove_pending(vault, out.cleared)
    restore = [d.id for d in req.decisions if d.action == "restore"]
    if restore and core.restore_rejected(vault, restore):
        out.restored = restore
    out.index_revision = current_index(vault)["revision"]
    return out


def _ids(req: PendingDecide, action: str, waiting: dict) -> list[str]:
    return [d.id for d in req.decisions if d.action == action and d.id in waiting]


def _adoptions(vault: Path, req: PendingDecide, waiting: dict, out: PendingDecided) -> tuple[list, list]:
    """把「采纳」翻成 add_edge：可以改类型、换方向。类型没登记、两端不在的，跳过并说明。"""
    rt = core.load_relation_types(vault)
    nodes = {n["id"]: n for n in current_index(vault)["nodes"]}
    changes, ids = [], []
    for d in req.decisions:
        if d.action != "adopt":
            continue
        e = waiting.get(d.id)
        if e is None:
            out.skipped.append(PendingSkip(id=d.id, why="待审里已经没有这条了（可能别处已经处理过）"))
            continue
        rel = (d.relation or e["relation"]).strip()
        src, tgt = (e["target"], e["source"]) if d.swap else (e["source"], e["target"])
        if not rt.known(rel):
            out.skipped.append(PendingSkip(id=d.id, why=f"关系类型 `{rel}` 没在 relation-types.json 里登记"))
            continue
        if src not in nodes or nodes[src].get("virtual") or tgt not in nodes:
            out.skipped.append(PendingSkip(id=d.id, why=f"`{src}` 或 `{tgt}` 已经不在图里了，只能驳回"))
            continue
        change = {"type": "add_edge", "source": src, "relation": rel, "target": tgt}
        if e.get("year"):
            change["year"] = e["year"]
        # 改过类型或方向的不带原来那句备注：它说的是模型推的那条边（「乙依赖丙」），不是现在这条
        if e.get("note") and not d.swap and rel == e["relation"]:
            change["note"] = e["note"]
        changes.append(change)
        ids.append(d.id)
    return changes, ids
