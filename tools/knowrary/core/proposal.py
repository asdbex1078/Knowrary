"""方案 JSON 的三级匹配：认领清单点、名字撞脸、和体系断开。

**为什么在 core**：这三件事原来只长在服务层（`server/importing.py`），于是
`knowrary.py article` 那条命令行导入拿到的是个缩水版——同一篇文章，网页端会认出
"这就是你清单里那个还没建的点"，命令行不会。导入本来就该只有一套口径，
入口不同不该改变结论。

判定顺序是有意的，后一级只看前一级没吃掉的：

1. **认领**（`claims`）：模型明说了"这个节点就是清单里的 X"，或者 id 本来就撞上了。
2. **撞脸**（`near_misses`）：没认领，但名字像得可疑——很可能是同一个东西起了两个名，
   只是模型没敢认。不替用户拍板，列出来让人点。
3. **孤立**（`isolated`）：整个连通块都没碰到已有节点，也没人认领。它们上图后只会掉进 Inbox。
"""
from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

from .projects import lists_of, load_projects

MAX_ARTICLE_CHARS = 80_000    # 一篇文章的上限：再长就该先切（批量那一步的事），别一口气塞给模型
NEAR_MISS_RATIO = 0.6         # 名字像到这个程度就值得让人看一眼


def check_length(text: str) -> str:
    """文章长度闸。超了抛 `ValueError`，上层各自翻成自己的报错类型。"""
    if len(text) > MAX_ARTICLE_CHARS:
        raise ValueError(f"文章太长（{len(text)} 字，上限 {MAX_ARTICLE_CHARS}）：先切成几段，一段一段导")
    return text


def project_points(vault: Path, index: dict, project: str | None) -> list[dict]:
    """当前项目清单里**还没建**的点：id / 名字 / 为什么学。没选项目就没有待认领。"""
    if not project:
        return []
    pr = (load_projects(vault).get("projects") or {}).get(project)
    if not pr:
        return []
    real = {n["id"] for n in index["nodes"] if not n.get("virtual")}
    out, seen = [], set()
    for ls in lists_of(pr):
        for stage in ls.get("stages") or []:
            for pt in stage.get("points") or []:
                pid = pt.get("id")
                if pid and pid not in real and pid not in seen:
                    seen.add(pid)
                    out.append({"id": pid, "name": pt.get("name") or pid, "why": pt.get("why") or ""})
    return out


def normalize_claims(plan: dict, points: list[dict], rename) -> tuple[dict, list[dict]]:
    """模型写了 `claims` 的节点：id 直接换成清单里的 id（连带关系、链接）。id 本来就等于清单 id 的也算认领。

    `rename` 是 `core.rename_in_plan`——从外面传进来是为了不和 `importing` 互相 import。
    """
    by_id = {p["id"]: p for p in points}
    claims: list[dict] = []
    for n in list(plan.get("nodes") or []):
        nid = str(n.get("id") or "")
        want = str(n.get("claims") or "").strip()
        if want and want in by_id and want != nid:
            plan = rename(plan, nid, want)
            nid = want
        if nid in by_id:
            claims.append({"node_id": nid, "point_id": nid, "point_name": by_id[nid]["name"]})
    for n in plan.get("nodes") or []:
        n.pop("claims", None)
    return plan, claims


def near_misses(plan: dict, points: list[dict], claimed: set[str]) -> list[dict]:
    """没认领、但名字和清单里某个点很像的新节点——很可能就是同一个东西起了两个名。"""
    out: list[dict] = []
    for n in plan.get("nodes") or []:
        nid = str(n.get("id") or "")
        if not nid or nid in claimed:
            continue
        mine = {_norm(nid), _norm(str(n.get("name") or ""))} - {""}
        best = None
        for p in points:
            theirs = {_norm(p["id"]), _norm(p["name"])} - {""}
            ratio = max((_similar(a, b) for a in mine for b in theirs), default=0.0)
            if ratio >= NEAR_MISS_RATIO and (best is None or ratio > best[0]):
                best = (ratio, p)
        if best:
            out.append({"node_id": nid, "point_id": best[1]["id"], "point_name": best[1]["name"],
                        "ratio": round(best[0], 2)})
    return out


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum() or "一" <= ch <= "鿿")


def _similar(a: str, b: str) -> float:
    if len(a) >= 2 and len(b) >= 2 and (a in b or b in a):
        # 一个是另一个的子串（`RNN` ⊂ `RNN与长程依赖`）：按长度比给 0.5～1，短的越接近长的越像
        return round(min(len(a), len(b)) / max(len(a), len(b)) * 0.5 + 0.5, 2)
    return SequenceMatcher(None, a, b).ratio()


def isolated(plan: dict, pending: list[dict], index: dict, claimed: set[str]) -> list[str]:
    """孤立 = 所在的连通块里没有任何一条边碰到已有节点、也没人认领清单点。

    按连通块算而不是按单个节点：`RNN` 只连了同篇拆出来的 `注意力机制`，而后者连着图里的 `a`——
    `RNN` 上图后不是孤岛，不该报。真正要提醒的是整块和体系断开的那些：它们只会掉进 Inbox。

    `pending` 是待审边（dict，含 `source` / `target`）：置信度不够没进 md，但它同样是"碰到了体系"
    的证据——审过了就是一条真边，现在就把它当锚点，免得刚导完先报一句孤立、审完又不是了。
    """
    existing = {n["id"] for n in index["nodes"] if not n.get("virtual")}
    new_ids = [str(n.get("id")) for n in plan.get("nodes") or [] if n.get("id") and n["id"] not in existing]
    parent = {nid: nid for nid in new_ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    anchored: set[str] = set(claimed)                    # 块里有谁碰到了体系
    for n in plan.get("nodes") or []:
        nid = str(n.get("id"))
        if nid not in parent:
            continue
        for r in n.get("relations") or []:
            tgt = str(r.get("target") or "")
            if tgt in existing:
                anchored.add(nid)
            elif tgt in parent:
                union(nid, tgt)
    for pe in pending:
        src, tgt = str(pe.get("source") or ""), str(pe.get("target") or "")
        if tgt in existing and src in parent:
            anchored.add(src)
    roots_ok = {find(x) for x in anchored if x in parent}
    return [nid for nid in new_ids if find(nid) not in roots_ok]
