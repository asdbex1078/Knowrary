"""模型调用账本：每次调 LLM 记一笔，存 .knowrary/llm-usage.json。

为什么同时存 `totals` 和 `recent`：`recent` 要截断（不然这个文件会一直涨），
但截断不能把历史花销一起抹掉——所以累计量单独放 `totals`，只加不减。
`by_day` 同理，只保留最近若干天的明细，总数仍在 `totals` 里。

`cost_usd` 只有 provider 自己报了才有值（目前 claude-cli 会报）。
**不按型号估价**：价目表会过期，估出来的数字比没有更糟，宁可只显示 token 数。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .mdio import load_json, write_json_atomic

SCHEMA_VERSION = 1
RECENT_KEEP = 200        # 明细留最近多少次调用
DAYS_KEEP = 60           # 按天汇总留多少天
TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def usage_path(vault: Path) -> Path:
    return vault / ".knowrary" / "llm-usage.json"


def empty_usage_log() -> dict:
    return {"schema_version": SCHEMA_VERSION, "updated_at": None,
            "totals": _zero(), "by_day": {}, "by_op": {}, "recent": []}


def _zero() -> dict:
    return {"calls": 0, "errors": 0, "cost_usd": 0.0, "ms": 0, **{k: 0 for k in TOKEN_KEYS}}


def load_usage(vault: Path) -> dict:
    path = usage_path(vault)
    if not path.exists():
        return empty_usage_log()
    try:
        data = load_json(path)
    except (ValueError, OSError):
        return empty_usage_log()
    if not isinstance(data, dict) or not isinstance(data.get("recent"), list):
        return empty_usage_log()
    for key in ("totals",):
        data[key] = {**_zero(), **(data.get(key) or {})}
    data.setdefault("by_day", {})
    data.setdefault("by_op", {})
    data["schema_version"] = SCHEMA_VERSION
    return data


def _add(bucket: dict, row: dict) -> dict:
    out = {**_zero(), **bucket}
    out["calls"] += 1
    out["errors"] += 1 if not row["ok"] else 0
    out["ms"] += int(row.get("ms") or 0)
    out["cost_usd"] = round(out["cost_usd"] + float(row.get("cost_usd") or 0), 6)
    for k in TOKEN_KEYS:
        out[k] += int(row.get(k) or 0)
    return out


def record(vault: Path, row: dict) -> dict:
    """记一笔调用。row: {op, role, provider, model, ok, ms, error, 以及各 token 字段}"""
    log = load_usage(vault)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    row = {"ts": now.isoformat().replace("+00:00", "Z"), **row}
    # **按本地日期分桶，不按 ts 的 UTC 日期。** `summary()` 用的是 dt.date.today()（本地），
    # 两边不一致的话，东八区每天 00:00～08:00 之间「今天」永远显示 0——
    # 那几个小时里 UTC 还停在昨天。ts 仍然存 UTC，那是时间点，不是账期。
    day = dt.date.today().isoformat()

    log["totals"] = _add(log["totals"], row)
    log["by_day"][day] = _add(log["by_day"].get(day, {}), row)
    log["by_op"][row.get("op") or "?"] = _add(log["by_op"].get(row.get("op") or "?", {}), row)
    log["recent"] = ([row] + log["recent"])[:RECENT_KEEP]          # 新的在前，看起来就是一条时间线
    for stale in sorted(log["by_day"])[:-DAYS_KEEP]:
        del log["by_day"][stale]

    log["updated_at"] = row["ts"]
    write_json_atomic(usage_path(vault), log)
    return log


def summary(log: dict, today: str | None = None) -> dict:
    """今天 + 累计 + 分功能，给前端直接摆出来。"""
    today = today or dt.date.today().isoformat()
    return {"today": {**_zero(), **(log.get("by_day", {}).get(today) or {})},
            "totals": {**_zero(), **(log.get("totals") or {})},
            "by_op": log.get("by_op") or {},
            "recent": (log.get("recent") or [])[:30],
            "date": today}
