"""碎片记忆（2026-10-23）：记（POST）、看（GET，带积压统计）、归拢（一次 learn 调用给分簇建议）。

归拢只出**建议**，不写盘：每簇的落地由前端走现成的路——enrich 走 `/api/changes` 的
append_body（写入审核照常把门）、new 把整理好的内容丢给导入面板、keep 什么都不动；
落完再来 `/api/fragments/mark` 给碎片标去向。这里不开第二条写 md 的路。
"""
from __future__ import annotations

from pathlib import Path

from .contracts import FragmentCluster, FragmentEntry, FragmentsConsolidated, FragmentsRead
from .index_service import current_index
from .llm_call import ask_meta
from .paths import core

MAX_OPEN = 200     # 一次归拢最多喂多少条：再多提示词就失控了，先归老的


class FragmentsRejected(Exception):
    """记不进去 / 归拢不了，消息是给人看的。"""


def read(vault: Path) -> FragmentsRead:
    entries = core.list_fragments(vault)
    return FragmentsRead(dir=core.FRAGMENTS_DIR, entries=[FragmentEntry(**f) for f in entries],
                         **core.fragment_stats(entries))


def add(vault: Path, text: str) -> FragmentEntry:
    try:
        return FragmentEntry(**core.add_fragment(vault, text))
    except ValueError as exc:
        raise FragmentsRejected(str(exc)) from exc


def mark(vault: Path, ids: list[str], target: str) -> int:
    return core.mark_fragments(vault, ids, target.strip())


def consolidate(vault: Path) -> FragmentsConsolidated:
    """把全部未归拢的碎片交给 learn 角色分簇。只出建议不写盘；最老的先归。"""
    entries = core.list_fragments(vault)
    open_ = [f for f in entries if not f["target"]]
    if not open_:
        raise FragmentsRejected("碎片箱是空的（或全归拢过了），没什么可归的")
    open_ = sorted(open_, key=lambda f: (f["date"], f["time"]))[:MAX_OPEN]
    index = current_index(vault)
    prompt = core.build_consolidate_prompt(core.cards_from_index(index), open_)
    raw, meta = ask_meta(vault, "learn", prompt, op="fragments")
    data = core.parse_json(raw, "fragments", vault=vault)
    node_ids = {n["id"] for n in index["nodes"] if not n.get("virtual")}
    clusters, issues = core.check_fragment_clusters(data, {f["id"] for f in open_}, node_ids)
    if not clusters:
        raise FragmentsRejected(f"模型没给出可用的分簇（{meta['model']}）。原始回复已记进问题流，再试一次或换个模型")
    return FragmentsConsolidated(clusters=[FragmentCluster(**c) for c in clusters], issues=issues,
                                 model=meta["model"], ms=meta["ms"])
