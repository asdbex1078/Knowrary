"""对话续接缓存（复盘 §11.2）：服务端记住上一轮结束时那份**完整**内部消息列表。

**为什么要有这一份**：前端每轮回传的是可见轮次（`strip_tools` 之后的 assistant + 我的话），
而服务端内部那份还夹着工具往返（assistant 原文含工具块、`[工具 X 的结果]` 那几条）。
两份对不上，`llm_backend` 靠指纹续 `claude -p` 会话的招就**只要上一轮调过工具就必然失效**——
使用记录里 67 段教练会话有 54 段只有一次往返，等于几乎每次都整段重发、全额重写缓存。

**它是缓存，不是真值。** 对话的真值是 `.knowrary/chat/<项目>/YYYY-MM.jsonl` 留档。
这份文件随时可以删，丢了只是下一轮多花一次全量重发的钱。所以：
- 不做 schema 迁移、不做向后兼容，形状对不上就整份丢掉重来；
- 前缀对不上就退回整段重建，**绝不猜**——续错一段（模型看着别人的上下文答题）
  比多花那点钱糟得多。

**为什么不是数据库。** 量级：单段对话总文本中位 11 KB、最大 32 KB，留最近 8 段约 0.2 MB，
和 `index.json`（87 KB）同级。访问形状是单进程、一次只读一个 key、每轮整文件替换——
数据库在这里只能卖给我们并发锁和索引，两样都用不上。真要上库的触发线有三条，
**到了任意一条再说，别凭感觉升级**：
① 多进程 / 多端同时写；② 需要跨对话检索（现在这活是 `rg` 干的）；③ 它不再可丢。
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from .paths import core

log = logging.getLogger(__name__)

SCHEMA = 1
MAX_KEYS = 8          # 最多留几段。留更多只是留着更老的对话，而老对话本来也续不上
MAX_CHARS = 200_000   # 单段上限。超了就不缓存这一段：省钱不值得拿内存和磁盘去换


def store_path(vault: Path) -> Path:
    return vault / ".knowrary" / "turns.json"


def _empty() -> dict:
    return {"schema_version": SCHEMA, "updated_at": "", "keys": {}}


def _load(vault: Path) -> dict:
    """读缓存。**任何异常都当成没有缓存**——它是可丢的，绝不能因为它让对话起不来。"""
    path = store_path(vault)
    if not path.exists():
        return _empty()            # 冷启动的正常状态，不是问题，别刷日志
    try:
        doc = core.load_json(path)
    except (OSError, ValueError) as exc:
        log.warning("续接缓存读不出来，当没有：%s", exc)
        return _empty()
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA:
        return _empty()
    if not isinstance(doc.get("keys"), dict):
        return _empty()
    return doc


def _save(vault: Path, doc: dict) -> None:
    """写缓存。**记不上绝不能拖垮正经对话**（同用量账本、出错流水那条纪律）。"""
    doc["schema_version"] = SCHEMA
    doc["updated_at"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        core.write_json_atomic(store_path(vault), doc)
    except OSError as exc:
        log.warning("续接缓存没写上：%s", exc)


def _chars(messages: list[dict]) -> int:
    return sum(len(m.get("content") or "") for m in messages)


def resume(vault: Path, key: str, incoming: list[dict]) -> list[dict] | None:
    """能不能接着上一轮往下发。

    `incoming` 是前端这一轮发来的可见轮次，最后一条是我刚说的话。
    比对的是**上一轮存下来的可见投影**和 `incoming[:-1]`——不去反推"哪条 user 是工具结果"，
    投影在存的时候就一起记下来了，比事后按前缀猜可靠。

    返回上一轮结束时的完整消息列表（**不含**这一轮的新话）；对不上就 None。
    """
    row = (_load(vault).get("keys") or {}).get(key)
    if not isinstance(row, dict):
        return None
    if row.get("visible") != incoming[:-1]:
        return None                      # 改过 / 删过 / 换了一段：老老实实重建
    messages = row.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    return messages


def remember(vault: Path, key: str, visible: list[dict], messages: list[dict]) -> None:
    """记下这一轮结束时的两份列表：给人看的（校验用）和给模型看的（续接用）。"""
    if not key or not messages or _chars(messages) > MAX_CHARS:
        return
    doc = _load(vault)
    keys = doc["keys"]
    keys[key] = {"at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "visible": visible, "messages": messages}
    if len(keys) > MAX_KEYS:             # 先进先出，别无限涨
        for old in sorted(keys, key=lambda k: keys[k].get("at") or "")[:len(keys) - MAX_KEYS]:
            keys.pop(old, None)
    _save(vault, doc)


def forget(vault: Path, key: str) -> None:
    """扔掉一段：下一轮从头重建。"""
    doc = _load(vault)
    if doc["keys"].pop(key, None) is not None:
        _save(vault, doc)
