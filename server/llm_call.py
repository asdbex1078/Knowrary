"""按角色取 provider、发起一次 LLM 调用，并把回答解析成 JSON。

suggest 与 quiz 共用：角色名（learn / review）和"剥围栏再 json.loads"的容错
只写一份，省得两边各写死一次、改配置时漏掉一处。
"""
from __future__ import annotations

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


def parse_json(raw: str, context: str = "") -> dict:
    """把回答解析成 dict。模型偶尔会套 ``` 围栏或直接答非所问，解析失败只警告不抛。"""
    text = raw.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        text = text[first_nl + 1:] if first_nl != -1 else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    try:
        data = json.loads(text.strip())
    except json.JSONDecodeError:
        log.warning("LLM 返回的不是合法 JSON%s，raw=%s", f"（{context}）" if context else "", raw[:200])
        return {}
    return data if isinstance(data, dict) else {}
