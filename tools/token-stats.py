#!/usr/bin/env python3
"""统计 Claude Code 在某个项目下的 token 消耗与费用。

数据来源是 Claude Code 自己写的会话记录：
  ~/.claude/projects/<项目路径转义>/*.jsonl
每条 assistant 消息的 message.usage 里带四类 token：
  input_tokens                  未命中缓存的输入
  cache_creation_input_tokens   写入缓存（按 TTL 拆成 5m / 1h 两档，计价不同）
  cache_read_input_tokens       命中缓存的读取
  output_tokens                 输出

用法：
  python3 tools/token-stats.py                    # 当前项目，按会话汇总
  python3 tools/token-stats.py --by day           # 按天汇总
  python3 tools/token-stats.py --by model         # 按模型汇总
  python3 tools/token-stats.py --since 2026-09-10
  python3 tools/token-stats.py --project ~/IdeaProjects/Other
  python3 tools/token-stats.py --all              # 所有项目一起排名
  python3 tools/token-stats.py --json             # 机器可读输出

费用是按 Anthropic 一方 API 公开价换算的「等价金额」，仅供了解量级；
订阅制（Pro/Max）实际不按这个扣费。零第三方依赖。
"""
from __future__ import annotations

import argparse
import json
import os
import unicodedata
import sys
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------- 价格表
# 单位：美元 / 百万 token。cache 读写是在 input 价基础上乘系数：
#   写入 5m TTL = 1.25x，写入 1h TTL = 2x，读取 = 0.1x
# Fable 5.x 是例外，缓存读取按 0.025x（$0.25/MTok）计。
# 表内模型均为 1M 上下文标准价，无长上下文溢价。
PRICES = {
    "claude-fable-5-1":  {"in": 10.0, "out": 50.0, "read_mult": 0.025},
    "claude-fable-5":    {"in": 10.0, "out": 50.0, "read_mult": 0.025},
    "claude-mythos-5-1": {"in": 10.0, "out": 50.0, "read_mult": 0.025},
    "claude-opus-5":     {"in": 5.0,  "out": 25.0},
    "claude-opus-4-8":   {"in": 5.0,  "out": 25.0},
    "claude-opus-4-7":   {"in": 5.0,  "out": 25.0},
    "claude-opus-4-6":   {"in": 5.0,  "out": 25.0},
    "claude-sonnet-5":   {"in": 2.0,  "out": 10.0},
    "claude-sonnet-4-6": {"in": 3.0,  "out": 15.0},
    "claude-haiku-4-5":  {"in": 1.0,  "out": 5.0},
}
WRITE_MULT_5M = 1.25
WRITE_MULT_1H = 2.0
DEFAULT_READ_MULT = 0.1

FIELDS = ("input", "out", "w5m", "w1h", "read")


def price_of(model: str) -> dict | None:
    """模型 id 可能带日期后缀或 [1m] 标记，取最长前缀匹配。"""
    if not model:
        return None
    base = model.split("[")[0]
    best = None
    for key in PRICES:
        if base.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return PRICES[best] if best else None


def cost_of(model: str, s: dict) -> float | None:
    p = price_of(model)
    if p is None:
        return None
    read_mult = p.get("read_mult", DEFAULT_READ_MULT)
    dollars = (
        s["input"] * p["in"]
        + s["w5m"] * p["in"] * WRITE_MULT_5M
        + s["w1h"] * p["in"] * WRITE_MULT_1H
        + s["read"] * p["in"] * read_mult
        + s["out"] * p["out"]
    )
    return dollars / 1_000_000


def project_dir(project: str | None) -> Path:
    """把项目路径转成 ~/.claude/projects 下的目录名（非字母数字一律换成 -）。"""
    root = Path.home() / ".claude" / "projects"
    path = Path(project or os.getcwd()).expanduser().resolve()
    slug = "".join(c if c.isalnum() else "-" for c in str(path))
    d = root / slug
    if not d.is_dir():
        sys.exit(f"找不到会话目录：{d}\n（该项目可能还没用 Claude Code 跑过，或路径写错了）")
    return d


def new_bucket() -> dict:
    b = {f: 0 for f in FIELDS}
    b["reqs"] = 0
    b["cost"] = 0.0
    b["unpriced"] = 0
    return b


