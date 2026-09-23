"""写入审核的第一段：确定性检查（不调模型）。

**和另外两层的分工**（三层，从硬到软）：

1. `writer.plan` 的 `ChangeRejected`：请求本身不合法（改了不许改的字段、id 非法、
   正文里出现 `## 关系`…）。直接 422，连 diff 都不出，**永远在**。
2. **这一层**：拿 dry-run 已经算好的新内容重新看一遍，查"写进去之后"会不会留下欠账——
   未登记类型、正文死链、空摘要、正文太薄、孤点、撞名。纯本地、毫秒级、零成本，
   所以**开关关掉时它照样跑**，只是不挡路（`settings.audit_on` 只决定挡不挡、以及要不要问模型）。
3. `server/audit.py` 的第二段：review 角色看内容对不对。那一段花钱、要等，归开关管。

这一层为什么必须是确定性的：它查的全是"能算出来的事实"——类型在不在表里、链接指向的 id
存不存在。这类问题拿去问模型既慢又不稳，而且模型答错了你还得再核一遍。
**能算的不要问**，剩下的才值得花一次调用。
"""
from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

from .mdio import RE_LINK, split_frontmatter
from .relations import load_relation_types, parse_relations
from .sources import Resolver, sources_of
from .writer import split_sections

THIN_BODY = 120      # 新建节点的正文短于这么多字就提醒：入库不许精简（设计文档 F1.1）
NEAR_NAME = 0.86     # 名字相似到这个程度就提醒可能撞名。比导入那边的 0.6 严——
                     # 这里是"写之前拦一下"，误报的代价是每次写入都被烦，宁可漏报


def _text_of(edit) -> str:
    return edit.after or ""


def _is_new(edit) -> bool:
    """这次才建出来的文件。before 为空 = 之前没有这个文件。"""
    return not (edit.before or "").strip()


def _body_len(text: str) -> int:
    """正文有多少字：不算 frontmatter、不算关系段、不算空白。"""
    _, body, _, _ = split_sections(text)
    return len("".join(body.split()))


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _issue(level: str, code: str, path: str, message: str, fix: str = "") -> dict:
    return {"level": level, "code": code, "path": path, "message": message, "fix": fix}


def precheck(vault: Path, index: dict, edits: list) -> list[dict]:
    """写进去之后会留下哪些欠账。返回 `{level, code, path, message, fix}` 的列表。

    `level` 只有 `warn` 一档：这一层报的都是"能写进去、但写进去就欠着"的事
    （死链会变成 stub、未登记类型会进索引 warning），**没有一条该硬拦**。
    真要挡下是第二段和人的判断，不是这里。
    """
    types = load_relation_types(vault)
    known = {n["id"] for n in index.get("nodes", [])}
    names = {n["id"]: (n.get("name") or n["id"]) for n in index.get("nodes", [])}
    # 同一批里新建的那些也算"存在"：一篇文章拆出来的点互相链接是正常的，
    # 挨个报死链会把整张卡刷满噪音
    born = {_id_of(e) for e in edits if _is_new(e)} - {None}
    resolve = Resolver(vault)
    out: list[dict] = []
    for edit in edits:
        if not edit.changed:
            continue
        out += _check_one(edit, types, known | born, names, born)
        out += _check_sources(edit, resolve)
    return out


def _check_sources(edit, resolve: Resolver) -> list[dict]:
    """③ 来源链到了不存在的原文 / 原文目录外面。**只提醒不拦**：导入时原文和节点
    不一定是同一步落盘的，硬拦会卡住流程。纯文字的外部来源（一张图、一篇论文）不查。"""
    fm, _ = split_frontmatter(_text_of(edit))
    out: list[dict] = []
    for item in sources_of(fm):
        ref = resolve(item)
        if ref["kind"] != "article" or ref.get("legacy"):
            continue
        if not ref["exists"]:
            out.append(_issue("warn", "dead_source", edit.rel,
                              f"来源 `{item}` 指向的原文 `{ref['path']}` 不存在",
                              "先把原文放进原文目录；或者这是外部来源的话，去掉 [[ ]] 写成纯文字"))
        elif not ref["path"].startswith(resolve.articles + "/"):
            out.append(_issue("warn", "source_outside", edit.rel,
                              f"来源 `{item}` 不在原文目录 `{resolve.articles}/` 里",
                              "原文统一放进原文目录，界面上才认得出它是原文（设置 → 知识库）"))
    return out


def _id_of(edit) -> str | None:
    """文件路径反推 id：写回一律是 `<目录>/<id>.md`（writer.NODE_ROOTS 保证）。"""
    rel = (edit.rel or "").rsplit("/", 1)[-1]
    return rel[:-3] if rel.endswith(".md") else None


def _check_one(edit, types, exists: set[str], names: dict[str, str], born: set[str]) -> list[dict]:
    text = _text_of(edit)
    path = edit.rel
    fm, _ = split_frontmatter(text)
    _, body, rel_block, _ = split_sections(text)
    node_id = _id_of(edit)
    edges, bad_lines = parse_relations(rel_block, node_id or path)
    out: list[dict] = []

    # ① 关系类型没登记：索引里只会静静记一条 warning，写的人看不见
    for edge in edges:
        if not types.known(edge.type):
            out.append(_issue("warn", "unknown_type", path,
                              f"关系类型 `{edge.type}` 没在 relation-types.json 里登记"
                              f"（→ [[{edge.target}]]）",
                              "换一个已登记的类型；确实需要新类型就走 proposed_types，别直接写进去"))
    for line in bad_lines:
        out.append(_issue("warn", "bad_relation_line", path,
                          f"这一行关系解析不了，写进去等于没写：{line.strip()[:60]}",
                          "照 `- 类型:: [[目标]] (年份)` 写"))

    # ② 正文里的链接指向不存在的 id：解析器会补一个虚拟 stub，图上多一个灰点，
    #    而**你不会收到任何提示**——这正是要在写之前说一声的那类事
    for m in RE_LINK.finditer(body):
        target = (m.group(1) or "").strip()
        if target and target not in exists:
            out.append(_issue("warn", "dead_link", path,
                              f"正文链到了图里没有的 `[[{target}]]`，写进去会变成一个 stub 占位",
                              "要么先把它建出来，要么把链接改成纯文本"))

    if not _is_new(edit):
        return out

    # 下面三条只对**新建**的文件查：老文件的这些毛病归欠账清单，不该在改一行时翻旧账。
    # 摘要为空不在这里查——`writer._create_node_edit` 已经把 name / field / desc 当必填硬拦了。
    if _body_len(text) < THIN_BODY:
        out.append(_issue("warn", "thin_body", path,
                          f"正文只有 {_body_len(text)} 字，太薄了",
                          "按项目档位把它写足；入库不许精简，正文是以后出题和补充的唯一依据"))

    if not edges:
        out.append(_issue("warn", "no_relation", path, "新节点一条关系都没有，落地就是孤点",
                          "至少连一条——整个产品建在边上，孤点在图上等于没建"))

    if node_id:
        for other, name in names.items():
            if other == node_id or other in born:
                continue
            if _similar(node_id, other) >= NEAR_NAME or _similar(
                    str(fm.get("name") or node_id), name) >= NEAR_NAME:
                out.append(_issue("warn", "near_miss", path,
                                  f"和已有的 `{other}`（{name}）名字很像，可能是同一个概念",
                                  "真重复就别新建，去补充老节点；不是重复就当没看见"))
                break
    return out
