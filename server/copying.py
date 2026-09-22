"""跨库复制知识点：从别人的库里挑几个点，抄进自己的库。

这是**参考库的用法**——HunDun 那种公开出来"给人看"的库，看到有用的点应该能抄走，
而不是把整个库 fork 一份、或者直接在别人的库里学习（下次 pull 必冲突）。

**整条写回通道是白捡的。** `core.translate()` 是纯函数：给它一份"方案 JSON"和
**目标库的索引**，它就吐出变更集。所以跨库复制 = 把"文章拆出来的方案"换成
"从源库读出来的方案"，后面的三级匹配、stub 补壳、待审边、写回、存档一行都不用重写
（`importing.run` 原样复用）。这里只负责**造那份方案**。

三条规矩（2026-09-22 定）：

- **指向没选中的点 → 落 stub**，边不丢。丢掉边等于悄悄削掉知识结构，而且事后
  根本不知道缺了什么。勾上「把邻居也带上」则改成真复制一跳邻居。
- **指向目标库已有同 id 的点 → 进待审**，不直接连。**同 id 不等于同概念**，
  跨库尤其：你库里的「Agent」和我库里的「Agent」很可能不是一回事。
  这一条靠把置信度压到 `CONFIDENCE_DIRECT` 以下实现，`translate` 会自己把它们收进 pending。
- **落点目录固定、领域跟着源库走**：统一进 `nodes/_抄来的/`（一眼看得出哪些是抄的），
  但 frontmatter 的 `field` 保留源库的值，否则图上会挤成一坨。
"""
from __future__ import annotations

from pathlib import Path

from .contracts import ImportRequest
from .index_service import current_index
from .paths import core
from . import importing, vaults

FOLDER = "_抄来的"
NEIGHBOR_CONF = 1.0          # 两端都抄过来的边：确定，直接写
EXISTING_CONF = 0.5          # 连到目标库已有节点的边：压到直写线以下 → 进待审


class CopyRejected(ValueError):
    """源库不可用、一个点都没选中之类——给人看的话都在异常消息里。"""


def known_vaults() -> list[Path]:
    """能当源库的：当前库 + 最近用过的。和目录浏览同一套放行规则——
    这个接口会读另一个目录里的 md，不能让任意路径进来。"""
    cfg = vaults.load()
    out: list[Path] = []
    for raw in [cfg.get("current"), *(cfg.get("recent") or [])]:
        if not raw:
            continue
        path = Path(raw).expanduser().resolve()
        if path not in out and vaults.is_vault(path):
            out.append(path)
    return out


