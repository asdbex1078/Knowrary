"""Knowrary · LLM 后端（配置文件驱动：多 provider、按角色选用）

配置文件：`~/.knowrary/llm.local.json`（用户级，所有知识库共用；模板见同目录 llm.example.json）。
环境变量 KNOWRARY_LLM_CONFIG 可指向别处。没有配置文件时退回 `claude -p`（复用 Claude Code 登录）。

{
  "providers": {
    "<名字>": { "type": "claude-cli" | "anthropic" | "openai", "model": "...", "api_key": "sk-.. 或 env:VAR", "base_url": "..." }
  },
  "roles": { "learn": "<名字>", "review": "<名字>" }
}

零第三方依赖：anthropic / openai 兼容协议都走 urllib；装了 anthropic SDK 时 anthropic 类型自动改用 SDK 流式（长输出更稳）。

**工具协议有两套，按 provider 能力自动挑**（见 `supports_tools` 与 §多轮对话 + 工具协议）：
anthropic / openai 走原生 tool use；claude-cli 是子进程、没有结构化工具接口，走文本围栏适配。
对上是同一个契约——给 `tools`、拿回结构化的 `calls`，调用方不知道底下用的是哪套。
"""
from __future__ import annotations

import hashlib
import http.client
import importlib.util
import json
import os
import re
import subprocess
import time
import urllib.error
import uuid
import urllib.request
from pathlib import Path

import core

CONFIG_NAME = "llm.local.json"
EXAMPLE_NAME = "llm.example.json"
ROLES = ("learn", "review")
TYPES = ("claude-cli", "anthropic", "openai")
DEFAULT_MODELS = {"anthropic": "claude-opus-5", "openai": "gpt-4o"}
DEFAULT_CONFIG = {
    "providers": {"claude-cli": {"type": "claude-cli"}},
    "roles": {"learn": "claude-cli", "review": "claude-cli"},
}
TIMEOUT = 900
RETRY_WAITS = (1.0, 3.0)                 # 连接类失败重试两次，之间等这么久
RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class LLMConfigError(SystemExit):
    pass


class LLMTransient(SystemExit):
    """这一次没通，但**再试一次多半就通**：连接被掐、读到一半断、429 / 5xx。

    单列一类是为了让重试有个准确的判据。按报错文案去猜"这是不是网络问题"迟早猜错，
    而把配置错、4xx 也重试一遍，只是把同一个错再犯两遍、白等四秒。
    仍然继承 `SystemExit`：CLI 那侧的行为一个字没变，服务侧照旧在 `llm_call` 里
    换成 `LLMFailed`，顺带盖上角色 / provider / 模型的前缀。
    """


# ---------------------------------------------------------------- 配置

def config_path() -> Path:
    """**模型配置只有一份**：`~/.knowrary/llm.local.json`（`KNOWRARY_LLM_CONFIG` 可覆盖）。

    2026-09-22 起不再有"库级配置"。一个人有好几个库（自己的、别人的参考库、示例库），
    密钥绑在库上就得切一次配一次；参考库和示例库里更不该出现密钥。
    库里留的只有**数据**（摆位、项目、复习记录、关系类型表），配置全在这儿。
    """
    env = os.environ.get("KNOWRARY_LLM_CONFIG")
    return Path(env).expanduser() if env else core.user_dir() / CONFIG_NAME


def load_config() -> tuple[dict, Path | None]:
    """返回 (配置, 来源路径)；还没配过时返回 (默认配置, None)——零配置走 `claude -p`。"""
    path = config_path()
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG)), None
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise LLMConfigError(f"LLM 配置不是合法 JSON：{path}\n  {e}")
    validate_config(cfg, path)
    return cfg, path


def _real_keys(section: dict) -> list[str]:
    """`_` 开头的键是注释（模板里到处是 `_说明`），不是条目。

    不跳过它们的话，照抄 llm.example.json 就会报「角色 `_说明` 指向不存在的 provider」——
    模板里 roles 那一段本来就带着注释。
    """
    return [k for k in section if not str(k).startswith("_")]


def validate_config(cfg: dict, path: Path) -> None:
    providers = cfg.get("providers")
    if not isinstance(providers, dict) or not _real_keys(providers):
        raise LLMConfigError(f"{path}: `providers` 必须是非空对象")
    for name in _real_keys(providers):
        p = providers[name]
        if not isinstance(p, dict) or p.get("type") not in TYPES:
            raise LLMConfigError(f"{path}: provider `{name}` 的 type 必须是 {'/'.join(TYPES)}")
        if p["type"] == "openai" and not p.get("base_url"):
            raise LLMConfigError(f"{path}: provider `{name}`（openai 兼容）缺少 base_url")
    roles = cfg.get("roles") or {}
    if not isinstance(roles, dict):
        raise LLMConfigError(f"{path}: `roles` 必须是对象")
    for role in _real_keys(roles):
        if roles[role] not in _real_keys(providers):
            raise LLMConfigError(f"{path}: 角色 `{role}` 指向不存在的 provider `{roles[role]}`")
    for role in ROLES:
        roles.setdefault(role, _real_keys(providers)[0])
    cfg["roles"] = roles


def resolve_provider(cfg: dict, role: str, override: str | None = None) -> tuple[str, dict]:
    """按角色取 provider；--llm <名字> 可临时覆盖。"""
    known = _real_keys(cfg["providers"])
    name = override or cfg["roles"].get(role) or known[0]
    if name not in known:
        raise LLMConfigError(f"未知 provider `{name}`，可选：{', '.join(known)}")
    return name, cfg["providers"][name]


