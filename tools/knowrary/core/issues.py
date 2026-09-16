"""出错流水：`.knowrary/issues.jsonl`，一行一条。

**为什么值得单独记一份**：现在的错误分散在三处——LLM 调用失败进用量账本、
工具执行失败只在那一轮对话里闪一下、ChangeSet 被拒只弹个 toast。
于是"这东西为什么老出问题"没有任何地方能回答，只能靠人记得住。

和 `llm-usage.json` 同一条纪律：**旁路，不是主线**。记不上绝不能拖垮正经操作。
也和对话留档同一条：追加 JSONL、不建库、不做索引——量级差两个数量级，`rg` 毫秒级。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

KEEP = 500          # 只留最近这么多条：它是线索不是账本，涨到几万条反而没人看


def issues_path(vault: Path) -> Path:
    return vault / ".knowrary" / "issues.jsonl"


def record(vault: Path, kind: str, message: str, where: str = "", detail: dict | None = None) -> None:
    """记一条。`kind` 粗分几类：llm / tool / write / index。"""
    row = {"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "kind": kind, "where": where, "message": str(message)[:500], **(detail or {})}
    path = issues_path(vault)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return                                  # 记不上就算了，绝不拖垮正经操作
    _trim(path)


def _trim(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > KEEP * 2:               # 攒够一倍才截一次，别每条都重写整个文件
            path.write_text("\n".join(lines[-KEEP:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def load(vault: Path, limit: int = 20) -> list[dict]:
    """最近几条，新的在前。"""
    path = issues_path(vault)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def summary(vault: Path, days: int = 7) -> dict:
    """最近几天按类型分组：哪一类在反复出问题，一眼看得出。"""
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    rows = [r for r in load(vault, KEEP) if (r.get("ts") or "")[:10] >= since]
    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r.get("kind") or "?"] = by_kind.get(r.get("kind") or "?", 0) + 1
    return {"count": len(rows), "by_kind": by_kind, "recent": rows[:8]}
