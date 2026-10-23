"""专题图的 AI 参与（2026-10-13 第 6 步）：「审这张图」和「问 AI 该画什么」。

两件事都是**他点了才问**：
- 审图走 review 角色。模型看的是画布本身——框套框的结构、框上的字和悬停说明、连线、升级过的框对应的知识点摘要，
  意见分三类：事实错误（fact）、缺了什么（missing）、建议升级 / 连线（upgrade / link）。每条意见带一个
  **能一键采纳的改法**，挂在具体的框 / 线上；指不到画布上真东西的意见直接丢掉（并记一笔），不摆给人看。
- 问该画什么走 learn 角色，只给文字建议，不替他画。

结果存在专题文件夹里（`.knowrary/topics/<专题>/review.json`），刷新还在、采纳 / 忽略的状态也在。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .layout import topic_dir
from .mdio import load_json, read, write_json_atomic
from .article import PROMPTS_DIR

KINDS = ("fact", "missing", "upgrade", "link")
FACT_FIELDS = ("text", "tip")
SHAPE_KINDS = ("rect", "frame", "text", "ellipse", "triangle", "diamond", "cylinder")
STATUSES = ("open", "adopted", "ignored")
ADVICE_KEEP = 10


def review_path(vault: Path, topic: str) -> Path:
    return topic_dir(vault, topic) / "review.json"


def load_review(vault: Path, topic: str) -> dict:
    path = review_path(vault, topic)
    try:
        return load_json(path) if path.exists() else {}
    except (ValueError, OSError):
        return {}


def save_review(vault: Path, topic: str, doc: dict) -> None:
    write_json_atomic(review_path(vault, topic), doc)


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 画布 → 给模型看的文字

def canvas_view(layout: dict, index: dict) -> str:
    """把画布写成模型读得懂的样子：框按嵌套缩进、同一层从左到右；每个框带 id，意见要指回它。"""
    by_id = {n["id"]: n for n in index.get("nodes") or []}
    shapes = layout.get("shapes") or {}
    lines = ["## 手画的框（缩进 = 套在上一层大框里；同一层从左到右）"]
    lines += _shape_tree(shapes, by_id, None, 0) or ["（还没画框）"]
    cards = sorted(layout.get("nodes") or {})
    lines += ["", "## 直接放上来的知识点卡片"]
    lines += [f"- 〔{nid}〕{_meta_line(by_id.get(nid))}" for nid in cards] or ["（没有）"]
    lines += ["", "## 连线（只存在画布上的，除非标了「已写进 md」）"]
    lines += [_link_line(lid, l, shapes, by_id) for lid, l in (layout.get("links") or {}).items()] or ["（没有）"]
    return "\n".join(lines)


def _shape_tree(shapes: dict, by_id: dict, parent: str | None, depth: int) -> list[str]:
    kids = sorted(((sid, s) for sid, s in shapes.items() if s.get("parent") == parent),
                  key=lambda kv: (kv[1].get("y", 0) // 200, kv[1].get("x", 0)))
    out: list[str] = []
    for sid, s in kids:
        bound = f"；已升级为知识点〔{s['node']}〕{_meta_line(by_id.get(s['node']))}" if s.get("node") else ""
        tip = f"；悬停说明：{s['tip']}" if s.get("tip") else ""
        out.append(f"{'  ' * depth}- [{sid}] {s.get('kind')}「{s.get('text') or ''}」{tip}{bound}")
        out += _shape_tree(shapes, by_id, sid, depth + 1)
    return out


def _meta_line(meta: dict | None) -> str:
    if not meta:
        return "（索引里没有这个点）"
    layer = f"，{meta['layer']}" if meta.get("layer") else ""
    return f"{meta.get('name') or meta['id']}：{meta.get('desc') or ''}{layer}"


def _end_name(end: dict, shapes: dict) -> str:
    if end.get("kind") == "shape":
        s = shapes.get(end.get("id")) or {}
        return f"[{end.get('id')}]「{s.get('text') or s.get('node') or ''}」"
    return f"〔{end.get('id')}〕"


def _link_line(lid: str, link: dict, shapes: dict, by_id: dict) -> str:
    edge = f"；已写进 md：{link['edge']}" if link.get("edge") else ""
    text = f"「{link['text']}」" if link.get("text") else ""
    return f"- [{lid}] {_end_name(link.get('source') or {}, shapes)} → {_end_name(link.get('target') or {}, shapes)}{text}{edge}"


# ---------------------------------------------------------------- 提示词

def build_review_prompt(topic: str, layout: dict, index: dict, center: dict | None) -> str:
    tpl = read(PROMPTS_DIR / "topic_review.md")
    return (tpl.replace("{{topic}}", topic)
            .replace("{{center}}", _meta_line(center) if center else "（还没有中心点）")
            .replace("{{canvas}}", canvas_view(layout, index)))


def build_advice_prompt(topic: str, layout: dict, index: dict, related: str, question: str) -> str:
    tpl = read(PROMPTS_DIR / "topic_advice.md")
    return (tpl.replace("{{topic}}", topic)
            .replace("{{question}}", question.strip() or "（没有具体问题：给一版这张图该怎么画的建议）")
            .replace("{{related}}", related or "（这个专题还没有中心点，库里的相关点算不出来）")
            .replace("{{canvas}}", canvas_view(layout, index)))


def related_view(draft: dict | None, index: dict) -> str:
    """库里按关系算出的相关点（core.topic_map 的结果），给「该画什么」做参考——建议优先用库里已有的点。"""
    if not draft:
        return ""
    by_id = {n["id"]: n for n in index.get("nodes") or []}
    names = {"parts": "部件", "up": "上（谁在用它）", "down": "下（它跑在什么上）", "left": "左（前身）",
             "right": "右（后继）", "compare": "对照", "pending": "其他相关"}
    out = [f"- 中心〔{draft['center']}〕{_meta_line(by_id.get(draft['center']))}"]
    for key, label in names.items():
        items = draft.get(key) or []
        ids = items if key == "parts" else [it["id"] for it in items]
        if ids:
            out.append(f"- {label}：" + "、".join(f"〔{i}〕{(by_id.get(i) or {}).get('name') or i}" for i in ids))
    return "\n".join(out)


# ---------------------------------------------------------------- 校验模型交回的意见

def check_items(raw: list, layout: dict, index: dict) -> tuple[list[dict], list[str]]:
    """只留下指得到画布上真东西、改法完整的意见；丢掉的说明原因（记进 issues，不摆给人）。"""
    ok, dropped = [], []
    for i, item in enumerate(raw or []):
        if not isinstance(item, dict):
            dropped.append(f"#{i} 不是对象")
            continue
        why = _invalid(item, layout, index)
        if why:
            dropped.append(f"#{i} {why}")
            continue
        ok.append({**_clean(item), "id": f"r{len(ok) + 1}", "status": "open"})
    return ok, dropped


def _invalid(item: dict, layout: dict, index: dict) -> str:
    kind = item.get("kind")
    if kind not in KINDS:
        return f"kind `{kind}` 不认识"
    if not str(item.get("say") or "").strip():
        return "没写意见"
    shapes = layout.get("shapes") or {}
    nodes = {n["id"] for n in index.get("nodes") or [] if not n.get("virtual")}
    fix = item.get("fix") or {}
    if kind == "fact":
        if item.get("target") not in shapes or fix.get("field") not in FACT_FIELDS or not isinstance(fix.get("value"), str):
            return "事实错误要指到一个框，改法是 {field: text|tip, value}"
    elif kind == "missing":
        if fix.get("shape") not in SHAPE_KINDS or not str(fix.get("text") or "").strip():
            return "缺了什么要给 {shape, text}"
        if item.get("target") and item["target"] not in shapes:
            return f"放进的大框 `{item['target']}` 不在画布上"
    elif kind == "upgrade":
        if item.get("target") not in shapes or shapes[item["target"]].get("node"):
            return "建议升级要指到一个还没升级的框"
        if fix.get("node") and fix["node"] not in nodes:
            return f"建议绑定的 `{fix['node']}` 库里没有"
    elif kind == "link":
        for end in (fix.get("from"), fix.get("to")):
            if not isinstance(end, dict) or not _end_ok(end, layout):
                return "连线两端要是画布上的框或卡片"
    return ""


def _end_ok(end: dict, layout: dict) -> bool:
    pool = layout.get("shapes") if end.get("kind") == "shape" else layout.get("nodes")
    return end.get("kind") in ("shape", "node") and end.get("id") in (pool or {})


def _clean(item: dict) -> dict:
    out = {"kind": item["kind"], "say": str(item["say"]).strip(), "fix": item.get("fix") or {}}
    if item.get("target"):
        out["target"] = item["target"]
    if item.get("why"):
        out["why"] = str(item["why"]).strip()
    return out


def mark(doc: dict, item_id: str, status: str) -> dict:
    """采纳 / 忽略一条意见（采纳的动作由前端照着 fix 做，这里只记状态）。"""
    if status not in STATUSES:
        raise ValueError(f"状态只能是 {STATUSES}")
    for item in (doc.get("review") or {}).get("items") or []:
        if item.get("id") == item_id:
            item["status"] = status
            item["at"] = _now()
            return item
    raise KeyError(item_id)


def stamp_review(items: list[dict], summary: str, meta: dict, dropped: int) -> dict:
    return {"items": items, "summary": summary, "dropped": dropped, "at": _now(), **meta}


def push_advice(doc: dict, question: str, text: str, meta: dict) -> dict:
    entry = {"question": question.strip(), "text": text.strip(), "at": _now(), **meta}
    doc["advice"] = [entry, *(doc.get("advice") or [])][:ADVICE_KEEP]
    return entry
