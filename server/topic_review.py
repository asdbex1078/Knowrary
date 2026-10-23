"""专题图的 AI 参与（2026-10-13 第 6 步）：审这张图（review 角色）、问该画什么（learn 角色）。

都是他点了才调；结果存进专题文件夹的 review.json。画布和索引从这里读，判断与存储在 core.topic_review。
"""
from __future__ import annotations

from pathlib import Path

from .index_service import current_index
from .layout_store import read_layout
from .llm_call import ask_meta
from .paths import core


class TopicReviewRejected(Exception):
    """专题不存在、画布是空的、或模型交回的东西一条都用不了。"""


def _canvas(vault: Path, topic: str) -> tuple[dict, dict]:
    doc = read_layout(vault, core.TOPIC_PREFIX + topic)
    if doc is None:
        raise TopicReviewRejected(f"专题「{topic}」还没有画布")
    return doc.model_dump(), current_index(vault)


def _center(index: dict, topic: str) -> dict | None:
    return next((n for n in index["nodes"] if n.get("topic") == topic), None)


def review(vault: Path, topic: str) -> dict:
    """审这张图：意见挂在具体的框 / 线上，每条带一个能一键采纳的改法。"""
    layout, index = _canvas(vault, topic)
    if not layout.get("shapes") and not layout.get("nodes"):
        raise TopicReviewRejected("画布上还什么都没有，先画几个框再审")
    prompt = core.topic_review.build_review_prompt(topic, layout, index, _center(index, topic))
    raw, meta = ask_meta(vault, "review", prompt, op="topic-review")
    data = core.parse_json(raw, "topic-review", vault=vault)
    if not isinstance(data, dict) or not data:
        # 解析不出来不能当「没意见」摆出去：那等于告诉他这张图没问题（原文已记进 issues）
        raise TopicReviewRejected(f"模型交回的不是能用的 JSON（{meta['model']}，{meta['ms'] / 1000:.1f}s），再审一次试试")
    items, dropped = core.topic_review.check_items(data.get("items") or [], layout, index)
    if dropped:
        core.record_issue(vault, "topic-review", "；".join(dropped)[:800], where=f"/api/topics/{topic}/review")
    doc = core.topic_review.load_review(vault, topic)
    doc["review"] = core.topic_review.stamp_review(items, str(data.get("summary") or "").strip(), meta, len(dropped))
    core.topic_review.save_review(vault, topic, doc)
    return doc["review"]


def advise(vault: Path, topic: str, question: str) -> dict:
    """问该画什么：只给文字建议。参考库里按关系能连到的点，建议优先用它们（画了就能直接升级绑定）。"""
    layout, index = _canvas(vault, topic)
    center = _center(index, topic)
    draft = core.topic_map(index, topic, layout.get("depth") or {}) if center else None
    prompt = core.topic_review.build_advice_prompt(topic, layout, index,
                                                   core.topic_review.related_view(draft, index), question)
    text, meta = ask_meta(vault, "learn", prompt, op="topic-advice")
    if not text.strip():
        raise TopicReviewRejected(f"模型什么都没说（{meta['model']}，{meta['ms'] / 1000:.1f}s）")
    doc = core.topic_review.load_review(vault, topic)
    entry = core.topic_review.push_advice(doc, question, text, meta)
    core.topic_review.save_review(vault, topic, doc)
    return entry


def mark(vault: Path, topic: str, item_id: str, status: str) -> dict:
    doc = core.topic_review.load_review(vault, topic)
    try:
        item = core.topic_review.mark(doc, item_id, status)
    except (KeyError, ValueError) as exc:
        raise TopicReviewRejected(f"没有这条意见，或状态不对：{exc}") from exc
    core.topic_review.save_review(vault, topic, doc)
    return item
