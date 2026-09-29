"""「问 AI 该放哪」（2026-09-29，开发实施计划 §10 #3 Curator 说理由）。

邻居投票判不出的那一块（整块都还在 Inbox 里、没有一个邻居在画布上），手动点一下，
review 角色看分组树 + 这批点的内容，给一个分组和理由。**只是建议**：结论存 pending.json 的
`curated`，Inbox 上显示出来，放不放还是人点。分组 id 必须是画布上真有的；模型回了名字就按名字对回 id。
"""
from __future__ import annotations

from pathlib import Path

from .contracts import CurateResult
from .curation import load_pair
from .llm_call import ask_meta, parse_json
from .paths import core

MAX_IDS = 30               # 一次最多问这么多点：一整块通常就几个，再多提示词太长
MEMBERS = 6                # 每个分组列几个代表成员


class CurateRejected(ValueError):
    """请求不成立：点不在 Inbox 里了、画布上一个分组都没有、模型给的分组对不上。"""


def _prompt() -> str:
    path = Path(__file__).resolve().parents[1] / "tools" / "knowrary" / "prompts" / "curate.md"
    return path.read_text(encoding="utf-8")


def curate(vault: Path, ids: list[str]) -> CurateResult:
    index, layout = load_pair(vault)
    plain = layout.model_dump()
    waiting = set(core.inbox_ids(index, plain))
    batch = [i for i in dict.fromkeys(ids) if i in waiting][:MAX_IDS]
    if not batch:
        raise CurateRejected("这些点已经不在 Inbox 里了（可能刚被放上画布），刷新一下")
    groups = plain["groups"]
    if not groups:
        raise CurateRejected("画布上还没有分组，没有地方可放")
    nodes = {n["id"]: n for n in index["nodes"]}
    prompt = (_prompt().replace("{{groups}}", _describe_groups(plain, nodes))
              .replace("{{nodes}}", "\n".join(_describe_node(nodes.get(i) or {"id": i}) for i in batch)))
    raw, meta = ask_meta(vault, "review", prompt, op="curate")
    data = parse_json(raw, "curate", vault=vault) or {}
    gid = _resolve(str(data.get("group") or ""), groups)
    if not gid:
        raise CurateRejected(f"模型给的分组「{data.get('group')}」画布上没有，再问一次试试")
    why = str(data.get("why") or "").strip()
    core.add_curated(vault, batch, {"group": gid, "why": why, **meta})
    return CurateResult(ids=batch, group=gid, group_name=groups[gid].get("name") or gid, why=why, **meta)


def _resolve(raw: str, groups: dict) -> str | None:
    """原样的 id 优先；模型偶尔连方括号抄回来，或者回的是名字（名字唯一才认）。"""
    key = raw.strip().strip("[]").strip()
    if key in groups:
        return key
    named = [g for g, v in groups.items() if (v.get("name") or "").strip() == key]
    return named[0] if len(named) == 1 else None


def _describe_groups(plain: dict, nodes: dict) -> str:
    groups = plain["groups"]
    members: dict[str, list[str]] = {}
    for nid, box in plain["nodes"].items():
        if box.get("group") in groups and nid in nodes:
            members.setdefault(box["group"], []).append(nodes[nid].get("name") or nid)
    lines = []
    for gid, g in groups.items():
        names = sorted(members.get(gid, []))
        parent = groups.get(g.get("parent") or "", {}).get("name")
        line = f"[{gid}] {g.get('name') or gid}" + (f"（上级：{parent}）" if parent else "")
        line += f"：{'、'.join(names[:MEMBERS])}（共 {len(names)} 个）" if names else "：（还空着）"
        lines.append(line)
    return "\n".join(lines)


def _describe_node(node: dict) -> str:
    line = f"- 「{node.get('name') or node['id']}」"
    extra = [f"领域 {node['field']}" if node.get("field") else "", f"层 {node['layer']}" if node.get("layer") else ""]
    extra = [x for x in extra if x]
    if extra:
        line += f"（{'，'.join(extra)}）"
    return line + (f"：{node['desc']}" if node.get("desc") else "")
