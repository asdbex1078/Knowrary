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


# 缓存命中的常设监控 -------------------------------------------------
#
# 缓存失效**没有任何报错**：请求照样成功、答案照样对，只有账单在涨。
# 2026-09-16 就是这么烧掉 $8 的（读写比 1.43×，等于每一轮都在重写缓存而不是读它）。
# 所以它只能靠一个摆在明面上的数盯着——测试钉不住"线上真的命中了"这件事。
HEALTHY_RATIO = 3.0      # 健康的多轮循环在 5-10×；留出余量，低于 3 才报
HEALTHY_HIT = 0.6        # 命中率口径：多轮对话前缀只增不改时该有 80%+，低于 0.6 才报
MIN_CALLS = 5            # 样本太少的比值没意义
MULTI_TURN = ("chat",)   # 只有多轮的才该有高比值；出题 / 建议那类一问一答天然接近 0


def _ratio(bucket: dict) -> float | None:
    """读 ÷ 写。没写过缓存就没有比值可言（不是 0，是"不适用"）。"""
    write = bucket.get("cache_write_tokens") or 0
    return round((bucket.get("cache_read_tokens") or 0) / write, 2) if write else None


def _hit_rate(bucket: dict) -> float | None:
    """命中 ÷ 输入。**只对不报写入的口径有意义**，也只有它们拿得到这个数。

    两套口径天生只能各用一个指标，所以这两个函数是互斥的：
    - claude-cli / anthropic 报 `cache_creation`，但整个前缀都算进读写两列，
      `input_tokens` 只剩个位数（账本里就是 2），除出来是个几万倍的假数 → 用读写比；
    - OpenAI 兼容（百炼 / DeepSeek / vLLM）只回 `cached_tokens`，**没有写入这一列**，
      读写比永远除不出来 → 用命中率。

    换 provider 之后监控不能跟着瞎：2026-09-21 换到千问那天，读写比整天是 `—`、
    `ok` 恒为真，那盏灯既不会红也不会绿，等于没有。
    """
    if bucket.get("cache_write_tokens"):
        return None
    total = bucket.get("input_tokens") or 0
    return round((bucket.get("cache_read_tokens") or 0) / total, 3) if total else None


def _metric_kind(bucket: dict) -> str:
    """这一桶该看哪个指标。**返回的就是那个字段名**（`ratio` / `hit_rate`，没数据时 `none`），
    这样界面直接 `cache[cache.kind]` 取值，不必两边各维护一张 kind → 字段 的对照表。"""
    if _ratio(bucket) is not None:
        return "ratio"
    return "hit_rate" if _hit_rate(bucket) is not None else "none"


def _local_day(ts: str) -> str:
    """账本里的 ts 是 UTC，账期按本地日期（同 `record`）。东八区 00:00～08:00
    那几个小时 UTC 还停在昨天，直接截前 10 个字符会把它们算成前一天。"""
    try:
        return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone().date().isoformat()
    except ValueError:
        return ""


def _today_by_op(log: dict, today: str) -> dict:
    """今天的调用按 op 聚合。**取自 `recent`**（`by_day` 没有 op 维度），
    所以窗口最多 RECENT_KEEP 条——一天打爆 200 次调用时只看得到最近这些，够用。"""
    out: dict[str, dict] = {}
    for row in log.get("recent") or []:
        if _local_day(row.get("ts") or "") != today:
            continue
        out[row.get("op") or "?"] = _add(out.get(row.get("op") or "?", {}), {**row, "ok": row.get("ok", True)})
    return out


def cache_health(log: dict, today: str | None = None) -> dict:
    """**今天**的多轮对话缓存命中得怎么样，以及最难看的是哪个 op。

    单轮调用（quiz / suggest / plan-*）每次都是新前缀，比值天然贴着 0，
    混在总账里算会把信号冲没——所以只看 `MULTI_TURN` 那几个，而且要够样本量。

    **窗口必须是今天，不能用 `by_op` 那个累计桶。** 累计桶只加不减：
    2026-09-16 烧掉 $8 的那天（1.28×）会永久压着分母，于是今天已经修好了
    （3.00×）灯还是红的，将来真退化了这个数也几乎不动——一盏既不会转绿
    也不会报警的灯，等于没有。
    """
    today = today or dt.date.today().isoformat()
    buckets = _today_by_op(log, today)
    agg, rows = {}, []
    for op, b in buckets.items():
        if not op.startswith(MULTI_TURN):
            continue
        agg = _add_tokens(agg, b)
        rows.append((op, b))
    kind = _metric_kind(agg)
    # 指标选定之后，最难看的那个 op 也要按**同一个**指标挑：两套口径的数字没有可比性
    worst = _worst_op(rows, kind)
    line = HEALTHY_RATIO if kind == "ratio" else HEALTHY_HIT
    return {"kind": kind, "ratio": _ratio(agg), "hit_rate": _hit_rate(agg),
            "worst": worst, "healthy": HEALTHY_RATIO, "healthy_hit": HEALTHY_HIT,
            "window": today, "calls": agg.get("calls") or 0,
            # 样本不够就不判——今天才聊两句就报红，跟累计桶一样没人会再看它
            # （`_worst_op` 里那道 MIN_CALLS 的闸就是这个意思，所以 worst 为空一律算好）
            "ok": worst is None or (worst[kind] or 0) >= line}


def _add_tokens(agg: dict, bucket: dict) -> dict:
    """把一个 op 的桶并进合计。只加判指标要用的那几列，不碰钱和耗时。"""
    out = {**agg}
    for k in (*TOKEN_KEYS, "calls"):
        out[k] = (out.get(k) or 0) + (bucket.get(k) or 0)
    return out


def _worst_op(rows: list, kind: str) -> dict | None:
    """今天多轮对话里指标最难看的那个 op。样本不够 MIN_CALLS 的不参评。"""
    if kind == "none":
        return None
    metric = _ratio if kind == "ratio" else _hit_rate
    worst = None
    for op, b in rows:
        val = metric(b)
        if val is None or (b.get("calls") or 0) < MIN_CALLS:
            continue
        if worst is None or val < (worst[kind] or 0):
            # 两个指标字段都摆出来（另一个必然是 None）：契约是固定形状的，
            # 少一个 key 就得让每个读它的人先判存不存在
            worst = {"op": op, "ratio": None, "hit_rate": None, "calls": b["calls"], kind: val}
    return worst


def summary(log: dict, today: str | None = None) -> dict:
    """今天 + 累计 + 分功能 + 缓存健康度，给前端直接摆出来。"""
    today = today or dt.date.today().isoformat()
    by_op = {op: {**b, "cache_ratio": _ratio(b), "cache_hit_rate": _hit_rate(b)}
             for op, b in (log.get("by_op") or {}).items()}
    # 今天和累计这两桶也要带上读写比：契约里这个字段默认 None，不填就是接口里永远是 null，
    # 而"今天的读写比"恰恰是这套监控存在的理由
    day_bucket = {**_zero(), **(log.get("by_day", {}).get(today) or {})}
    all_bucket = {**_zero(), **(log.get("totals") or {})}
    return {"today": {**day_bucket, "cache_ratio": _ratio(day_bucket),
                      "cache_hit_rate": _hit_rate(day_bucket)},
            "totals": {**all_bucket, "cache_ratio": _ratio(all_bucket),
                       "cache_hit_rate": _hit_rate(all_bucket)},
            "by_op": by_op,
            "cache": cache_health(log, today),
            "recent": (log.get("recent") or [])[:30],
            "date": today}
