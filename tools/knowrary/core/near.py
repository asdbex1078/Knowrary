"""写入前拦重复（2026-09-29，项目化重构方案 §5A）：要新建的点，图里可能已经有了。

2026-09-15 同一个目标被拆了两次，冒出三对近义重复——`RNN` / `RNN与长程依赖`、
`BPE分词` / `子词分词-BPE`、`因果掩码` / `注意力掩码`。id 精确去重一对都拦不住；
写入审核那条 `near_miss`（名字相似 0.86）又太严，也只在审核开着时才出现。

这里给变更卡和导入面板用：

- `candidates`：一个要新建的点，图里最像它的几个已有节点，带上"为什么判它像"；
- `relink`：把一份改法里的「新建 X」换成「并进已有的 Y」——不建 X，指向 X 的边改指 Y，
  X 的正文作为一段「补充」**追加**到 Y 末尾（不动 Y 的老正文），X 的名字进 Y 的别名。

**只提议、不自动并**（重构方案 4.4）：两个看起来像的点自动并掉，比多一个重复节点严重得多。
"""
from __future__ import annotations

from .digest import EMPTY_TAILS as _DIGEST_TAILS

RATIO = 0.8
CHARSET = 0.8          # 用字重合度：`BPE分词` / `子词分词-BPE` 字几乎一样、只是顺序不同
CHARSET_MIN = 4        # 短于这个字数不比用字——`ARM` / `RAM`、`CPU` / `GPU` 三个字母互相撞得太容易
CHARSET_SPAN = 2       # 两个名字的长度最多差这么多
LIMIT = 3
# 「加了等于没加」的后缀：`MHA` / `MHA机制` 是同一个东西写了两遍（和欠账面板的重复候选同一张表），
# 英文再加复数
EMPTY_TAILS = (*(t for t in _DIGEST_TAILS), "s", "es")


def _norm(s: str) -> str:
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum() or "一" <= ch <= "鿿")


def _score(a: str, b: str) -> tuple[float, str]:
    """两个名字是不是同一个东西。**宁可漏报**：只认三种几乎可以确定的情况。

    模糊匹配在实盘上误报远多于命中（2026-09-29 拿 HunDun 140 个点试：子串 + 相似度的口径
    有 59 个点冒候选，几乎全是 `ELF` / `ELF段` 这种上下位、`编译器前端` / `编译器后端` 这种兄弟、
    `CPU` / `GPU` 这种缩写撞车）。上下位和兄弟不是重复，是缺的那条边——那归欠账面板的连边建议。
    `RNN` / `RNN与长程依赖` 这种也因此不报：光看名字，它和 `ELF` / `ELF段` 分不开。
    """
    if not a or not b:
        return 0.0, ""
    if a == b:
        return 1.0, "同名"
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    tails = tuple(_norm(t) for t in EMPTY_TAILS)
    for rest in ((long_[len(short):] if long_.startswith(short) else None),
                 (long_[:-len(short)] if long_.endswith(short) else None)):
        if rest and rest in tails:
            return 0.95, f"只差一个「{rest}」"
    if short in long_:
        return 0.0, ""                    # 一个包含另一个：上下位或版本（`RISC` / `RISC-V`），不是重复
    if len(short) >= CHARSET_MIN and len(long_) - len(short) <= CHARSET_SPAN:
        sa, sb = set(a), set(b)
        jac = len(sa & sb) / len(sa | sb)
        if jac >= CHARSET:
            return round(jac, 2), "用字几乎一样，只是顺序不同"
    return 0.0, ""


def _names(node: dict) -> list[str]:
    return [n for n in {_norm(node.get("id")), _norm(node.get("name")),
                        *(_norm(a) for a in node.get("aliases") or [])} if n]


def candidates(index: dict, new: dict, exclude: set[str] | None = None) -> list[dict]:
    """`new` 是要新建的点（`id` / `name` / `aliases`）。返回最像它的已有节点，最多 LIMIT 个。
    没有 md 文件的（被引用出来的虚拟占位）不算——并不进去。"""
    mine = _names(new)
    skip = (exclude or set()) | {new.get("id")}
    out = []
    for node in index.get("nodes") or []:
        if node.get("virtual") or not node.get("path") or node["id"] in skip:
            continue
        score, why = max((_score(a, b) for a in mine for b in _names(node)), default=(0.0, ""))
        if score >= RATIO:
            out.append({"id": node["id"], "name": node.get("name") or node["id"], "desc": node.get("desc") or "",
                        "score": score, "why": why})
    out.sort(key=lambda c: -c["score"])
    return out[:LIMIT]


def for_changes(index: dict, changes: list[dict]) -> dict[str, list[dict]]:
    """一份改法里每个「新建」的候选：{新点 id: [候选…]}，没有候选的不出现。
    同一批里新建的那几个互相不算（一篇拆出来的点本来就可能名字相近）。"""
    born = {c.get("source") for c in changes if c.get("type") == "create_node"}
    out = {}
    for c in changes:
        if c.get("type") != "create_node":
            continue
        fields = c.get("fields") or {}
        got = candidates(index, {"id": c.get("source"), "name": fields.get("name"),
                                 "aliases": fields.get("aliases")}, exclude=born)
        if got:
            out[c["source"]] = got
    return out


