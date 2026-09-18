"""文章 → 拆解提示词：挑相关节点、拼模板。CLI（`knowrary article / context`）与服务端
（`POST /api/import/propose`）共用这一份，两边给模型看的东西才一致。

输入统一成"节点卡"（轻量 dict），不绑 parser.Node 也不绑 index 的形状：
CLI 从 load_vault 的 Node 造，服务端从 index.json 造，各自一行转换。
"""
from __future__ import annotations

import re
from pathlib import Path

from .mdio import read
from .relations import RelationTypes

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
RELATED_LIMIT = 60
# 提示词里"可链 id"一栏的上限。以前是全部 id：88 个节点时无感，到上千个节点就是每次导入
# 白送几千 token，而且模型链到的绝大多数都在同领域或相关节点的一跳之内。
LINKABLE_LIMIT = 200
RE_TOKEN = re.compile(r"[a-z0-9_+#.-]+|[一-鿿]{2,4}")


def cards_from_index(index: dict) -> list[dict]:
    """index.json → 节点卡。虚拟 stub（没有文件）不算：模型链到它们等于链到不存在的东西。"""
    out_edges: dict[str, list[tuple[str, str]]] = {}
    for e in index.get("edges") or []:
        out_edges.setdefault(e["source"], []).append((e["type"], e["target"]))
    return [{"id": n["id"], "name": n.get("name") or n["id"], "aliases": n.get("aliases") or [],
             "tags": n.get("tags") or [], "desc": n.get("desc") or "", "field": n.get("field") or "",
             "edges": out_edges.get(n["id"], [])}
            for n in index.get("nodes") or [] if not n.get("virtual")]


def cards_from_nodes(nodes: dict) -> list[dict]:
    """parser.load_vault 的 {id: Node} → 节点卡（CLI 用）。"""
    return [{"id": n.id, "name": str(n.fm.get("name") or n.id), "aliases": n.fm.get("aliases") or [],
             "tags": n.fm.get("tags") or [], "desc": str(n.fm.get("desc") or ""),
             "field": str(n.fm.get("field") or ""), "edges": [(e.type, e.target) for e in n.edges]}
            for n in nodes.values()]


def select_related(cards: list[dict], text: str, limit: int = RELATED_LIMIT) -> list[dict]:
    """和文章最相关的已有节点：名字 / 别名 / 标签命中算 3 倍，desc 命中算 1 倍，至少 6 分。"""
    toks = {t for t in RE_TOKEN.findall(text.lower()) if len(t) >= 2}
    scored = []
    for c in cards:
        names = " ".join([c["id"], c["name"], *c["aliases"], *c["tags"]]).lower()
        desc = c["desc"].lower()
        s = sum(3 * len(t) for t in toks if t in names) + sum(len(t) for t in toks if t in desc)
        if s >= 6:
            scored.append((s, c))
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    return [c for _, c in scored[:limit]]


def select_linkable(cards: list[dict], related: list[dict], field_name: str,
                    limit: int = LINKABLE_LIMIT) -> list[str]:
    """提示词里允许模型链接的 id 子集：相关节点 → 它们一跳的邻居 → 导入目标同领域的节点，
    按这个优先级填到 limit 为止。不发全量 id：那是唯一一块随图谱线性长、没有上限的 prompt。"""
    known = {c["id"] for c in cards}
    picked: list[str] = []
    seen: set[str] = set()

    def take(ids) -> None:
        for i in ids:
            if len(picked) >= limit:
                return
            if i in known and i not in seen:
                seen.add(i)
                picked.append(i)

    take(c["id"] for c in related)
    take(t for c in related for _, t in c["edges"])
    take(sorted(c["id"] for c in cards if field_name and c.get("field") == field_name))
    return sorted(picked)


def describe_related(cards: list[dict]) -> list[str]:
    return [f"- {c['id']}｜{c['desc']}｜边: " + ("; ".join(f"{t}→{tgt}" for t, tgt in c["edges"][:8]) or "(无)")
            for c in cards]


def describe_points(points: list[dict]) -> str:
    """当前项目里还没建的点，给模型当"待认领"清单。"""
    lines = [f"- {p['id']}｜{p.get('name') or p['id']}｜{p.get('why') or ''}".rstrip("｜") for p in points]
    return "\n".join(lines) or "(无)"


def build_article_prompt(cards: list[dict], rt: RelationTypes, article: str, field_name: str,
                         project_points: list[dict] | None = None) -> str:
    tpl = read(PROMPTS_DIR / "article.md")
    related = select_related(cards, article)
    linkable = select_linkable(cards, related, field_name)
    return (tpl.replace("{{relation_types}}", rt.describe())
            .replace("{{field}}", field_name)
            .replace("{{total_nodes}}", str(len(cards)))
            .replace("{{linkable_count}}", str(len(linkable)))
            .replace("{{all_ids}}", "、".join(linkable) or "(空)")
            .replace("{{related_nodes}}", "\n".join(describe_related(related)) or "(无)")
            .replace("{{project_points}}", describe_points(project_points or []))
            .replace("{{article}}", article))
