"""对比表取数（《横向对比规范》4）：对比组 + 成员的 md → 一张可以直接渲染的表。

三条规则，**没有别名映射表**：

1. 这个维度映射到 frontmatter 吗（年份→year / 参数量→params / 抽象层→layer）→ 读它；
2. 否则看成员的 `## 速查` 里有没有**同名**的键 → 读它；
3. 都没有 → 空格子，表格留空，不报错。

别名表是在猜"你可能会怎么写"，维护成本永远在。写歪了的键不会消失——它落进
**残差列**（`extra`），一列干两件事：只有一个成员有的是真·独特点，两个以上成员都有的
是"这其实是个维度"，`promote` 里会把它列出来，点一下加进 `dimensions` 就行。

这一点是必须的：如果只按 `dimensions` 取值，写错名的那格显示为**空**，你看到的是
"这篇没写"，而看不出"写了但名字不对"。空格子和写错名必须能分开。

`source` 每格都带：frontmatter 的格子要走 `update_frontmatter` 改，速查的格子走
`set_fact` 改一行——编辑入口要按它分流，不能一律整段替换正文。
"""
from __future__ import annotations

from pathlib import Path

from .facts import FM_DIMENSIONS, facts_of
from .mdio import read
from .parser import load_node

COMPARE_TYPE = "对比组"
MEMBER_RELATION = "包含"
GAP_RATIO = 0.5          # 空成这样就该进欠账：半张表都是空的，摆出来也读不出东西


def groups(index: dict) -> list[dict]:
    """索引里的所有对比组，按 field 再按 name 排。目录用这一份，不另存数据。"""
    rows = [n for n in index["nodes"]
            if n.get("type") == COMPARE_TYPE and not n.get("virtual")]
    rows.sort(key=lambda n: ((n.get("field") or ""), n.get("name") or n["id"]))
    return [{"id": n["id"], "name": n.get("name") or n["id"], "field": n.get("field") or "",
             "desc": n.get("desc") or "", "columns": list(n.get("dimensions") or [])}
            for n in rows]


def _member_ids(vault: Path, index: dict, group: dict) -> list[str]:
    """成员 id，**按对比组 md 里 `- 包含::` 的书写顺序**。

    索引里的边是按 id 排过序的，拿它当列顺序，表格的行序就成了字典序——而你写下来的
    那个顺序（通常是年代或者演进次序）才是读表的顺序。想调整行序就去 md 里挪那几行。

    成员也可以是对方写的 `- 属于:: [[对比组]]`（索引会归一成同一条 `包含` 边），
    这种在 md 里找不到，按 id 排在后面。
    """
    from_index = {e["target"] for e in index["edges"]
                  if e["source"] == group["id"] and e["type"] == MEMBER_RELATION}
    out: list[str] = []
    path = group.get("path")
    if path and (vault / path).exists():
        node, _ = load_node(vault, vault / path)
        for edge in node.edges:
            if edge.type == MEMBER_RELATION and edge.target in from_index and edge.target not in out:
                out.append(edge.target)
    out += sorted(from_index - set(out))
    return out


def _cell(meta: dict, facts: dict, dim: str) -> dict:
    """一个格子：值 + 它是从哪儿来的。取不到值时 value 为 None。"""
    fm_key = FM_DIMENSIONS.get(dim)
    if fm_key and meta.get(fm_key) not in (None, "", []):
        return {"value": str(meta[fm_key]), "source": "frontmatter", "field": fm_key}
    if dim in facts:
        return {"value": facts[dim], "source": "速查"}
    return {"value": None, "source": None}


def _row(vault: Path, meta: dict | None, nid: str, columns: list[str]) -> dict:
    """一个成员一行。还没建出来的成员照样占一行——"这个还没写"本身就是表里的信息。"""
    meta = meta or {}
    body = ""
    path = meta.get("path")
    if path and (vault / path).exists():
        body = read(vault / path)
    facts = facts_of(body)
    cells = {dim: _cell(meta, facts, dim) for dim in columns}
    return {
        "id": nid,
        "name": meta.get("name") or nid,
        "built": bool(path) and not meta.get("virtual"),
        "stub": bool(meta.get("stub")),
        "cells": cells,
        # 残差：速查里没被 dimensions 认领的键，原样带出来。真·独特点和写歪了的键都在这儿
        "extra": {k: v for k, v in facts.items() if k not in columns},
    }


def table(vault: Path, index: dict, group_id: str) -> dict | None:
    """一个对比组的完整表。不是对比组、或者索引里没有这个 id，返回 None。"""
    meta_by_id = {n["id"]: n for n in index["nodes"]}
    group = meta_by_id.get(group_id)
    if group is None or group.get("type") != COMPARE_TYPE:
        return None
    columns = [str(d) for d in (group.get("dimensions") or []) if str(d).strip()]
    rows = [_row(vault, meta_by_id.get(nid), nid, columns)
            for nid in _member_ids(vault, index, group)]
    filled = sum(1 for r in rows for c in r["cells"].values() if c["value"] is not None)
    total = len(rows) * len(columns)
    return {
        "id": group_id,
        "name": group.get("name") or group_id,
        "field": group.get("field") or "",
        "desc": group.get("desc") or "",
        "columns": columns,
        "rows": rows,
        "uniform": _uniform(rows, columns),
        "promote": _promote(rows),
        "cells": total,
        "gaps": total - filled,
    }


def _uniform(rows: list[dict], columns: list[str]) -> list[str]:
    """所有成员取值完全相同的列——默认收起。

    对比表的信息量全在差异上，一列 20 行写着同一句话只是在占宽度。**有空格子的列不算
    一致**：那是"还没填"，不是"都一样"，收起来就等于把该补的东西藏了。
    """
    if len(rows) < 2:
        return []
    out = []
    for dim in columns:
        values = [r["cells"][dim]["value"] for r in rows]
        if None not in values and len(set(values)) == 1:
            out.append(dim)
    return out


def _promote(rows: list[dict]) -> list[dict]:
    """残差里出现在 ≥2 个成员上的键——"这看着是个维度，要不要升成一列"。

    这条规则替掉了别名映射表的另一半：映射表是猜你可能怎么写，这个是看你实际写成了什么。
    只出现一次的不提——那多半就是这个技术真正独特的地方，升成一列只会多一列空格。
    """
    seen: dict[str, list[str]] = {}
    for r in rows:
        for key in r["extra"]:
            seen.setdefault(key, []).append(r["id"])
    return [{"key": k, "count": len(ids), "members": ids}
            for k, ids in sorted(seen.items(), key=lambda kv: (-len(kv[1]), kv[0]))
            if len(ids) > 1]


def gaps(vault: Path, index: dict, ratio: float = GAP_RATIO) -> list[dict]:
    """空得过头的对比组，进欠账清单。点一条就该能直接去"补一轮"。"""
    out = []
    for g in groups(index):
        t = table(vault, index, g["id"])
        if not t or not t["cells"] or t["gaps"] / t["cells"] <= ratio:
            continue
        out.append({"id": t["id"], "name": t["name"], "gaps": t["gaps"], "cells": t["cells"],
                    "detail": f"{t['cells']} 个格子空了 {t['gaps']} 个"})
    out.sort(key=lambda d: (-d["gaps"], d["id"]))
    return out