def resolve_source(raw: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if path not in known_vaults():
        raise CopyRejected(f"{path} 不在可选的知识库里（只能从当前库或最近用过的库复制）")
    return path


def catalog(src: Path, q: str = "", limit: int = 200) -> dict:
    """源库的节点清单，给"从别的库抽"那一侧的面板用。只读，不碰源库一个字节。"""
    index = current_index(src)
    key = q.strip().lower()
    rows = []
    for n in index["nodes"]:
        if n.get("virtual"):
            continue
        if key and key not in f"{n['id']} {n.get('name') or ''} {n.get('desc') or ''}".lower():
            continue
        rows.append({"id": n["id"], "name": n.get("name") or n["id"], "field": n.get("field") or "",
                     "desc": n.get("desc") or "", "status": n.get("status") or "",
                     "degree": n.get("degree") or 0})
    rows.sort(key=lambda r: (-r["degree"], r["id"]))
    return {"vault": str(src), "total": len(rows), "nodes": rows[:limit]}


# ---------------------------------------------------------------- 造方案

def _read(src: Path, entry: dict) -> tuple[dict, list]:
    """把源库的一个节点读成方案里的一条（正文、frontmatter、它声明的边）。"""
    node, _ = core.load_node(src, src / entry["path"])
    fm = node.fm or {}
    row = {"id": node.id, "name": fm.get("name") or entry.get("name") or node.id,
           "desc": fm.get("desc") or entry.get("desc") or "", "body": node.body,
           "field": fm.get("field") or entry.get("field") or ""}
    for k in ("type", "year", "aliases", "tags", "layer"):
        if fm.get(k):
            row[k] = fm[k]
    return row, list(node.edges)


def _neighbors(index: dict, picked: set[str]) -> set[str]:
    """选中点一跳之内、还没被选中的那些。"""
    out: set[str] = set()
    for e in index["edges"]:
        if e["source"] in picked and e["target"] not in picked:
            out.add(e["target"])
        elif e["target"] in picked and e["source"] not in picked:
            out.add(e["source"])
    return out


def build_plan(src: Path, ids: list[str], with_neighbors: bool, existing: set[str]) -> dict:
    """选中的点 → 一份导入方案。

    `existing` 是**目标库**已有的 id：连到它们的边压低置信度进待审，
    因为同 id 不代表同概念。
    """
    index = current_index(src)
    by_id = {n["id"]: n for n in index["nodes"] if not n.get("virtual")}
    picked = [i for i in dict.fromkeys(ids) if i in by_id]
    if not picked:
        raise CopyRejected("选中的点在源库里一个都找不到（改过名？换过库？）")
    chosen = set(picked)
    if with_neighbors:
        chosen |= {n for n in _neighbors(index, chosen) if n in by_id}

    nodes, stub_ids = [], set()
    for nid in sorted(chosen, key=lambda i: (i not in picked, i)):
        row, edges = _read(src, by_id[nid])
        rels = []
        for e in edges:
            if e.target == nid:
                continue
            if e.target not in chosen and e.target not in existing:
                stub_ids.add(e.target)          # 没抄过来、目标库也没有 → 补个壳，边不丢
            rels.append({"type": e.type, "target": e.target, "year": e.year,
                         "note": e.note or "",
                         "confidence": EXISTING_CONF if e.target in existing else NEIGHBOR_CONF})
        row["relations"] = rels
        nodes.append(row)

    stubs = []
    for sid in sorted(stub_ids):
        meta = by_id.get(sid) or {}
        stubs.append({"id": sid, "name": meta.get("name") or sid, "field": meta.get("field") or "",
                      "why": f"被抄过来的节点引用，正文还留在 {src.name}"})
    return {"nodes": nodes, "stubs": stubs,
            "summary": f"从 {src.name} 抄来 {len(nodes)} 个点"}


# ---------------------------------------------------------------- 执行

def run(target_vault: Path, src_raw: str, ids: list[str], *, with_neighbors: bool = False,
        dry_run: bool = True, renames: dict[str, str] | None = None,
        target_raw: str | None = None) -> dict:
    """预览 / 落盘。后半程整个交给 `importing.run`——同一条写回通道、同一份存档规则。

    **两个方向共用这一条**：站在参考库里往外推（给 `target_raw`），
    或站在自己库里从别处拉（不给，就写当前库）。两端都要在放行名单里。
    """
    src = resolve_source(src_raw)
    if target_raw:
        target_vault = resolve_source(target_raw)      # 写入方也只能是认识的库
    if src == target_vault:
        raise CopyRejected("源库和目标库是同一个，没什么可抄的")
    index = current_index(target_vault)
    existing = {n["id"] for n in index["nodes"] if not n.get("virtual")}
    plan = build_plan(src, ids, with_neighbors, existing)
    req = ImportRequest(plan=plan, field=_main_field(plan), source=f"抄自 {src.name}",
                        folder=FOLDER, dry_run=dry_run, renames=renames or {}, keep_field=True)
    result = importing.run(target_vault, req)
    return {"source_vault": str(src), "target_vault": str(target_vault),
            "picked": len(plan["nodes"]), "stubs": len(plan["stubs"]),
            **result.model_dump()}


def _main_field(plan: dict) -> str:
    """`field` 逐节点保留，这个只是兜底（某个节点在源库就没填领域时用）。取出现最多的那个。"""
    counts: dict[str, int] = {}
    for n in plan["nodes"]:
        name = (n.get("field") or "").strip()
        if name:
            counts[name] = counts.get(name, 0) + 1
    return max(counts, key=counts.get) if counts else "抄来的"
