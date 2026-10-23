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

# 拆分粒度（2026-10-13）：拆粗拆细由人在拆之前选，不再写死「3～10 个」。
# 起因是 ELF 一篇拆了 8 个点，「数据库 C 扩展加载」这种应用场景也单独成了点——
# 所以三档都说清楚**应用场景、工具清单、案例怎么处理**，不只给一个数字。
GRAINS = {
    "粗": ("**粗**：只拆 2～4 个节点。只有会被别的知识反复引用、值得单独复习的核心概念才单独成点；"
          "应用场景（某个系统 / 产品怎么用它）、工具与命令清单、具体案例、历史细节一律写进相关节点的 `## 例子` 或 `## 核心内容`，"
          "不单独成点。拿不准拆不拆的就不拆。"),
    "中": ("**中**：一篇几千字的文章通常拆出 3～8 个节点，太碎会让图变毛线球。"
          "应用场景、工具清单、具体案例默认写进它所属概念的 `## 例子`，只有它本身也是一个能独立复习的概念才单独成点。"),
    "细": ("**细**：按机制拆，每个能独立讲清、独立被考的子概念 / 机制都单独成点，可以拆到 8～15 个；"
          "但仍然不是一节一个点——没有独立价值的段落（过渡、背景、工具清单）并进它所属的那个点。"),
}
DEFAULT_GRAIN = "中"


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
                         project_points: list[dict] | None = None, grain: str | None = None) -> str:
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
            .replace("{{grain}}", GRAINS.get(grain or "", GRAINS[DEFAULT_GRAIN]))
            .replace("{{article}}", article))


# ---------------------------------------------------------------- 拆后调整：合并 / 再拆（2026-10-13）

RESHAPE_OPS = ("merge", "split")


def _node_view(n: dict) -> dict:
    """给模型看的一个点：全量（要改写的那几个）。"""
    keep = ("id", "name", "type", "year", "desc", "from_section", "tags", "aliases", "body", "relations")
    return {k: n[k] for k in keep if n.get(k) not in (None, "", [])}


def build_reshape_prompt(plan: dict, op: str, ids: list[str], article: str, rt: RelationTypes,
                         hint: str = "") -> str:
    """审核卡上勾了几个点「合并」、或对一个点「再拆」：只让模型重写动到的那几个，别的点原样留着。"""
    picked = [n for n in plan.get("nodes") or [] if n.get("id") in ids]
    others = [n for n in plan.get("nodes") or [] if n.get("id") not in ids]
    tpl = read(PROMPTS_DIR / "reshape.md")
    task = {"merge": f"把下面这 {len(picked)} 个点**合并成一个**节点。", "split": "把下面这 1 个点**再拆成 2 个或更多**节点。"}[op]
    import json
    return (tpl.replace("{{task}}", task)
            .replace("{{op}}", op)
            .replace("{{hint}}", hint.strip() or "（没有额外要求）")
            .replace("{{relation_types}}", rt.describe())
            .replace("{{picked}}", json.dumps([_node_view(n) for n in picked], ensure_ascii=False, indent=2))
            .replace("{{others}}", "\n".join(f"- {n.get('id')}｜{n.get('desc') or ''}" for n in others) or "(无)")
            .replace("{{article}}", article))