def nested_cli_warning(cfg: dict) -> str:
    """在 Claude Code 会话里又要跑 `claude -p` —— 每一次调用都会当场失败。

    报错是 `Error: Claude Code cannot be launched inside another Claude Code session.`，
    2026-09-16 就整轮报废过一次。它是**零配置的默认路径**，所以最容易在
    "从 Claude Code 里顺手起个服务" 的时候踩到，而且要等第一句话发出去才炸。

    只在**真的有角色指向 claude-cli** 时才吭声：配了 anthropic / openai 的人
    嵌套着跑没有任何问题，对他们报警就是狼来了。

    返回一段给人看的话；没问题就返回空串。
    """
    if not os.environ.get("CLAUDECODE"):
        return ""
    providers = cfg.get("providers") or {}
    roles = cfg.get("roles") or {}
    hit = sorted({r for r in ROLES
                  if (providers.get(roles.get(r)) or {}).get("type") == "claude-cli"})
    if not hit:
        return ""
    return ("⚠️  当前终端在一个 Claude Code 会话里，而角色 "
            f"{'、'.join(hit)} 指向 claude-cli —— `claude -p` 不能嵌套运行，"
            "这些调用会全部失败。\n"
            "    要么换个普通终端起服务，要么在 .knowrary/llm.local.json 里"
            "把这些角色指到 anthropic / openai 类型的 provider。")


def resolve_secret(value: str | None) -> str | None:
    """api_key 支持 `env:VAR_NAME` 引用环境变量。"""
    if isinstance(value, str) and value.startswith("env:"):
        var = value[4:]
        secret = os.environ.get(var)
        if not secret:
            raise LLMConfigError(f"api_key 引用的环境变量 {var} 未设置")
        return secret
    return value


def describe(cfg: dict, path: Path | None) -> str:
    lines = [f"配置文件：{path or '（无，使用默认 claude-cli）'}"]
    for name in _real_keys(cfg["providers"]):
        p = cfg["providers"][name]
        roles = [r for r in _real_keys(cfg["roles"]) if cfg["roles"][r] == name]
        tag = f"  ← 角色 {', '.join(roles)}" if roles else ""
        model = p.get("model") or DEFAULT_MODELS.get(p["type"], "（claude 默认）")
        lines.append(f"  {name:<14} {p['type']:<10} {model}{tag}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 调用

def empty_usage() -> dict:
    """三个 provider 的用量字段各不相同，先归一到这一张表再往外给。

    `cost_usd` 只有 provider 自己报了才填。**不在这里按型号估价**——
    价目表会过期，估出来的数字比没有更糟。
    """
    return {"model": None, "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": None}


def ask(prompt: str, provider: dict, model_override: str | None = None) -> str:
    return ask_detailed(prompt, provider, model_override)[0]


def _retrying(run, spoke: dict | None = None):
    """连接类失败（`LLMTransient`）自动重试——**但只在这一次一个字都还没吐出来时**。

    已经吐了一半再重来，屏幕上那半截和重试后的正文会叠在一起（除非再约一个"清屏"事件），
    而且能吐字说明请求真的到了模型那边，多半不是抖动，重来一次八成还是同样的地方断。
    所以流式那条路把"吐没吐过字"记在 `spoke` 里，由它一票否决重试。

    非瞬时的错误（配置错、4xx、模型拒绝）一次都不重试，直接抛。
    """
    for wait in RETRY_WAITS + (None,):
        try:
            return run()
        except LLMTransient:
            if wait is None or (spoke or {}).get("n"):
                raise
            time.sleep(wait)


def ask_detailed(prompt: str, provider: dict,
                 model_override: str | None = None) -> tuple[str, dict]:
    """问一次，连用量一起返回。`ask()` 是它的薄壳，老调用方不受影响。

    单轮不流式，一个字都没往外吐过，所以连接抖了直接重试是安全的。
    """
    return _retrying(lambda: _ask_once(prompt, provider, model_override))


def _ask_once(prompt: str, provider: dict, model_override: str | None) -> tuple[str, dict]:
    model = model_override or provider.get("model")
    kind = provider["type"]
    if kind == "claude-cli":
        return _ask_claude_cli(prompt, model)
    if kind == "anthropic":
        return _ask_anthropic(prompt, provider, model or DEFAULT_MODELS["anthropic"])
    return _ask_openai(prompt, provider, model or DEFAULT_MODELS["openai"])


def _claude_cli_usage(data: dict) -> dict:
    u = empty_usage()
    raw = data.get("usage") or {}
    u["input_tokens"] = int(raw.get("input_tokens") or 0)
    u["output_tokens"] = int(raw.get("output_tokens") or 0)
    # 缓存单列：claude -p 的成本大头常常是系统提示的 cache，不拆开会看不懂那个金额
    u["cache_read_tokens"] = int(raw.get("cache_read_input_tokens") or 0)
    u["cache_write_tokens"] = int(raw.get("cache_creation_input_tokens") or 0)
    if isinstance(data.get("total_cost_usd"), (int, float)):
        u["cost_usd"] = float(data["total_cost_usd"])
    models = data.get("modelUsage")
    if isinstance(models, dict) and models:
        # 一次 claude -p 可能动用不止一个模型（主模型 + 干杂活的小模型），
        # 取输出最多的那个——那才是真正答题的；取第一个会随字典顺序漂。
        u["model"] = max(models, key=lambda m: (models[m] or {}).get("outputTokens") or 0)
    return u


def _ask_claude_cli(prompt: str, model: str | None, extra: list[str] | None = None) -> tuple[str, dict]:
    cmd = ["claude", "-p", "--output-format", "json"] + (extra or [])
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=TIMEOUT)
    except FileNotFoundError:
        raise LLMConfigError("找不到 `claude` 命令：安装 Claude Code，或在 llm.local.json 里配置 anthropic / openai 类型的 provider")
    if proc.returncode != 0:
        raise SystemExit(f"claude -p 失败（{proc.returncode}）：{proc.stderr[:500]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.stdout, empty_usage()
    if not isinstance(data, dict):
        return proc.stdout, empty_usage()
    return data.get("result") or "", _claude_cli_usage(data)


def _anthropic_usage(raw, model: str) -> dict:
    """SDK 返回对象、REST 返回 dict，两边字段名一样，取值方式不同。"""
    get = (lambda k: getattr(raw, k, 0)) if not isinstance(raw, dict) else (lambda k: raw.get(k) or 0)
    u = empty_usage()
    u["model"] = model
    u["input_tokens"] = int(get("input_tokens") or 0)
    u["output_tokens"] = int(get("output_tokens") or 0)
    u["cache_read_tokens"] = int(get("cache_read_input_tokens") or 0)
    u["cache_write_tokens"] = int(get("cache_creation_input_tokens") or 0)
    return u


def _ask_anthropic(prompt: str, provider: dict, model: str) -> tuple[str, dict]:
    api_key = resolve_secret(provider.get("api_key")) or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMConfigError("anthropic provider 缺少 api_key（也可设置 ANTHROPIC_API_KEY）")
    base = (provider.get("base_url") or "https://api.anthropic.com").rstrip("/")
    max_tokens = int(provider.get("max_tokens", 16000))
    if importlib.util.find_spec("anthropic") is None:
        return _ask_anthropic_rest(prompt, api_key, base, model, max_tokens)
    return _ask_anthropic_sdk(prompt, api_key, base, model, max_tokens)


def _ask_anthropic_sdk(prompt: str, api_key: str, base: str, model: str, max_tokens: int) -> tuple[str, dict]:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key, base_url=base)
    try:
        with client.messages.stream(
            model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}]
        ) as stream:
            msg = stream.get_final_message()
    except (anthropic.APIConnectionError, anthropic.RateLimitError,
            anthropic.InternalServerError) as exc:     # SDK 的连接错误不是 OSError，接不住
        raise LLMTransient(f"LLM 连接失败（{base}）：{type(exc).__name__}: {exc}") from None
    if msg.stop_reason == "refusal":
        raise SystemExit("模型拒绝了这次请求")
    text = "".join(b.text for b in msg.content if b.type == "text")
    return text, _anthropic_usage(getattr(msg, "usage", None), model)


