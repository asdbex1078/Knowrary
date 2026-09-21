"""index.json 生成：vault 全量重建，方向归一 + 反链邻接 + stub 识别 + 诊断。

契约见设计文档 3.4.1。要点：
- index.json 是**派生缓存**，可以随时删掉重建；知识真相源只有 md。
- 内容哈希决定 revision：同一份 vault 重复生成结果逐字节一致，revision 与
  generated_at 都不动，避免无谓触发 layout 的乐观并发校验。
- 边只存归一化后的正向边，反链通过节点上的 in/out 邻接表表达，不重复存储。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .analysis import find_cycles, pagerank
from .diagnostics import Diagnostics
from .mdio import RE_LINK, json_safe, load_json
from .compare import COMPARE_TYPE, MEMBER_RELATION
from .schools import SCHOOL_TYPE
from .facts import (COMPARE_HEADING, FACTS_HEADING, FM_DIMENSIONS, bare_compare_headings,
                    compare_targets, facts_of, section_bounds, stray_facts)
from .parser import Node, is_aggregate, load_vault, validate_frontmatter, parse_params
from .relations import NormalizedEdge, RelationTypes, load_relation_types, normalize_direction

# 演化族里「源比目标晚」属于正常的那几个类型（见 _warn_year_inverted）。
NEWER_FIRST = ("修订",)

INDEX_SCHEMA_VERSION = 1
NODE_FM_FIELDS = ("name", "field", "type", "status", "desc", "year", "start_year", "end_year",
                  "aliases", "tags", "learned", "source", "layer", "params", "dimensions", "color")


@dataclass
class BuildContext:
    """一次索引构建的全部中间产物，避免在辅助函数间传 6 个参数。"""

    vault: Path
    rt: RelationTypes
    diags: Diagnostics
    nodes: dict[str, Node]
    edges: dict[str, NormalizedEdge]
    virtual: dict[str, list[str]]   # 被引用但无 md 文件的节点 → 引用它的文件


@dataclass
class IndexResult:
    data: dict
    diags: Diagnostics
    changed: bool          # 与磁盘上的 index.json 相比内容是否变化

    @property
    def stats(self) -> dict:
        return self.data["stats"]


def build_index(vault: Path, previous: dict | None = None) -> IndexResult:
    """全量解析 vault 并生成 index 数据结构（不写盘）。"""
    nodes, diags = load_vault(vault)
    ctx = BuildContext(vault, load_relation_types(vault), diags, nodes, {}, {})
    for node in nodes.values():
        validate_frontmatter(vault, node, diags)
    _collect_edges(ctx)
    _resolve_targets(ctx)
    _warn_history_gaps(ctx)
    _warn_year_conflicts(ctx)
    _warn_dead_body_links(ctx)
    _check_compare(ctx)
    payload = _assemble(ctx)
    _warn_cycles(payload, ctx)
    return _finalize(payload, diags, previous)


def _collect_edges(ctx: BuildContext) -> None:
    """归一方向、按 (源,目标,类型) 去重，把"两侧都写"合并成一条边并记录声明文件。"""
    rt, diags, out = ctx.rt, ctx.diags, ctx.edges
    for node in sorted(ctx.nodes.values(), key=lambda n: n.id):
        rel = node.path.relative_to(ctx.vault).as_posix()
        for raw in node.edges:
            src, tgt, typ = normalize_direction(raw, rt)
            if not rt.known(raw.type):
                diags.warn("unknown_type", f"未登记类型 `{raw.type}` → [[{raw.target}]]，"
                                           f"按 {rt.default_family} 渲染", file=rel, node=node.id)
            if src == tgt:
                diags.warn("self_loop", f"自环关系 `{raw.type} [[{raw.target}]]`，已忽略",
                           file=rel, node=node.id)
                continue
            edge = NormalizedEdge(src, tgt, typ, rt.family(typ), symmetric=rt.symmetric(typ))
            existing = out.get(edge.id)
            if existing is None:
                edge.raw_types = [raw.type]
                edge.year, edge.note, edge.declared_in = raw.year, raw.note, [rel]
                out[edge.id] = edge
            else:
                _merge_edge(existing, raw, rel, diags, node.id)


def _merge_edge(existing: NormalizedEdge, raw, rel: str, diags: Diagnostics, node_id: str) -> None:
    """同一条归一化边被第二次声明：合并年份/说明，重复维护给警告。"""
    if raw.type not in existing.raw_types:
        existing.raw_types.append(raw.type)
    if existing.year is None:
        existing.year = raw.year
    if not existing.note:
        existing.note = raw.note
    if rel in existing.declared_in:
        diags.warn("duplicate_relation", f"同一文件重复声明 `{raw.type} [[{raw.target}]]`",
                   file=rel, node=node_id, edge=existing.id)
        return
    existing.declared_in.append(rel)
    kind = "对称关系两侧都写了" if existing.symmetric else "与对方的互逆关系重复维护"
    diags.warn("duplicate_relation", f"{kind}：`{raw.type} [[{raw.target}]]`（另一侧 "
                                     f"{existing.declared_in[0]}），索引已合并为一条边",
               file=rel, node=node_id, edge=existing.id)


def _resolve_targets(ctx: BuildContext) -> None:
    """未创建的引用目标 → 虚拟 stub 节点（不丢边），记录 {stub id: 引用来源文件}。"""
    virtual: dict[str, list[str]] = defaultdict(list)
    for edge in ctx.edges.values():
        for endpoint in (edge.source, edge.target):
            if endpoint in ctx.nodes:
                continue
            for rel in edge.declared_in:
                if rel not in virtual[endpoint]:
                    virtual[endpoint].append(rel)
                    ctx.diags.error("unknown_target", f"关系目标 [[{endpoint}]] 不存在（{edge.type}），"
                                                      f"已按 stub 占位", file=rel, edge=edge.id)
    ctx.virtual = dict(virtual)


def _warn_history_gaps(ctx: BuildContext) -> None:
    """演化族边两端都没有年份时进不了历史视图，提前提示。"""
    def year_of(nid: str):
        node = ctx.nodes.get(nid)
        return node.fm.get("year") if node else None

    for edge in ctx.edges.values():
        if edge.family != "演化" or edge.year is not None:
            continue
        if year_of(edge.source) is None and year_of(edge.target) is None:
            ctx.diags.warn("evolution_without_year",
                       f"演化边 `{edge.source} {edge.type} {edge.target}` 两端都无 year，"
                           f"不会进入历史视图", file=edge.declared_in[0], edge=edge.id)


def _warn_year_conflicts(ctx: BuildContext) -> None:
    """演化族边两端年份倒挂：`A 演化为 B` 而 A 比 B 还晚。

    **这是唯一不依赖外部知识的年份矫正**：它不问"1997 对不对"，只问"这条线自己自洽吗"。
    年份写错时，十有八九会和图里已有的某条演化关系撞上——
    口述一句"year 填 2017"没人能核，但"它比它的前身还早"是能算出来的。

    `被激活`（跨领域点燃）同理：点燃者不可能晚于被点燃的那个。
    """
    def year_of(nid: str):
        node = ctx.nodes.get(nid)
        y = node.fm.get("year") if node else None
        return y if isinstance(y, int) and not isinstance(y, bool) else None

    for edge in ctx.edges.values():
        # **演化族里只有 `修订` 是「新的指向旧的」**：`A 修订 B` 的主语是修订者，
        # 它必然比被修订的那个晚（反向传播 1986 修订 Perceptrons 1969）。
        # 其余几个（演化为 / 扩展为 / 源自归一后的演化为 / 被激活）都是旧→新。
        # 一刀切按旧→新判的话，`修订` 每用一次报一次 —— 而**误报比不报更糟**：
        # 报几次之后人就开始无视这条诊断，真的倒挂那次也跟着被无视了。
        if edge.family != "演化" or edge.type in NEWER_FIRST:
            continue
        a, b = year_of(edge.source), year_of(edge.target)
        if a is None or b is None or a <= b:
            continue
        ctx.diags.warn("year_inverted",
                       f"`{edge.source}`（{a}）{edge.type} `{edge.target}`（{b}）——"
                       f"年份倒挂了：源比目标还晚，多半有一个写错了",
                       file=edge.declared_in[0], edge=edge.id)


def _warn_dead_body_links(ctx: BuildContext) -> None:
    for node in ctx.nodes.values():
        rel = node.path.relative_to(ctx.vault).as_posix()
        for link in sorted(set(RE_LINK.findall(node.body))):
            if link not in ctx.nodes and link not in ctx.virtual:
                ctx.diags.warn("dead_body_link", f"正文链接 [[{link}]] 不存在", file=rel, node=node.id)


MIN_MEMBERS = 2


def _check_compare(ctx: BuildContext) -> None:
    """《横向对比规范》6 的校验：对比组本身、以及任意节点里的 `## 速查` / `## 对比项`。

    放在索引构建里而不是 `validate_frontmatter` 里，是因为前四条都要看**边**
    （成员是 `包含` 边、对比项要对上 `对比` 边），而那个函数只拿得到 frontmatter。
    """
    out_by_type: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for edge in ctx.edges.values():
        out_by_type[edge.source][edge.type].add(edge.target)
        if edge.symmetric:                       # 对比是对称边，索引按 id 排序定向，两头都算
            out_by_type[edge.target][edge.type].add(edge.source)

    for node in ctx.nodes.values():
        rel = node.path.relative_to(ctx.vault).as_posix()
        loc = {"file": rel, "node": node.id}
        if node.fm.get("type") == COMPARE_TYPE:
            _check_compare_group(node, out_by_type[node.id].get(MEMBER_RELATION, set()),
                                 ctx.diags, loc)
        if node.fm.get("type") == SCHOOL_TYPE:
            _check_school(node, out_by_type[node.id].get(MEMBER_RELATION, set()), ctx.diags, loc)
        _check_node_facts(node, out_by_type[node.id].get("对比", set()), ctx.diags, loc)


def _check_compare_group(node: Node, members: set[str], diags: Diagnostics, loc: dict) -> None:
    """对比组文档自己：成员够不够、维度有没有。"""
    if len(members) < MIN_MEMBERS:
        diags.error("compare_too_few_members",
                    f"对比组只有 {len(members)} 个成员（要 ≥{MIN_MEMBERS}）。"
                    f"成员写成 `- 包含:: [[节点]]`，不要写进 frontmatter 数组——"
                    f"数组在改名时会悄悄悬空", **loc)
    dims = node.fm.get("dimensions")
    if not isinstance(dims, list) or not [d for d in dims if str(d).strip()]:
        diags.error("compare_no_dimensions", "对比组缺 `dimensions`，表格没有列", **loc)
    elif len(set(map(str, dims))) != len(dims):
        diags.warn("compare_dup_dimension", f"`dimensions` 里有重复的维度：{dims}", **loc)


def _check_school(node: Node, members: set[str], diags: Diagnostics, loc: dict) -> None:
    """流派文档自己：有没有时间范围、成员够不够。

    **`start_year` 是必填的，因为整个流派功能就靠它排序。** 历史视图里流派是一条
    横跨 start_year～end_year 的时间带，没有起点它既排不了序也画不出带子——
    而这个失败是静默的：文档照常解析、照常入索引，只是**在历史视图里根本不出现**。
    `end_year` 可以不填，那表示"到现在还没结束"（连接主义就是）。
    """
    if not isinstance(node.fm.get("start_year"), int):
        diags.error("school_no_start_year",
                    "流派缺 `start_year`，历史视图里排不了序也画不出时间带（不填会静默消失）。"
                    "还在延续的流派 `end_year` 留空即可", **loc)
    if len(members) < MIN_MEMBERS:
        diags.warn("school_too_few_members",
                   f"流派只有 {len(members)} 个成员（建议 ≥{MIN_MEMBERS}）。"
                   f"成员写成 `- 包含:: [[节点]]`——**一个技术可以同时属于两个流派**，"
                   f"结构族不限制这个，重叠是这套东西本来就要表达的事", **loc)


def _check_node_facts(node: Node, compared: set[str], diags: Diagnostics, loc: dict) -> None:
    """任意节点里的两节：位置、速查写法、和 frontmatter 撞车、对比项有没有对应的边。"""
    # **位置写错是静默失效**：`## 关系` 之后的正文被切成 rel_tail，取数、出题、摘要
    # 读的都是关系段之前那一段（`parser.py` 的 body / `server/quiz.py` 的 `_read_body`）。
    # 追加到文件末尾是最顺手的写法，也正是会掉进这个坑的写法——所以必须有人喊一声。
    for heading in (FACTS_HEADING, COMPARE_HEADING):
        if section_bounds(node.rel_tail, heading) is not None:
            diags.warn("facts_after_relations",
                       f"`## {heading}` 写在了 `## 关系` 后面，取数和出题都读不到它——"
                       f"挪到 `## 关系` 之前", **loc)
    body = node.body
    for key in facts_of(body):
        if key in FM_DIMENSIONS:
            diags.warn("facts_shadows_frontmatter",
                       f"`## 速查` 里写了 `{key}`，它的真相在 frontmatter 的 "
                       f"`{FM_DIMENSIONS[key]}`——两份一定会漂，删掉正文这行", **loc)
    bad_lines, dup_keys = stray_facts(body)
    for ln in bad_lines:
        diags.warn("facts_bad_syntax",
                   f"`## 速查` 里这行取不到值：`{ln}`。要用 `::`（Dataview 内联字段），"
                   f"单冒号会被整行忽略", **loc)
    for key in dup_keys:
        diags.warn("facts_duplicate_key", f"`## 速查` 里 `{key}` 写了不止一次，只会用第一条", **loc)
    for target in compare_targets(body):
        if target not in compared:
            diags.warn("compare_section_without_edge",
                       f"`## 对比项` 里写了「与 [[{target}]]」，却没有一条 `对比:: [[{target}]]` 边——"
                       f"图上看不见这条对比", **loc)
    for title in bare_compare_headings(body):
        diags.warn("compare_section_no_link",
                   f"`## 对比项` 的小节「{title}」没写 `[[目标]]`，对不上任何节点", **loc)


def _node_payload(vault: Path, node: Node) -> dict:
    d = {"id": node.id, "path": node.path.relative_to(vault).as_posix(), "digest": node.digest}
    for k in NODE_FM_FIELDS:
        v = node.fm.get(k)
        if v not in (None, "", []):
            d[k] = json_safe(v)
    # 参数量在索引里存**解析好的数值**：前端画图不该各写一遍"175B 是多少"，
    # 而 md 里仍然是人写得顺手的那种写法
    n = parse_params(node.fm.get("params"))
    if n is not None:
        d["params_n"] = n
    d.setdefault("status", "active")
    # 聚合文档（对比组 / 领域总览）：算一遍存进来，下游只看这个布尔值，
    # 不用每处都去认那几个 type 字符串
    if is_aggregate(node.fm):
        d["aggregate"] = True
    if node.is_stub:
        d["stub"] = True
    return d


def _virtual_payload(nid: str, referrers: list[str]) -> dict:
    """被引用但没有 md 文件的节点：占位进图，标记 virtual，等待补写。"""
    return {"id": nid, "name": nid, "desc": "待补充", "status": "stub",
            "stub": True, "virtual": True, "referrers": sorted(referrers)}


def _assemble(ctx: BuildContext) -> dict:
    """拼装 nodes/edges/families/stubs/stats，并挂上 in/out 邻接。"""
    payloads = [_node_payload(ctx.vault, n) for n in ctx.nodes.values()]
    payloads += [_virtual_payload(nid, refs) for nid, refs in ctx.virtual.items()]
    payloads.sort(key=lambda d: d["id"])
    edge_list = [e.to_dict() for e in sorted(ctx.edges.values(), key=lambda e: e.id)]
    out_map, in_map = defaultdict(list), defaultdict(list)
    for e in edge_list:
        out_map[e["source"]].append(e["id"])
        in_map[e["target"]].append(e["id"])
    ranks = pagerank([d["id"] for d in payloads], edge_list)
    top = max(ranks.values(), default=1) or 1
    for d in payloads:
        d["out"], d["in"] = out_map.get(d["id"], []), in_map.get(d["id"], [])
        d["degree"] = len(d["out"]) + len(d["in"])
        # rank：pageRank 原值（总和 1）；weight：归一到 0~1，前端直接拿来定节点大小
        d["rank"] = round(ranks.get(d["id"], 0), 6)
        d["weight"] = round(ranks.get(d["id"], 0) / top, 4)
    return {
        "nodes": payloads,
        "edges": edge_list,
        "families": _families_payload(ctx.rt, edge_list),
        "stubs": sorted(d["id"] for d in payloads if d.get("stub")),
        "stats": _stats(payloads, edge_list, ctx.virtual, ctx.diags),
        "errors": ctx.diags.sorted_dicts("error"),
        "warnings": ctx.diags.sorted_dicts("warning"),
    }


def _warn_cycles(payload: dict, ctx: BuildContext) -> None:
    """同族短环 = 方向矛盾（A 包含 B 又 B 包含 A），写进警告，`knowrary check` 能直接看到。"""
    declared = {e["id"]: e.get("declared_in", [""])[0] for e in payload["edges"]}
    for cycle in find_cycles(payload["edges"]):
        path = cycle["path"]
        first = f"{path[0]}->{path[1]}#"
        file = next((f for eid, f in declared.items() if eid.startswith(first)), "")
        ctx.diags.warn("relation_cycle",
                       f"{cycle['family']}族存在环：{' → '.join(path)}（方向矛盾，需要删掉其中一条）",
                       file=file)
    payload["errors"] = ctx.diags.sorted_dicts("error")
    payload["warnings"] = ctx.diags.sorted_dicts("warning")
    payload["stats"]["warnings"] = len(ctx.diags.warnings)
    payload["stats"]["cycles"] = len(find_cycles(payload["edges"]))


def _type_meta(rt: RelationTypes, t: str) -> dict:
    """一个关系类型的方向信息：反向类型 / 归一到的正向类型 / 是否对称。

    前端建立关系时要当场告诉用户"反过来怎么读"（A 基于 B ⇒ B 支撑 A）——
    方向是这套类型表里最容易写反的东西，写反了图就从知识网退化成乱指的箭头。
    """
    meta = {}
    if rt.inverse(t):
        meta["inverse"] = rt.inverse(t)
    if rt.canonical(t):
        meta["canonical"] = rt.canonical(t)
    if rt.symmetric(t):
        meta["symmetric"] = True
    return meta


def _families_payload(rt: RelationTypes, edge_list: list[dict]) -> list[dict]:
    per_family: dict[str, Counter] = defaultdict(Counter)
    for e in edge_list:
        per_family[e["family"]][e["type"]] += 1
    known: dict[str, list[str]] = defaultdict(list)
    for t, meta in rt.types.items():
        known[meta["family"]].append(t)
    families = list(dict.fromkeys(list(rt.families) + sorted(per_family)))
    out = []
    for fam in families:
        types = sorted(set(known.get(fam, [])) | set(per_family[fam]))
        out.append({"name": fam,
                    "default": fam == rt.default_family,
                    "types": types,
                    "meta": {t: _type_meta(rt, t) for t in types},
                    "edge_count": sum(per_family[fam].values())})
    return out


def _stats(payloads: list[dict], edge_list: list[dict], virtual: dict, diags: Diagnostics) -> dict:
    return {
        "nodes": len(payloads),
        "edges": len(edge_list),
        "stubs": sum(1 for d in payloads if d.get("stub")),
        "missing_targets": len(virtual),
        "with_year": sum(1 for d in payloads if d.get("year")),
        "errors": len(diags.errors),
        "warnings": len(diags.warnings),
        "by_family": dict(sorted(Counter(e["family"] for e in edge_list).items())),
        "by_field": dict(sorted(Counter(d.get("field", "(未指定)") for d in payloads).items())),
    }


def content_hash(payload: dict) -> str:
    body = {k: v for k, v in payload.items()
            if k not in ("schema_version", "revision", "generated_at", "content_hash")}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _finalize(payload: dict, diags: Diagnostics, previous: dict | None) -> IndexResult:
    """内容哈希相同 → 沿用旧 revision 与 generated_at（幂等）；不同 → revision +1。"""
    digest = content_hash(payload)
    prev_rev = int(previous.get("revision", 0)) if previous else 0
    unchanged = bool(previous) and previous.get("content_hash") == digest
    head = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "revision": prev_rev if unchanged else prev_rev + 1,
        "generated_at": previous["generated_at"] if unchanged and previous.get("generated_at")
                        else dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "content_hash": digest,
    }
    return IndexResult({**head, **payload}, diags, changed=not unchanged)


def load_previous(path: Path) -> dict | None:
    """读磁盘上的 index.json；损坏或缺失都当"没有"，下次全量重建。"""
    if not path.exists():
        return None
    try:
        data = load_json(path)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def index_path(vault: Path) -> Path:
    return vault / ".knowrary" / "index.json"
