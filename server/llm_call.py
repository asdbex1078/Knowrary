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


class LLMFailed(Exception):
    """模型这一次没答上来（连不上、超时、配置错、被拒）。

    **为什么要换个类型**：`llm_backend` 是先有 CLI 后有服务的，报错一路用的都是
    `SystemExit`——在命令行里那是对的（打一句话然后退出）。但 `SystemExit` 是
    `BaseException`，Starlette 的异常中间件只接 `Exception`，于是它会**一路穿过请求
    处理层**：客户端拿到的不是"模型挂了"，而是连接莫名其妙断掉，服务端日志里横着一段
    asyncio 的 ExceptionGroup。自测里那段一直挂着的 traceback 就是它。

    所有 LLM 调用都从这个模块过，所以在这里换一次类型就够了，不必去每条路由上补 try。
    """


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


def chat(vault: Path, role: str, messages: list[dict], op: str = "chat", on_delta=None,
         session: str | None = None) -> tuple[str, dict]:
    """多轮对话版的 `ask`。同一套 provider 配置、同一本用量账。

    `on_delta(text)` 逐段回调，用来把增量推给 SSE；不传就整段返回。
    `session` 给 claude-cli 用来续上同一段会话，只发新增的几条（省的是缓存写）；
    别的 provider 收到它也无妨——它们本来就每次发全量 messages 数组。
    """
    # 会话表跟着 vault 落盘：`uvicorn --reload` 一天几十次，只放内存等于每次重启全额重付（复盘 §11.2）。
    # 放在这里而不是让 llm_backend 自己找路径——**vault 在哪只有服务层知道**，
    # 而 llm_backend 是 CLI 也在用的下层，不该反过来依赖服务层的目录约定。
    llm_backend.use_session_store(vault / ".knowrary" / "cli-sessions.json")
    cfg, _ = llm_backend.load_config(vault)
    name, provider = llm_backend.resolve_provider(cfg, role)
    started = time.monotonic()
    row = {"op": op, "role": role, "provider": name, "model": provider.get("model")}
    try:
        text, used = llm_backend.chat(messages, provider, on_delta=on_delta, session=session)
    except BaseException as exc:
        _record(vault, {**row, "ok": False, "ms": _ms(started), "error": str(exc)[:200]})
        raise _with_context(exc, role, name, provider) from None
    _record(vault, {**row, **used, "model": used.get("model") or provider.get("model"),
                    "ok": True, "ms": _ms(started), "chars": len(text or "")})
    return text, used


def _with_context(exc: BaseException, role: str, name: str, provider: dict) -> BaseException:
    """报错里带上**是哪个角色、哪个 provider、哪个模型**在报，顺便换成 `LLMFailed`。

    原来只有一句 "LLM 请求失败 HTTP 503（某个 url）"，而 url 上看不出这是 learn 还是 review、
    用的是配置里哪一条——配了多个 provider 时，第一件事就是猜"到底是谁炸了"。
    """
    head = f"[{role} 角色 · provider `{name}` · 模型 {provider.get('model') or '(默认)'}] "
    if isinstance(exc, SystemExit):
        return LLMFailed(head + str(exc))
    return exc


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