def _ask_anthropic_rest(prompt: str, api_key: str, base: str, model: str, max_tokens: int) -> tuple[str, dict]:
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    payload = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    data = _post_json(f"{base}/v1/messages", headers, payload)
    if data.get("stop_reason") == "refusal":
        raise SystemExit("模型拒绝了这次请求")
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    return text, _anthropic_usage(data.get("usage") or {}, model)


def _ask_openai(prompt: str, provider: dict, model: str) -> tuple[str, dict]:
    """OpenAI 兼容协议：OpenAI / DeepSeek / 通义 / Ollama / vLLM 等。"""
    api_key = resolve_secret(provider.get("api_key")) or "none"
    base = provider["base_url"].rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False}
    if provider.get("max_tokens"):
        payload["max_tokens"] = int(provider["max_tokens"])
    if provider.get("temperature") is not None:
        payload["temperature"] = provider["temperature"]
    data = _post_json(f"{base}/chat/completions", headers, payload)
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise SystemExit("OpenAI 兼容接口返回格式异常：\n" + json.dumps(data, ensure_ascii=False)[:800])
    return text, _openai_usage(data.get("usage") or {}, data.get("model") or model)


def _openai_usage(raw: dict, model: str) -> dict:
    """OpenAI 兼容口径的用量。**这一路没有"写了多少缓存"那一列**——

    兼容接口只回 `prompt_tokens_details.cached_tokens`（这一次命中了多少），
    百炼 / DeepSeek 这类隐式缓存的写入既不上报也不单独计费，于是 `cache_write_tokens`
    恒为 0。那是口径如此，不是没缓存：判有没有吃到缓存要看 `cache_read ÷ input`，
    别用读写比（那是 anthropic 那一路的指标，在这里永远除不出来）。

    三条路都从这里过：`_ask_openai`、`_chat_openai` 的流式与非流式分支。
    原来非流式那支直接还一张空表，token 全丢账——账本上那长得和"没命中"一模一样。
    """
    u = empty_usage()
    u["model"] = model
    u["input_tokens"] = int(raw.get("prompt_tokens") or 0)
    u["output_tokens"] = int(raw.get("completion_tokens") or 0)
    u["cache_read_tokens"] = int((raw.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    return u


def _post_json(url: str, headers: dict, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise _http_failed(url, e)
    except (OSError, http.client.HTTPException) as e:      # URLError 也是 OSError
        raise _conn_failed(url, e)


def _http_failed(url: str, e: urllib.error.HTTPError) -> SystemExit:
    detail = e.read().decode("utf-8", "replace")[:800]
    msg = f"LLM 请求失败 HTTP {e.code}（{url}）：\n{detail}"
    return LLMTransient(msg) if e.code in RETRY_STATUS else SystemExit(msg)


def _conn_failed(url: str, exc: BaseException) -> SystemExit:
    """连接层的失败，一律收成这一条带 url 的报错。

    **为什么不能只接 `URLError`**：urllib 只在"发请求"那一步把 OSError 包成 URLError，
    等响应时对方断开，`getresponse()` 抛的是原样的 `http.client.RemoteDisconnected`
    （`ConnectionResetError` 的子类，不是 URLError）。只接 URLError 的话它会一路裸奔到
    出错流水里——url、角色、provider、模型全丢，事后只剩一句 "Remote end closed
    connection without response"，连是哪个 provider 断的都看不出来（2026-09-21 那次）。
    """
    reason = getattr(exc, "reason", None) or exc
    return LLMTransient(f"LLM 连接失败（{url}）：{type(exc).__name__}: {reason}")


def ping(provider: dict, model_override: str | None = None) -> str:
    """连通性测试：要求模型只回复 OK。"""
    return ask("只回复两个大写字母：OK", provider, model_override).strip()


# probe 用的那道题：**必须逼它先想再开口**，静默期才撑得起来。
# 边想边说的模型第一个字很快就来了，那样量到的首字节没有意义。
PROBE_ASK = ("不要边想边说。先在心里把下面这道题完整推演一遍，每一步都确认站得住，"
             "然后一次性输出结论。\n\n"
             "题目：把「从统计计数到神经网络」这条线上的关键转折按时间排好，"
             "并说明每一次转折到底解决了上一代的哪个具体缺陷。")
PROBE_PAD = "背景资料，与上面的问题无关，仅用于撑长上下文。"


def probe(provider: dict, model_override: str | None = None, pad_chars: int = 20000) -> dict:
    """发一个"要想很久"的请求，量清楚这条链路在第几秒、断在哪一类。

    **和 `ping()` 的分工**：`ping` 问"通不通"（一个极短的请求），`probe` 问"能不能等"。
    2026-09-21 那次故障 `ping` 是测不出来的——断的不是连通性，是模型静默思考期间连接
    被掐（`RemoteDisconnected`：一个响应字节都没到），而短请求永远碰不到那堵墙。
    两次失败一次 63 秒、一次 190 秒（= 三次尝试各 63 秒），指向路径上某一跳的 60 秒
    空闲超时——中转站的网关，或者本机代理的节点。probe 就是用来把这个数量出来的。

    **只发一次，不重试**（走 `_chat_once` 而不是 `chat`）：重试会把几次尝试的耗时叠成
    一个数，而这里要看的恰恰是单次在第几秒断。
    """
    pad = PROBE_PAD * max(1, pad_chars // len(PROBE_PAD))
    marks: dict = {"chars": 0}
    t0 = time.monotonic()

    def on_delta(piece: str) -> None:
        marks.setdefault("first", time.monotonic() - t0)
        marks["chars"] += len(piece)

    row = {"model": model_override or provider.get("model"), "first_byte": None, "chars": 0}
    try:
        text, _calls, usage = _chat_once([{"role": "user", "content": pad + "\n\n" + PROBE_ASK}],
                                         provider, model_override, on_delta, None, None)
    except SystemExit as exc:
        return {**row, "ok": False, "total": time.monotonic() - t0,
                "first_byte": marks.get("first"), "chars": marks["chars"],
                "error": f"{type(exc).__name__}: {exc}"}
    return {**row, "ok": True, "total": time.monotonic() - t0, "first_byte": marks.get("first"),
            "chars": marks["chars"] or len(text), "model": usage.get("model") or row["model"]}



# ---------------------------------------------------------------- 多轮对话 + 工具协议
#
# **对上只有一个契约：给 `tools`，拿回结构化的 `calls`。** 底下有两种实现，按 provider 的
# 能力自动选（`supports_tools`），调用方一行 if 都不用写：
#
#     anthropic / openai  →  原生 tool use / tool_calls（schema 约束、可并行、解析失败可见）
#     claude-cli          →  文本围栏适配层（`claude -p` 是子进程，没有结构化工具接口）
#
# **为什么围栏没被删掉**：它是 claude-cli 唯一能走的路，而 claude-cli 是零配置的默认
# provider——复用本机 Claude Code 登录，不用填任何密钥。把它砍掉等于"想聊天先去开个 API key"。
#
# **为什么围栏也不配当唯一的路**：守协议的责任全压在模型的指令遵循上，而且失败是静默的
# ——解析不出 JSON 就当它没调工具，那段话直接成了答案，留档上看不出任何异常。
# Claude 守得住，换个小一点的模型未必。所以能走原生的一律走原生。
#
# **适配层放在这里，不放在 agent 循环里。** 循环那边只有一条代码路径：
# `_run` 永远把 schema 传下来、永远拿结构化的 calls 回去，不知道底下用的是哪套协议。
# 协议差异（提示词里怎么写、回答里怎么捞）全关在 `_chat_claude_cli` 一个函数内。
#
# 内部消息形状是中立的，provider 差异只在翻译层：
#
#     {"role": "system" | "user",  "content": str}
#     {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "args"}]}
#     {"role": "tool", "tool_call_id": str, "name": str, "content": str}
#
# **只增不改**那条总纲对三条路都成立（claude-cli 靠前缀指纹续会话、anthropic 靠缓存断点、
# openai 靠自动前缀缓存）：前面那一截一个字都别动。


def supports_tools(provider: dict) -> bool:
    """这个 provider 能不能走原生工具调用。

    判据是**协议能力**，不是模型强弱：`claude -p` 收的是一段纯文本、返回的也是纯文本，
    没有地方放 schema，也没有结构化的调用回来。别的都是 HTTP API，两家都有原生工具。
    """
    return provider.get("type") != "claude-cli"


def chat(messages: list[dict], provider: dict, model_override: str | None = None,
         on_delta=None, tools: list[dict] | None = None,
         session: str | None = None) -> tuple[str, list[dict], dict]:
    """多轮对话。返回 `(正文, 工具调用, 用量)`——**三种 provider 同一个契约**。

    `on_delta(text)` 给流式增量；不传就整段返回。
    `tools` 是工具表：`[{"name", "description", "input_schema"}]`（Anthropic 的字段名，
    openai 那侧翻译成 `function`，claude-cli 那侧渲染成提示词里的一段围栏说明）。
    不传就是纯聊天。
    `session` 给 claude-cli 续上同一段会话，只发新增的几条（省的是缓存写）；
    别的 provider 收到它也无妨——它们本来就每次发全量 messages 数组。

    和 `ask()` 的关系：`ask()` 是单轮、无工具的，出题 / 拆计划那类"一问一答"用它就够。
    要能追问、能调工具才走这里。

    连接抖了会自动重试（`_retrying`），**但只在这一次还没吐过字的时候**——
    吐了一半再重来，屏幕上会出现两截叠在一起的正文。
    """
    spoke = {"n": 0}

    def relay(piece):            # 记一笔"这一步已经吐过字"：重试要看它
        spoke["n"] += 1
        on_delta(piece)

    return _retrying(lambda: _chat_once(messages, provider, model_override,
                                        relay if on_delta else None, tools, session), spoke)


def _chat_once(messages: list[dict], provider: dict, model_override: str | None,
               on_delta, tools: list[dict] | None,
               session: str | None) -> tuple[str, list[dict], dict]:
    model = model_override or provider.get("model")
    kind = provider["type"]
    if kind == "claude-cli":
        return _chat_claude_cli(messages, model, on_delta, session, tools)
    if kind == "anthropic":
        return _chat_anthropic(messages, provider, model or DEFAULT_MODELS["anthropic"], on_delta, tools)
    return _chat_openai(messages, provider, model or DEFAULT_MODELS["openai"], on_delta, tools)


def _text_of(m: dict) -> str:
    c = m.get("content")
    return c if isinstance(c, str) else ""


def _split_system(messages: list[dict]) -> tuple[list[str], list[dict]]:
    """anthropic 的 system 是顶层参数——但**只有开头那几条**。

    出现在对话中间或末尾的 system 是「对话中途的操作指令」（mid-conversation system
    message），必须留在 messages 里的原位：顶层 system 整体渲染在所有 messages 之前，
    把会变的东西放进去，等于它一变整段对话的缓存全丢；留在队尾就只作废它自己。
    （server/chat.py 的 `_graph_snapshot` 走的就是这条路。）

    **带 tool_calls 的 assistant 正文可以是空的**（模型只调工具、一个字没说），
    所以不能只按"有没有文本"过滤，否则那一条会被悄悄丢掉、tool_result 找不到它的 tool_use。
    """
    head: list[str] = []
    rest: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "system" and not rest:
            text = _text_of(m).strip()
            if text:
                head.append(text)
            continue
        if role == "tool" or m.get("tool_calls"):
            rest.append(m)
            continue
        if role in ("user", "assistant", "system") and _text_of(m).strip():
            rest.append({"role": role, "content": m["content"]})
    return head, rest


# ---------------------------------------------------------------- claude -p：会话复用 + 围栏适配
#
# `claude -p` 每次都是新进程，没有服务端会话，所以原来每一轮都把整段对话拍平重发。
# 实测的代价（haiku，约 40k token 的上下文）：
#
#     重发全文   第二轮 $0.0673，cache_write 22242
#     --resume   第二轮 $0.0061，cache_write   423
#
# 差在 cache_write：重发全文 = 每一轮都重新写一遍缓存，而缓存写比读贵得多。
# 一个用户回合里常常夹着三四次工具往返（每次工具结果都要再问一遍模型），
# **那几次才是账单的大头**，而它们之间只差末尾几百个字。
#
# 只在**能证明前缀没变**时才续：指纹对不上就老老实实开新的一段。
# 续错一段的代价（模型看着别人的上下文答题）远大于多花的那点钱。
#
# **会话表落盘**：`uvicorn --reload` 一天要重启几十次，只放进程内存等于每次重启都
# 从头重付一次全额 cache_write。claude 那侧的会话本来就存在磁盘上，这边跟着存一份就能续上。
# 存的只是 uuid + 指纹 + 发到第几条，**没有对话正文**——正文在 server/turns.py 那份缓存里。
_CLI_SESSIONS: dict[str, dict] = {}
MAX_CLI_SESSIONS = 32
_SESSION_STORE: Path | None = None
_STORE_LOADED = False

# 围栏：```knowrary {"tool": ..., "args": {...}} ``` —— 非贪婪，只认第一个块（一次只准调一个）
_FENCE_RE = re.compile(r"```knowrary\s*(\{.*?\})\s*```", re.S)


def use_session_store(path: Path) -> None:
    """指定会话表落在哪个文件。幂等：第一次调用时把盘上那份读进来，之后只认内存这份。"""
    global _SESSION_STORE, _STORE_LOADED
    _SESSION_STORE = path
    if _STORE_LOADED:
        return
    _STORE_LOADED = True
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return                                   # 没有 / 坏了都当没有：它是可丢的缓存
    if isinstance(rows, dict):
        _CLI_SESSIONS.update({k: v for k, v in rows.items() if isinstance(v, dict)})


def _save_sessions() -> None:
    """落盘。**记不上绝不能拖垮正经调用**——续不上最多是多花一次全量重发的钱。"""
    if _SESSION_STORE is None:
        return
    tmp = _SESSION_STORE.with_suffix(".tmp")
    try:
        _SESSION_STORE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(_CLI_SESSIONS, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _SESSION_STORE)
    except OSError:
        pass


def _fingerprint(messages: list[dict]) -> str:
    """前缀指纹。**按渲染后的样子算**，不是按 role + content。

    因为消息里现在可能带 `tool_calls`：两条 assistant 正文一样、调的工具不一样，
    role + content 会算出同一个指纹，于是"续"上一段其实不同的上下文。
    渲染后的文本里有围栏块，两者自然分得开。
    """
    raw = "\u0000".join(_render_one(m) for m in messages)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cli_session(key: str | None, messages: list[dict], model: str | None) -> tuple[list[str], int]:
    """这一次该带什么命令行参数、从第几条消息开始发。

    返回 `(extra_args, start)`：`start > 0` 表示续上了已有的一段，只发 `messages[start:]`。
    """
    if not key:
        return [], 0
    have = _CLI_SESSIONS.get(key)
    if (have and have["model"] == model and have["sent"] <= len(messages)
            and have["fingerprint"] == _fingerprint(messages[:have["sent"]])):
        return ["--resume", have["uuid"]], have["sent"]
    if len(_CLI_SESSIONS) >= MAX_CLI_SESSIONS:
        _CLI_SESSIONS.pop(next(iter(_CLI_SESSIONS)), None)     # 先进先出，别无限涨
    _CLI_SESSIONS[key] = {"uuid": str(uuid.uuid4()), "sent": 0, "fingerprint": "", "model": model}
    _save_sessions()
    return ["--session-id", _CLI_SESSIONS[key]["uuid"]], 0


def _remember_cli_session(key: str | None, messages: list[dict], reply: dict) -> None:
    """记下"CLI 那一侧现在知道哪些消息"——**含它自己刚生成的那条回复**。

    `reply` 是**调用方随后会追加的那条 assistant 消息**（正文 + 这一步调的工具），
    不是 CLI 吐出来的原文：下一次的前缀校验比的是调用方手上那份列表，
    两边形状对不上就等于每轮都在重开会话（而且不报错，只是账单变贵）。
    """
    have = _CLI_SESSIONS.get(key or "")
    if not have:
        return
    known = messages + [reply]
    have["sent"] = len(known)
    have["fingerprint"] = _fingerprint(known)
    _save_sessions()


def drop_cli_session(key: str | None) -> None:
    """扔掉一段会话：下一次从头发。"""
    if _CLI_SESSIONS.pop(key or "", None) is not None:
        _save_sessions()


def _fence_of(call: dict) -> str:
    """把一次工具调用渲染回围栏块。转录时要用：模型得看见自己上一步调了什么。"""
    body = json.dumps({"tool": call.get("name"), "args": call.get("args") or {}}, ensure_ascii=False)
    return f"```knowrary\n{body}\n```"


def _render_one(m: dict) -> str:
    """一条中立消息 → 转录里的一段文本。

    `claude -p` 收的是一段纯文本，没有 messages 数组、没有 tool_result 通道，
    所以工具往返只能写成对话里的话。**渲染必须是确定性的**——前缀指纹是按它算的。
    """
    role = m.get("role")
    text = (_text_of(m) or "").strip()
    if role == "tool":
        return f"我：[工具 {m.get('name')} 的结果]\n{m.get('content') or ''}"
    if role == "system":
        return text
    calls = m.get("tool_calls") or []
    if role == "assistant" and calls:
        # 一次只准调一个，但真收到多个也照样全渲染出来，别让转录和事实对不上
        return "你：" + "\n".join([text, *[_fence_of(c) for c in calls]]).strip()
    return f"{'我' if role == 'user' else '你'}：{text}"


def _render_transcript(messages: list[dict]) -> str:
    """把多轮对话拍平成一段 prompt。

    没有会话可续时**重发全文**，行为和换别的 provider 时完全一致（见 `_cli_session`）。
    """
    return "\n\n".join(t for t in (_render_one(m) for m in messages) if t.strip())


def _fence_spec(tools: list[dict]) -> str:
    """围栏协议的机制说明，作为一条额外的 system 消息插在开头那段 system 之后。

    **只讲怎么调，不重复讲有哪些工具**——工具表已经在 `prompts/chat.md` 渲染过一份
    （同一份 `TOOLS_SPEC`）。在这儿再抄一遍等于花钱买重复，还会和那份说明打架。
    """
    names = "、".join(f"`{t['name']}`" for t in tools)
    return ("（这条链路没有结构化的工具通道，所以上面那张表里的工具这样调：）\n"
            "需要用工具时，输出**一个**这样的代码块，然后**立刻停下**等我把结果给你——\n"
            "不要在同一条消息里既调工具又长篇大论，**也不要一次调两个**：\n\n"
            "```knowrary\n"
            '{"tool": "search_nodes", "args": {"q": "注意力"}}\n'
            "```\n\n"
            f"`tool` 只能是这几个之一：{names}；`args` 就是表里那几个参数。\n"
            "块要闭合、里面必须是合法 JSON——**写坏了我这边看不出你想调工具**，"
            "那段话会被当成你的回答直接发给我。")


def _with_fence_spec(messages: list[dict], tools: list[dict] | None) -> list[dict]:
    """把协议说明插在**开头那段 system 之后**——工具表就在那里面，紧挨着才讲得通。

    位置必须是确定的：`--resume` 的前缀指纹按整个列表算，说明一挪位置前缀就断，
    于是每轮都在重开会话（不报错，只是账单变贵）。开头那段 system 的长度在一段对话里
    是稳定的（会变的图谱快照挂在队尾，见 `_split_system`），所以这个插入点是稳的。
    """
    if not tools:
        return messages
    head = 0
    while head < len(messages) and messages[head].get("role") == "system":
        head += 1
    return [*messages[:head], {"role": "system", "content": _fence_spec(tools)}, *messages[head:]]


def _chat_claude_cli(messages: list[dict], model: str | None, on_delta, session: str | None,
                     tools: list[dict] | None) -> tuple[str, list[dict], dict]:
    """`claude -p` 那条路：围栏适配 + 会话续接。

    对外和另外两条一样返回 `(正文, 工具调用, 用量)`——**围栏只活在这个函数里**，
    上层不知道有它。正文是**剥掉围栏之后**的，免得屏幕上闪过一段 JSON。

    给了 `session` 就尽量续上已有的那一段，只发新增的几条（见 `_cli_session`）。
    续不上（进程重启、CLI 把会话清了）会退回重发全文，不让一次省钱把对话弄炸。
    """
    sent = _with_fence_spec(messages, tools)
    try:
        raw, usage = _run_claude_cli(sent, model, on_delta, session)
    except SystemExit:
        if not session or not _CLI_SESSIONS.get(session, {}).get("sent"):
            raise
        drop_cli_session(session)               # 续不上就当没有过这段会话，重发一次全文
        raw, usage = _run_claude_cli(sent, model, on_delta, None)
    calls = _parse_fence(raw)
    text = _FENCE_RE.sub("", raw or "").strip()
    # 记的是**调用方随后会追加的那条**，不是 CLI 的原文（见 _remember_cli_session）
    _remember_cli_session(session, sent, {"role": "assistant", "content": text, "tool_calls": calls})
    return text, calls, usage


def _parse_fence(raw: str) -> list[dict]:
    """从回答里捞出那个围栏块。捞不到 / JSON 坏了都返回空——**这就是围栏的先天缺陷**。

    原生协议里参数写坏了仍然算"调过这个工具"（带空参数交上去让工具报错）；
    围栏这边连"他是不是想调工具"都判不出来，只能当他没调。
    `supports_tools` 为真的 provider 一律不走这条路，就是为了少受这个罪。
    """
    m = _FENCE_RE.search(raw or "")
    if not m:
        return []
    try:
        call = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    name = str(call.get("tool") or "")
    if not name:
        return []
    args = call.get("args")
    # id 只是用来把 tool_result 对回 tool_use 的，围栏这边没有真 id，自己发一个
    return [{"id": f"fence_{uuid.uuid4().hex[:8]}", "name": name,
             "args": args if isinstance(args, dict) else {}}]


def _run_claude_cli(messages: list[dict], model: str | None, on_delta,
                    session: str | None) -> tuple[str, dict]:
    extra, start = _cli_session(session, messages, model)
    prompt = _render_transcript(messages[start:])
    if on_delta is None:
        return _ask_claude_cli(prompt, model, extra)
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose"] + extra
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        raise LLMConfigError("找不到 `claude` 命令：安装 Claude Code，或在 llm.local.json 里配置 anthropic / openai 类型的 provider")
    proc.stdin.write(prompt)
    proc.stdin.close()

    parts, usage, result_text = [], empty_usage(), None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue                      # 认不出的行一律跳过，别让格式变动打断对话
        kind = ev.get("type")
        if kind == "assistant":
            for block in ((ev.get("message") or {}).get("content") or []):
                if block.get("type") == "text" and block.get("text"):
                    parts.append(block["text"])
                    on_delta(block["text"])
        elif kind == "result":
            result_text = ev.get("result")
            usage = _claude_cli_usage(ev)
    proc.wait(timeout=TIMEOUT)
    if proc.returncode != 0:
        raise SystemExit(f"claude -p 失败（{proc.returncode}）：{(proc.stderr.read() or '')[:500]}")
    # result 事件里的全文才是权威；流里没收到就用拼起来的
    return (result_text if result_text is not None else "".join(parts)), usage


# ---------------------------------------------------------------- anthropic

def _to_anthropic(messages: list[dict]) -> list[dict]:
    """中立形状 → anthropic 的 content block。

    **连着的几条 tool 结果要并成同一条 user 消息**：一个 assistant 回合里并行调了几个工具时，
    它们的 `tool_result` 必须全在紧随其后的那一条 user 里，拆成好几条会被 API 拒。
    """
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.get("tool_call_id") or "",
                     "content": m.get("content") or ""}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                    and out[-1]["content"][-1].get("type") == "tool_result":
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue
        calls = m.get("tool_calls") or []
        if role == "assistant" and calls:
            blocks: list[dict] = []
            if _text_of(m).strip():
                blocks.append({"type": "text", "text": m["content"]})
            blocks += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c.get("args") or {}}
                       for c in calls]
            out.append({"role": "assistant", "content": blocks})
            continue
        out.append({"role": role, "content": m.get("content")})
    return out


def _cached_system(blocks: list[str]) -> list[dict]:
    """顶层 system 整段打一个缓存断点。

    它是整条链路上最大的一段（工具表、格式说明、关系类型表、教练侧写，约 9000 字），
    不标 cache_control 就等于每一轮原价重买一次。

    **这里只剩不会变的东西**——会变的那块（图谱节点数 / 项目列表）已经被 `_split_system`
    留在 messages 队尾当 mid-conversation system message 了，够不着这个断点。
    """
    return [{"type": "text", "text": "\n\n".join(blocks),
             "cache_control": {"type": "ephemeral"}}]


def _cached_tools(tools: list[dict]) -> list[dict]:
    """工具表也打一个断点。

    它和顶层 system 一样是逐字不变的一大块（十来个工具的 schema），而且在 anthropic 的
    prompt 里**排在 system 之前**——不标它，system 那个断点前面就永远躺着一段没缓存的内容。
    断点打在最后一个工具上，覆盖到此为止的整张表。
    """
    if not tools:
        return tools
    out = [dict(t) for t in tools]
    out[-1]["cache_control"] = {"type": "ephemeral"}
    return out


def _cached(messages: list[dict]) -> list[dict]:
    """在最后一条**非 system** 的消息上打缓存断点，把到此为止的整段对话存进缓存。

    一个用户回合里常常夹着三四次工具往返，每次都要把前面所有内容再发一遍——
    断点打在这里，后面那几次就都是缓存命中而不是重新计费。
    前缀不够长时 API 直接忽略这个标记，不会报错，所以不用判长度。

    **为什么跳过 system**：队尾那条 system 是会变的图谱快照，故意留在断点之后，
    它怎么变都不动前面整段对话的缓存；而且 mid-conversation system message 上
    打 cache_control 本身就会 400。
    """
    idx = next((i for i in range(len(messages) - 1, -1, -1)
                if messages[i].get("role") != "system"), None)
    if idx is None:
        return messages
    out = list(messages)
    content = out[idx].get("content")
    if isinstance(content, str):
        out[idx] = {**out[idx], "content": [{"type": "text", "text": content,
                                             "cache_control": {"type": "ephemeral"}}]}
    elif isinstance(content, list) and content and isinstance(content[-1], dict):
        # 已经是分块的（tool_use / tool_result）：断点挂在最后一块上，覆盖同样的范围
        blocks = [dict(b) for b in content]
        blocks[-1]["cache_control"] = {"type": "ephemeral"}
        out[idx] = {**out[idx], "content": blocks}
    return out


def _has_mid_system(messages: list[dict]) -> bool:
    """有没有「不在开头」的 system 消息。"""
    seen_turn = False
    for m in messages:
        if m.get("role") in ("user", "assistant", "tool"):
            seen_turn = True
        elif m.get("role") == "system" and seen_turn:
            return True
    return False


def _fold_mid_system(messages: list[dict]) -> list[dict]:
    """把对话中途的 system 折进前一条 user 里——给不支持这个用法的模型兜底。

    退化的是**安全性**（user 文本里的 `<system-reminder>` 谁都能伪造）和**缓存**
    （它并进了 user 消息，那条消息一变，断点就跟着动），但对话本身照常。
    """
    out: list[dict] = []
    for m in messages:
        if m.get("role") != "system" or not out:
            out.append(m)
            continue
        wrapped = f"<system-reminder>\n{_text_of(m)}\n</system-reminder>"
        prev = out[-1]
        if prev.get("role") == "user" and isinstance(prev.get("content"), str):
            out[-1] = {**prev, "content": f"{prev['content']}\n\n{wrapped}"}
        else:
            out.append({"role": "user", "content": wrapped})
    return out


def _chat_anthropic(messages: list[dict], provider: dict, model: str, on_delta,
                    tools: list[dict] | None) -> tuple[str, list[dict], dict]:
    """**Sonnet 5 不支持 mid-conversation system message**（400），Opus 5 / 4.8 / Fable 5 支持。

    与其维护一张"哪个模型行"的表（它一定会过期），不如撞上 400 再退一步：
    折成 user 文本重发一次。只在报错确实是这件事、而且真有中途 system 时才重试。
    """
    try:
        return _post_anthropic(messages, provider, model, on_delta, tools)
    except SystemExit as exc:
        if "role" not in str(exc) or "system" not in str(exc) or not _has_mid_system(messages):
            raise
        return _post_anthropic(_fold_mid_system(messages), provider, model, on_delta, tools)


def _post_anthropic(messages: list[dict], provider: dict, model: str, on_delta,
                    tools: list[dict] | None) -> tuple[str, list[dict], dict]:
    api_key = resolve_secret(provider.get("api_key")) or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMConfigError("anthropic provider 缺少 api_key（也可设置 ANTHROPIC_API_KEY）")
    base = (provider.get("base_url") or "https://api.anthropic.com").rstrip("/")
    max_tokens = int(provider.get("max_tokens", 16000))
    system, rest = _split_system(messages)
    payload = {"model": model, "max_tokens": max_tokens, "messages": _cached(_to_anthropic(rest))}
    if system:
        payload["system"] = _cached_system(system)
    if tools:
        payload["tools"] = _cached_tools(tools)
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    if on_delta is None:
        data = _post_json(f"{base}/v1/messages", headers, payload)
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        calls = [{"id": b.get("id") or "", "name": b.get("name") or "", "args": b.get("input") or {}}
                 for b in blocks if b.get("type") == "tool_use"]
        return text, calls, _anthropic_usage(data.get("usage") or {}, model)

    payload["stream"] = True
    parts, usage = [], empty_usage()
    usage["model"] = model
    pending: dict[int, dict] = {}          # 流式的 tool_use：参数是一片一片来的，按块号攒
    for ev, data in _sse(f"{base}/v1/messages", headers, payload):
        if ev == "content_block_start":
            block = data.get("content_block") or {}
            if block.get("type") == "tool_use":
                pending[data.get("index")] = {"id": block.get("id") or "",
                                              "name": block.get("name") or "", "json": ""}
        elif ev == "content_block_delta":
            delta = data.get("delta") or {}
            piece = delta.get("text") or ""
            if piece:
                parts.append(piece)
                on_delta(piece)
            frag = delta.get("partial_json")
            if frag is not None and data.get("index") in pending:
                pending[data["index"]]["json"] += frag
        elif ev in ("message_start", "message_delta"):
            raw = (data.get("message") or data).get("usage") or {}
            for k, v in (("input_tokens", "input_tokens"), ("output_tokens", "output_tokens"),
                         ("cache_read_input_tokens", "cache_read_tokens"),
                         ("cache_creation_input_tokens", "cache_write_tokens")):
                if raw.get(k):
                    usage[v] = int(raw[k])
    return "".join(parts), _finish_calls(pending), usage


def _finish_calls(pending: dict) -> list[dict]:
    """把攒了一路的参数片段解析成 dict。

    **解析不出来不能当没调过**：那是模型真的想调这个工具、只是参数写坏了。
    带一个空 args 交给上层，上层会把工具的报错原样喂回去让它重写——
    这正是原生协议比文本围栏强的地方：围栏解析失败会被当成"这段话就是答案"，静默。
    """
    out = []
    for _, row in sorted(pending.items(), key=lambda kv: kv[0] if kv[0] is not None else 0):
        try:
            args = json.loads(row["json"]) if row["json"].strip() else {}
        except json.JSONDecodeError:
            args = {}
        out.append({"id": row["id"], "name": row["name"], "args": args if isinstance(args, dict) else {}})
    return out


# ---------------------------------------------------------------- openai 兼容

def _to_openai(messages: list[dict]) -> list[dict]:
    """中立形状 → OpenAI 兼容的 messages。"""
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append({"role": "tool", "tool_call_id": m.get("tool_call_id") or "",
                        "content": m.get("content") or ""})
            continue
        calls = m.get("tool_calls") or []
        if role == "assistant" and calls:
            out.append({"role": "assistant", "content": _text_of(m) or None,
                        "tool_calls": [{"id": c["id"], "type": "function",
                                        "function": {"name": c["name"],
                                                     "arguments": json.dumps(c.get("args") or {},
                                                                             ensure_ascii=False)}}
                                       for c in calls]})
            continue
        if _text_of(m).strip():
            out.append({"role": role, "content": m["content"]})
    return out


def _openai_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description") or "",
                                              "parameters": t.get("input_schema") or {"type": "object"}}}
            for t in tools]