def scan(files: list[Path], group: str, since: str | None, until: str | None) -> dict:
    """遍历 jsonl，按 group 维度累加。用 message.id 去重，避免流式分片重复计数。"""
    buckets: dict[str, dict] = defaultdict(new_bucket)
    seen: set[str] = set()
    for f in files:
        with f.open(errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = obj.get("message") or {}
                usage = msg.get("usage")
                if not usage:
                    continue
                mid = msg.get("id")
                if mid:
                    if mid in seen:
                        continue
                    seen.add(mid)

                day = (obj.get("timestamp") or "")[:10]
                if since and day and day < since:
                    continue
                if until and day and day > until:
                    continue

                model = msg.get("model") or "unknown"
                if group == "day":
                    key = day or "unknown"
                elif group == "model":
                    key = model
                else:
                    key = f.stem

                creation = usage.get("cache_creation") or {}
                w1h = creation.get("ephemeral_1h_input_tokens", 0)
                w5m = creation.get("ephemeral_5m_input_tokens", 0)
                if not creation:
                    # 老版本记录没有 TTL 拆分，整体按 5m 价算
                    w5m = usage.get("cache_creation_input_tokens", 0)

                one = {
                    "input": usage.get("input_tokens", 0),
                    "out": usage.get("output_tokens", 0),
                    "w5m": w5m,
                    "w1h": w1h,
                    "read": usage.get("cache_read_input_tokens", 0),
                }
                b = buckets[key]
                for k in FIELDS:
                    b[k] += one[k]
                b["reqs"] += 1
                c = cost_of(model, one)
                if c is None:
                    # <synthetic> 这类零用量记录不算漏价
                    if any(one[k] for k in FIELDS):
                        b["unpriced"] += 1
                else:
                    b["cost"] += c
    return buckets


def fmt(n: int) -> str:
    return f"{n:,}"


def width(s: str) -> int:
    """终端显示宽度：CJK 字符占两列。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s: str, w: int) -> str:
    return s + " " * max(0, w - width(s))


def rpad(s: str, w: int) -> str:
    return " " * max(0, w - width(s)) + s


def render(buckets: dict, group: str, sort_by_key: bool) -> None:
    items = sorted(buckets.items()) if sort_by_key else sorted(
        buckets.items(), key=lambda kv: -kv[1]["cost"]
    )
    head = {"day": "日期", "model": "模型"}.get(group, "会话")
    w = min(max([width(head)] + [width(k) for k in buckets] or [0]), 46)
    print(
        f"{pad(head, w)} {rpad('请求', 6)} {rpad('输入', 12)} {rpad('缓存写5m', 13)} "
        f"{rpad('缓存写1h', 13)} {rpad('缓存读', 15)} {rpad('输出', 12)} {rpad('费用$', 10)}"
    )
    print("-" * (w + 85))
    total = new_bucket()
    for key, b in items:
        print(
            f"{pad(key[:w], w)} {b['reqs']:>6} {fmt(b['input']):>12} {fmt(b['w5m']):>13} "
            f"{fmt(b['w1h']):>13} {fmt(b['read']):>15} {fmt(b['out']):>12} {b['cost']:>10.2f}"
        )
        for k in FIELDS:
            total[k] += b[k]
        total["reqs"] += b["reqs"]
        total["cost"] += b["cost"]
        total["unpriced"] += b["unpriced"]
    print("-" * (w + 85))
    print(
        f"{pad('合计', w)} {total['reqs']:>6} {fmt(total['input']):>12} {fmt(total['w5m']):>13} "
        f"{fmt(total['w1h']):>13} {fmt(total['read']):>15} {fmt(total['out']):>12} "
        f"{total['cost']:>10.2f}"
    )

    all_in = total["input"] + total["w5m"] + total["w1h"] + total["read"]
    grand = all_in + total["out"]
    print()
    print(f"总 token：{fmt(grand)}（输入类 {fmt(all_in)} + 输出 {fmt(total['out'])}）")
    if all_in:
        print(f"缓存命中率：{total['read'] / all_in * 100:.1f}%（缓存读 ÷ 全部输入类）")
    if total["unpriced"]:
        print(f"注意：{total['unpriced']} 条记录的模型不在价格表内，未计入费用。")
    print("费用为按一方 API 公开价换算的等价金额；订阅制实际不这样扣费。")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="统计 Claude Code 在某项目下的 token 消耗与等价费用",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--project", help="项目路径，默认当前目录")
    ap.add_argument("--all", action="store_true", help="统计 ~/.claude/projects 下所有项目")
    ap.add_argument(
        "--by", choices=["session", "day", "model"], default="session", help="汇总维度"
    )
    ap.add_argument("--since", help="起始日期 YYYY-MM-DD（含）")
    ap.add_argument("--until", help="结束日期 YYYY-MM-DD（含）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    if args.all:
        base = Path.home() / ".claude" / "projects"
        files = sorted(base.glob("**/*.jsonl"))
        scope = str(base)
    else:
        d = project_dir(args.project)
        files = sorted(d.glob("**/*.jsonl"))
        scope = str(d)
    if not files:
        sys.exit(f"{scope} 下没有会话记录")

    buckets = scan(files, args.by, args.since, args.until)
    if args.json:
        print(json.dumps({"scope": scope, "by": args.by, "buckets": buckets},
                         ensure_ascii=False, indent=2))
        return
    print(f"范围：{scope}（{len(files)} 个会话文件）")
    render(buckets, args.by, sort_by_key=(args.by == "day"))


if __name__ == "__main__":
    main()