def relink(changes: list[dict], index: dict, src: str, dst: str, head: str) -> tuple[list[dict], list[str]]:
    """把改法里的「新建 src」换成「并进已有的 dst」。返回 (新改法, 给人看的几句说明)。

    - 不建 src；它的正文加一行 `head` 引言后追加到 dst 末尾（**只追加**，dst 的老正文一个字不动）；
    - 别的改动里指向 src 的都改指 dst；src 自己的 `update_body` 降成追加（整篇替换 dst 会抹掉老正文）；
    - 改完变成自环的、dst 身上本来就有的、同一批里重复的边，丢掉；
    - 其他新点正文里的 `[[src]]` 改成 `[[dst]]`（不然会变成指向空的链接）；
    - src 的名字和 id 进 dst 的别名：以后搜这个名字能找到它，也不会再建一遍。
    """
    nodes = {n["id"]: n for n in index.get("nodes") or []}
    target = nodes.get(dst)
    if target is None or target.get("virtual"):
        raise ValueError(f"`{dst}` 不在图里（或者只是个占位），没法并进去")
    born = next((c for c in changes if c.get("type") == "create_node" and c.get("source") == src), None)
    if born is None:
        raise ValueError(f"这份改法里没有新建 `{src}`")
    have = {(e["type"], e["target"]) for e in index.get("edges") or [] if e["source"] == dst}
    out, notes, seen = [], [f"不新建「{src}」，并进已有的「{dst}」"], set()
    for c in changes:
        if c is born:
            continue
        c = _retarget(c, src, dst)
        if c["type"] == "add_edge":
            key = (c["source"], c["relation"], c["target"])
            if c["source"] == c["target"] or key in seen or (c["source"] == dst and key[1:] in have):
                notes.append(f"丢掉一条边 {c['source']} —{c['relation']}→ {c['target']}（自环或 {dst} 身上已经有了）")
                continue
            seen.add(key)
        out.append(c)
    body = str(born.get("body") or "").strip()
    if body:
        out.append({"type": "append_body", "source": dst, "body": f"{head}\n\n{body}"})
        notes.append(f"「{src}」的正文追加到「{dst}」末尾")
    alias = _alias_change(target, born, src)
    if alias:
        out.append(alias)
        notes.append(f"「{src}」进了「{dst}」的别名")
    return out, notes


def _retarget(c: dict, src: str, dst: str) -> dict:
    c = {**c}
    if c.get("source") == src:
        c["source"] = dst
        if c.get("type") == "update_body":
            c["type"] = "append_body"
    if c.get("target") == src:
        c["target"] = dst
    if isinstance(c.get("body"), str):
        c["body"] = c["body"].replace(f"[[{src}]]", f"[[{dst}]]").replace(f"[[{src}|", f"[[{dst}|")
    return c


def _alias_change(target: dict, born: dict, src: str) -> dict | None:
    return alias_change(target, [str((born.get("fields") or {}).get("name") or ""), src])


def alias_change(target: dict, names: list[str]) -> dict | None:
    """把几个名字加进已有节点的别名（原有的保留在前，和它自己的 id / 名字重的不加）。"""
    have = list(target.get("aliases") or [])
    taken = {_norm(x) for x in (*have, target["id"], target.get("name"))}
    add = [x for x in dict.fromkeys(names) if x and _norm(x) not in taken]
    if not add:
        return None
    return {"type": "update_frontmatter", "source": target["id"], "fields": {"aliases": [*have, *add]}}


def relink_in_plan(plan: dict, src: str, dst: str) -> dict:
    """导入方案里的同一件事：把新节点 `src` 换成「补充已有的 `dst`」。返回新方案，不改入参。

    正文进 `enrich`（导入本来就有的「补充老节点」，只追加、带来源引言）；它自己的边挂到 `dst` 上
    （`relink_edges`，照样按置信度分直写 / 待审）；名字进 `dst` 的别名（`relink_aliases`）；
    方案里别处指向 `src` 的，用 `rename_in_plan` 改指 `dst`。
    """
    import copy
    from .importing import rename_in_plan
    out = copy.deepcopy(plan)
    node = next((n for n in out.get("nodes") or [] if n.get("id") == src), None)
    if node is None:
        raise ValueError(f"方案里没有新节点 `{src}`")
    out["nodes"] = [n for n in out["nodes"] if n is not node]
    key = "merge_into" if "merge_into" in out and "enrich" not in out else "enrich"
    if str(node.get("body") or "").strip():
        out.setdefault(key, []).append({"existing": dst, "content": node["body"],
                                        "why": f"原本想新建「{src}」，和已有的「{dst}」是同一个概念"})
    out.setdefault("relink_edges", []).append({"id": dst, "relations": node.get("relations") or []})
    out.setdefault("relink_aliases", []).append({"existing": dst, "names": [node.get("name") or src, src]})
    return rename_in_plan(out, src, dst)