def _chat_openai(messages: list[dict], provider: dict, model: str, on_delta,
                 tools: list[dict] | None) -> tuple[str, list[dict], dict]:
    api_key = resolve_secret(provider.get("api_key")) or "none"
    base = provider["base_url"].rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"model": model, "messages": _to_openai(messages), "stream": on_delta is not None}
    if tools:
        payload["tools"] = _openai_tools(tools)
    if provider.get("max_tokens"):
        payload["max_tokens"] = int(provider["max_tokens"])
    if provider.get("temperature") is not None:
        payload["temperature"] = provider["temperature"]
    if on_delta is None:
        data = _post_json(f"{base}/chat/completions", headers, payload)
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise SystemExit("OpenAI 兼容接口返回格式异常：\n" + json.dumps(data, ensure_ascii=False)[:800])
        calls = [{"id": c.get("id") or "", "name": (c.get("function") or {}).get("name") or "",
                  "args": _loads_args((c.get("function") or {}).get("arguments"))}
                 for c in (msg.get("tool_calls") or [])]
        return (msg.get("content") or "", calls,
                _openai_usage(data.get("usage") or {}, data.get("model") or model))

    payload["stream_options"] = {"include_usage": True}   # 不要它就拿不到这轮的 token 数
    parts, usage = [], empty_usage()
    usage["model"] = model
    pending: dict[int, dict] = {}        # 流式的 tool_calls：name 只来一次，arguments 是碎片
    for _, data in _sse(f"{base}/chat/completions", headers, payload):
        for ch in data.get("choices") or []:
            delta = ch.get("delta") or {}
            piece = delta.get("content") or ""
            if piece:
                parts.append(piece)
                on_delta(piece)
            for c in delta.get("tool_calls") or []:
                row = pending.setdefault(c.get("index") or 0, {"id": "", "name": "", "json": ""})
                if c.get("id"):
                    row["id"] = c["id"]
                fn = c.get("function") or {}
                if fn.get("name"):
                    row["name"] = fn["name"]
                row["json"] += fn.get("arguments") or ""
        raw = data.get("usage") or {}
        if raw:
            usage = _openai_usage(raw, data.get("model") or model)
    return "".join(parts), _finish_calls(pending), usage


def _loads_args(raw) -> dict:
    try:
        data = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _sse(url: str, headers: dict, payload: dict):
    """读 SSE 流，逐条 yield (事件名, 已解析的 JSON)。`[DONE]` 与心跳行跳过。"""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream")
    for k, v in headers.items():
        req.add_header(k, v)
    event = ""
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    chunk = line[5:].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        yield event, json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
    except urllib.error.HTTPError as e:
        raise _http_failed(url, e)
    except (OSError, http.client.HTTPException) as e:      # 流读到一半断，抛的也是这一类
        raise _conn_failed(url, e)
