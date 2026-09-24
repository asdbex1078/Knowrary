"""导入方案 → 变更集 + 待审边（第二步"导入产物三分"，2026-09-18）。

一篇笔记经 LLM 拆出来的方案 JSON（见 prompts/article.md），落盘时分成三种产物：

1. **新建节点**：`create_node` + 节点自己的 `add_edge`。
2. **补充老节点**（`enrich`，旧名 `merge_into`）：`append_body` 追加到老节点正文末尾，
   开头一行引言标来源和日期。**不碰老正文一个字**——模型看到的原文随时可能是截断过的，
   整段替换会把以前记的东西抹掉；追加从根上免掉这个风险。
3. **待审边**：模型对连到**已有节点**的边给 0～1 的置信度，低于 `CONFIDENCE_DIRECT` 的不进 md，
   只记 `.knowrary/pending.json`，审核通过再写回。同一篇拆出的新节点之间的边默认直接写——
   那是模型刚刚亲手拆出来的结构，它最清楚。

**来源**（2026-09-23 起）：节点记 `sources`。保留了原文（`ImportTarget.article`）就链到原文，
新节点链到它出自的那一节（`[[articles/文章#小节]]`）；没保留就是一段纯文字的文章名。
补充过的老节点也把这篇追加进它的 sources——它现在确实有一部分内容出自这篇。

前两种都是普通的 ChangeSet 变更，走 `/api/changes` 那条唯一的 Markdown 写回通道
（dry-run 出 diff、落盘前备份），CLI 的 `apply` 和服务端的 `/api/import` 共用这一份翻译，
skill 和网页不会各长出一套写法。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .articles import ArticlePlan
from .mdio import RE_ID_OK, RE_LINK, RE_REL_HEADER
from .relations import RelationTypes

CONFIDENCE_DIRECT = 0.8          # 连到已有节点的边：置信度够这个数才直接进 md
ENRICH_MARK = "补充自"           # 追加段开头的引言标记，复习时一眼看出哪段是后补的
STUB_DIR = "nodes/_stubs"


@dataclass
class Translation:
    """翻译结果：`changes` 直接喂 `core.plan`；`pending` 交给 `core.add_pending`。"""

    changes: list[dict] = field(default_factory=list)
    pending: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    new_ids: list[str] = field(default_factory=list)
    stub_ids: list[str] = field(default_factory=list)
    enriched: list[str] = field(default_factory=list)
    summary: str = ""

    def counts(self) -> dict[str, int]:
        return {"nodes": len(self.new_ids), "stubs": len(self.stub_ids), "enrich": len(self.enriched),
                "edges": sum(1 for c in self.changes if c["type"] == "add_edge"), "pending": len(self.pending)}


@dataclass
class ImportTarget:
    """一次导入的落点：算哪个领域、放哪个子目录、来源标记。

    `keep_field` 给**跨库复制**用：抄来的节点统一落在一个收件箱目录（`folder`），
    但 frontmatter 里的 `field` 保留它在源库的领域——目录只是收纳，`field` 决定它
    在图上归到哪一组。压平成同一个领域的话，抄 8 个点回来会挤成一坨，
    以后往各领域搬文件时还得重新判断每个点属于哪儿。
    """

    field_name: str
    source: str
    folder: str | None = None
    today: str | None = None
    keep_field: bool = False
    article: ArticlePlan | None = None     # 保留了原文就有：节点的 sources 链到它

    def sources_for(self, section: str | None = None) -> list[str]:
        """一个节点该写的 sources：链到原文（能对上小节就链到那一节），没原文就是文章名。"""
        return [self.article.link_to(section)] if self.article else [self.source]

    @property
    def node_dir(self) -> str:
        return f"nodes/{self.folder or self.field_name}"

    @property
    def date(self) -> str:
        return self.today or dt.date.today().isoformat()


def translate(plan: dict, index: dict, rt: RelationTypes, target: ImportTarget) -> Translation:
    """把方案 JSON 翻成变更集。只做校验和翻译，不读不写 vault。"""
    out = Translation(summary=str(plan.get("summary") or ""))
    existing = {n["id"]: n for n in index["nodes"] if not n.get("virtual")}
    nodes = _fresh_nodes(plan, existing, out)
    stubs = _collect_stubs(plan, nodes, existing, out)
    legal = set(existing) | {n["id"] for n in nodes} | {s["id"] for s in stubs}

    for n in nodes:
        out.changes.append(_create_node(n, target, out))
        out.new_ids.append(n["id"])
        _translate_edges(n, legal, existing, rt, out)
    for s in stubs:
        out.changes.append(_create_stub(s, target))
        out.stub_ids.append(s["id"])
    for e in plan.get("enrich") or plan.get("merge_into") or []:
        change = _enrich(e, existing, target, out)
        if change is not None:
            out.changes.append(change)
            out.enriched.append(change["source"])
            more = _enrich_sources(existing[change["source"]], target)
            if more is not None:
                out.changes.append(more)
    for t in plan.get("proposed_types") or []:
        out.warnings.append(f"提议新类型 `{t.get('type')}`（{t.get('family')}）：{t.get('why', '')}")
    return out


def _fresh_nodes(plan: dict, existing: dict, out: Translation) -> list[dict]:
    """真正要新建的节点：id 合法、还不存在。已存在的不覆盖，提醒改成 enrich。"""
    kept: list[dict] = []
    seen: set[str] = set()
    for n in plan.get("nodes") or []:
        nid = str(n.get("id") or "").strip()
        if not nid or not RE_ID_OK.match(nid):
            out.warnings.append(f"跳过非法 id：{nid!r}")
        elif nid in existing:
            out.warnings.append(f"`{nid}` 已存在，未覆盖；要补内容请放进 enrich")
        elif nid in seen:
            out.warnings.append(f"方案里 `{nid}` 出现了两次，只取第一个")
        else:
            seen.add(nid)
            kept.append({**n, "id": nid})
    return kept


def _collect_stubs(plan: dict, nodes: list[dict], existing: dict, out: Translation) -> list[dict]:
    """方案给的 stubs，加上正文里链到不存在 id 的那些（自动补壳，正文里不许悬空链接）。"""
    stubs: list[dict] = []
    legal = set(existing) | {n["id"] for n in nodes}
    for s in plan.get("stubs") or []:
        sid = str(s.get("id") or "").strip()
        if not sid or not RE_ID_OK.match(sid) or sid in legal:
            continue
        legal.add(sid)
        stubs.append({**s, "id": sid})
    for n in nodes:
        for link in sorted(set(RE_LINK.findall(n.get("body") or ""))):
            if link in legal or not RE_ID_OK.match(link):
                continue
            legal.add(link)
            stubs.append({"id": link, "name": link, "desc": "待补充", "why": f"正文 [[{link}]] 引用但不存在"})
            out.warnings.append(f"正文链到不存在的 `{link}`，补成 stub")
    return stubs


def _create_node(n: dict, target: ImportTarget, out: Translation) -> dict:
    field_name = (n.get("field") if target.keep_field else None) or target.field_name
    fields = {"name": n.get("name") or n["id"], "field": field_name,
              "desc": n.get("desc") or "待补充", "learned": target.date,
              "sources": target.sources_for(n.get("from_section"))}
    for k in ("type", "year", "aliases", "tags", "layer"):
        if n.get(k):
            fields[k] = n[k]
    return {"type": "create_node", "source": n["id"], "path": f"{target.node_dir}/{n['id']}.md",
            "fields": fields, "body": _body_without_relations(n, out)}


def _body_without_relations(n: dict, out: Translation) -> str:
    """正文里不许带 `## 关系`（写回通道会整批拒掉）：模型偶尔照着规范把关系段也写进 body，
    这里截掉并提醒，边一律从 relations 来。"""
    body = (n.get("body") or "").strip()
    m = RE_REL_HEADER.search(body)
    if m is None:
        return body
    out.warnings.append(f"`{n['id']}` 的正文里带了 `## 关系` 段，已截掉（边只从 relations 取）")
    return body[:m.start()].rstrip()


def _create_stub(s: dict, target: ImportTarget) -> dict:
    field_name = (s.get("field") if target.keep_field else None) or target.field_name
    fields = {"name": s.get("name") or s["id"], "field": field_name, "status": "stub",
              "desc": s.get("desc") or "待补充", "sources": target.sources_for()}
    return {"type": "create_node", "source": s["id"], "path": f"{STUB_DIR}/{s['id']}.md",
            "fields": fields, "body": f"> 空壳节点（stub）：{s.get('why', '')}"}


def _translate_edges(n: dict, legal: set[str], existing: dict, rt: RelationTypes, out: Translation) -> None:
    """一个新节点的边：目标要存在、类型要登记；连到老节点且置信度不够的进待审。"""
    seen: set[tuple[str, str]] = set()
    for r in n.get("relations") or []:
        rel, tgt = str(r.get("type") or "").strip(), str(r.get("target") or "").strip()
        if not rel or not tgt or tgt == n["id"] or tgt not in legal:
            out.warnings.append(f"丢弃边 {n['id']} {rel} → {tgt}（目标不存在或指向自己）")
            continue
        if (rel, tgt) in seen:
            continue
        seen.add((rel, tgt))
        if not rt.known(rel):
            out.warnings.append(f"未登记类型 {n['id']} {rel} → {tgt}（照写，check 会警告）")
        conf = _confidence(r)
        edge = {"source": n["id"], "relation": rel, "target": tgt, "year": r.get("year"),
                "note": (r.get("note") or "").strip() or None, "confidence": conf}
        if tgt in existing and conf < CONFIDENCE_DIRECT:
            out.pending.append(edge)
        else:
            out.changes.append({"type": "add_edge", **{k: v for k, v in edge.items() if v is not None}})


def _confidence(r: dict) -> float:
    """模型没给就当它确定（旧方案 JSON 没这个字段，不能因此全进待审）；给了非法值当 0。"""
    raw = r.get("confidence")
    if raw is None:
        return 1.0
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.0


def _enrich(e: dict, existing: dict, target: ImportTarget, out: Translation) -> dict | None:
    """补充老节点：只追加、带来源引言。目标不存在或内容为空就不写。"""
    nid = str(e.get("existing") or "").strip()
    content = str(e.get("content") or "").strip()
    if nid not in existing:
        out.warnings.append(f"enrich 的目标 `{nid}` 不存在，跳过")
        return None
    if not content:
        out.warnings.append(f"enrich `{nid}` 没给内容，跳过")
        return None
    if RE_REL_HEADER.search(content):
        out.warnings.append(f"enrich `{nid}` 的内容里带 `## 关系`，关系请放 relations，这段跳过")
        return None
    why = str(e.get("why") or "").strip()
    head = f"> {ENRICH_MARK}《{target.source}》（{target.date}）" + (f"：{why}" if why else "")
    return {"type": "append_body", "source": nid, "body": f"{head}\n\n{content}"}


def _enrich_sources(node: dict, target: ImportTarget) -> dict | None:
    """补充过的老节点：把这篇追加进它的 sources（原有的保留在前面）。已经有了就不动。

    `sources` 写的是完整列表（写回层会顺手撕掉旧的单值 source），所以这里把索引里
    已经并过一遍的那份原样带上再追加。"""
    have = list(node.get("sources") or [])
    add = target.sources_for()[0]
    if add in have:
        return None
    return {"type": "update_frontmatter", "source": node["id"], "fields": {"sources": [*have, add]}}


# ---------------------------------------------------------------- 方案改写（审核卡上的两个动作）

def rename_in_plan(plan: dict, old: str, new: str) -> dict:
    """把方案里一个新节点的 id 换掉：节点本身、所有 relations 的 target、正文里的 [[链接]]、stubs。

    用在"认领幽灵"上——模型拆出的 id 叫 `RNN`，清单里那个点叫 `RNN与长程依赖`，
    人点一下"改用清单里的 id"，整份方案里指向它的地方都要跟着改，不能只改一处留下悬空链接。
    返回新方案，不改入参。
    """
    if not old or not new or old == new:
        return plan
    import copy
    out = copy.deepcopy(plan)
    link_old, link_new = f"[[{old}]]", f"[[{new}]]"
    for n in out.get("nodes") or []:
        if n.get("id") == old:
            n["id"] = new
            if not n.get("name") or n["name"] == old:
                n["name"] = new
        if n.get("body"):
            n["body"] = n["body"].replace(link_old, link_new).replace(f"[[{old}|", f"[[{new}|")
        for r in n.get("relations") or []:
            if r.get("target") == old:
                r["target"] = new
    out["stubs"] = [s for s in out.get("stubs") or [] if s.get("id") != new]
    for s in out["stubs"]:
        if s.get("id") == old:
            s["id"] = new
    for e in out.get("enrich") or out.get("merge_into") or []:
        if e.get("content"):
            e["content"] = e["content"].replace(link_old, link_new)
    return out


def promote_in_plan(plan: dict, keys: list[str]) -> dict:
    """把指定的几条关系置信度抬到 1.0，让它们直接进 md 而不是待审。
    key 用 `源->目标#类型`（和 pending.json / index 里边的 id 同一写法）。"""
    if not keys:
        return plan
    import copy
    wanted = set(keys)
    out = copy.deepcopy(plan)
    for n in out.get("nodes") or []:
        for r in n.get("relations") or []:
            if f"{n.get('id')}->{r.get('target')}#{r.get('type')}" in wanted:
                r["confidence"] = 1.0
    return out
