"""按角色取 provider、发起一次 LLM 调用，并把回答解析成 JSON。

suggest 与 quiz 共用：角色名（learn / review）和"剥围栏再 json.loads"的容错
只写一份，省得两边各写死一次、改配置时漏掉一处。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from pathlib import Path

from .paths import core  # 副作用：把 tools/knowrary/ 注入 sys.path；同时用它的 record_usage

import llm_backend  # noqa: E402  （必须在 paths 之后）

log = logging.getLogger(__name__)


def ask(vault: Path, role: str, prompt: str, op: str = "?") -> str:
    """按角色取 provider 问一次，并把用量记进账本。

    所有 LLM 调用都从这里过，所以记账放这一处就够了——包括失败的那些：
    调用失败照样烧了时间、也可能已经计费，账本上不能没有它。
    """
    cfg, _ = llm_backend.load_config(vault)
    name, provider = llm_backend.resolve_provider(cfg, role)
    started = time.monotonic()
    row = {"op": op, "role": role, "provider": name, "model": provider.get("model")}
    try:
        text, used = llm_backend.ask_detailed(prompt, provider)
    except BaseException as exc:                      # SystemExit 也要记：它是 llm_backend 的报错方式
        _record(vault, {**row, "ok": False, "ms": _ms(started), "error": str(exc)[:200]})
        raise _with_context(exc, role, name, provider) from None
    _record(vault, {**row, **used, "model": used.get("model") or provider.get("model"),
                    "ok": True, "ms": _ms(started), "chars": len(text or "")})
    return text


def chat(vault: Path, role: str, messages: list[dict], op: str = "chat", on_delta=None) -> tuple[str, dict]:
    """多轮对话版的 `ask`。同一套 provider 配置、同一本用量账。

    `on_delta(text)` 逐段回调，用来把增量推给 SSE；不传就整段返回。
    """
    cfg, _ = llm_backend.load_config(vault)
    name, provider = llm_backend.resolve_provider(cfg, role)
    started = time.monotonic()
    row = {"op": op, "role": role, "provider": name, "model": provider.get("model")}
    try:
        text, used = llm_backend.chat(messages, provider, on_delta=on_delta)
    except BaseException as exc:
        _record(vault, {**row, "ok": False, "ms": _ms(started), "error": str(exc)[:200]})
        raise _with_context(exc, role, name, provider) from None
    _record(vault, {**row, **used, "model": used.get("model") or provider.get("model"),
                    "ok": True, "ms": _ms(started), "chars": len(text or "")})
    return text, used


def _with_context(exc: BaseException, role: str, name: str, provider: dict) -> BaseException:
    """报错里带上**是哪个角色、哪个 provider、哪个模型**在报。

    原来只有一句 "LLM 请求失败 HTTP 503（某个 url）"，而 url 上看不出这是 learn 还是 review、
    用的是配置里哪一条——配了多个 provider 时，第一件事就是猜"到底是谁炸了"。
    """
    head = f"[{role} 角色 · provider `{name}` · 模型 {provider.get('model') or '(默认)'}] "
    return type(exc)(head + str(exc)) if isinstance(exc, SystemExit) else exc


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _record(vault: Path, row: dict) -> None:
    """记账失败绝不能拖垮正经调用——用量是旁路，不是主线。"""
    try:
        core.record_usage(vault, row)
    except OSError as exc:
        log.warning("用量没记上：%s", exc)


def _carve(text: str) -> str:
    """从一段话里抠出第一个完整的 JSON 对象。

    模型经常在 JSON 前后加一句「好的，这是题目：」或者半个围栏，只剥首尾围栏不够用。
    按花括号配对扫一遍（跳过字符串里的括号和转义），比正则可靠。
    """
    start = text.find("{")
    if start < 0:
        return text
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False
            continue
        if ch == '"': in_str = True
        elif ch == "{": depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


def parse_json(raw: str, context: str = "", vault: Path | None = None) -> dict:
    """把回答解析成 dict。模型偶尔会套 ``` 围栏或直接答非所问，解析失败只警告不抛。

    解析不出来时**必须留痕**：调用已经花了钱和时间，界面上只剩一句「没出出题来」的话，
    下次遇到照样两眼一抹黑。原文前 600 字进问题流（`.knowrary/issues.jsonl`）。
    """
    text = raw.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        text = text[first_nl + 1:] if first_nl != -1 else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    for candidate in (text, _carve(text)):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    log.warning("LLM 返回的不是合法 JSON%s，raw=%s", f"（{context}）" if context else "", raw[:200])
    if vault is not None:
        try:
            core.record_issue(vault, "llm", f"模型没给出合法 JSON（{context}）", where="parse_json",
                              detail=raw[:600])
        except OSError as exc:
            log.warning("问题流没记上：%s", exc)
    return {}


def clean_layer(raw) -> str:
    """模型给的抽象层：只认 core.LAYERS 里的七档，别的一律当没填。

    和 load 不一样：load 认不出就退回默认档（总得有个负荷才排得出时间表），
    而 layer 认不出必须留空——瞎填一个会把节点放进错的泳道，比不分层更难发现。

    拆计划（projects.py）和关系建议（suggest.py）两条链路都要收这个字段，
    所以判断只写一份：两边各写一遍，改了七档的名字必定漏掉一处。
    """
    val = str(raw or "").strip()
    return val if val in core.LAYERS else ""


def clean_year(raw) -> int | None:
    """模型给的年份：三到四位的才要。

    模型偶尔会给 "1970s"、"约 2017"、"不详" 或者一整句话，全部当没填——
    错的年份会在历史视图上把节点摆到错误的位置，比空着更难查。
    """
    if isinstance(raw, bool):          # bool 是 int 的子类，不挡住 True 就会变成 1
        return None
    try:
        year = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return year if 100 <= year <= dt.date.today().year else None
