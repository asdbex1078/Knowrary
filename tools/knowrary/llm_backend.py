"""Knowrary · LLM 后端（配置文件驱动：多 provider、按角色选用）

配置文件：<vault>/.knowrary/llm.local.json（gitignore 忽略 *.local.json），模板见 llm.example.json。
环境变量 KNOWRARY_LLM_CONFIG 可指向别处。没有配置文件时退回 `claude -p`（复用 Claude Code 登录）。

{
  "providers": {
    "<名字>": { "type": "claude-cli" | "anthropic" | "openai", "model": "...", "api_key": "sk-.. 或 env:VAR", "base_url": "..." }
  },
  "roles": { "learn": "<名字>", "review": "<名字>" }
}

零第三方依赖：anthropic / openai 兼容协议都走 urllib；装了 anthropic SDK 时 anthropic 类型自动改用 SDK 流式（长输出更稳）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

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


class LLMConfigError(SystemExit):
    pass


# ---------------------------------------------------------------- 配置

def config_path(vault: Path) -> Path:
    env = os.environ.get("KNOWRARY_LLM_CONFIG")
    return Path(env).expanduser() if env else vault / ".knowrary" / CONFIG_NAME


def load_config(vault: Path) -> tuple[dict, Path | None]:
    """返回 (配置, 来源路径)；没有配置文件时返回 (默认配置, None)。"""
    path = config_path(vault)
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


def ask_detailed(prompt: str, provider: dict,
                 model_override: str | None = None) -> tuple[str, dict]:
    """问一次，连用量一起返回。`ask()` 是它的薄壳，老调用方不受影响。"""
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


def _ask_claude_cli(prompt: str, model: str | None) -> tuple[str, dict]:
    cmd = ["claude", "-p", "--output-format", "json"]
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
    with client.messages.stream(
        model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}]
    ) as stream:
        msg = stream.get_final_message()
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
    raw = data.get("usage") or {}
    u = empty_usage()
    u["model"] = data.get("model") or model
    u["input_tokens"] = int(raw.get("prompt_tokens") or 0)
    u["output_tokens"] = int(raw.get("completion_tokens") or 0)
    u["cache_read_tokens"] = int((raw.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    return text, u


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
        detail = e.read().decode("utf-8", "replace")[:800]
        raise SystemExit(f"LLM 请求失败 HTTP {e.code}（{url}）：\n{detail}")
    except urllib.error.URLError as e:
        raise SystemExit(f"LLM 连接失败（{url}）：{e.reason}")


def ping(provider: dict, model_override: str | None = None) -> str:
    """连通性测试：要求模型只回复 OK。"""
    return ask("只回复两个大写字母：OK", provider, model_override).strip()


# ---------------------------------------------------------------- 多轮对话（阶段 12）

def _render_transcript(messages: list[dict]) -> str:
    """把多轮对话拍平成一段 prompt。

    `claude -p` 收的是一段纯文本，没有 messages 数组，所以多轮只能这么喂。
    **每一轮都重发全文**（无服务端会话状态）：对一个人用的系统，实现简单比省 token 重要，
    而且这样换 provider 时行为完全一致。
    """
    out = []
    for m in messages:
        role = m.get("role")
        text = (m.get("content") or "").strip()
        if not text:
            continue
        if role == "system":
            out.append(text)
        else:
            out.append(f"{'我' if role == 'user' else '你'}：{text}")
    return "\n\n".join(out)


def chat(messages: list[dict], provider: dict, model_override: str | None = None,
         on_delta=None) -> tuple[str, dict]:
    """多轮对话。`on_delta(text)` 给流式增量；不传就整段返回。

    和 `ask()` 的关系：`ask()` 是单轮的，出题 / 拆计划那类"一问一答"用它就够；
    对话式教练要能追问（「你说用了 CAS，那 ABA 怎么解决」），必须有这个。
    两者共用同一套 provider 配置与用量统计，不另起一套。
    """
    model = model_override or provider.get("model")
    kind = provider["type"]
    if kind == "claude-cli":
        return _chat_claude_cli(messages, model, on_delta)
    if kind == "anthropic":
        return _chat_anthropic(messages, provider, model or DEFAULT_MODELS["anthropic"], on_delta)
    return _chat_openai(messages, provider, model or DEFAULT_MODELS["openai"], on_delta)


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """anthropic 的 system 是顶层参数，不进 messages 数组。"""
    system = "\n\n".join((m.get("content") or "") for m in messages if m.get("role") == "system")
    rest = [{"role": m["role"], "content": m.get("content") or ""}
            for m in messages if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()]
    return system.strip(), rest


def _chat_claude_cli(messages: list[dict], model: str | None, on_delta) -> tuple[str, dict]:
    """`claude -p --output-format stream-json`：一行一个事件。

    增量按**消息块**给，不按 token——`--include-partial-messages` 的事件形状是内部细节，
    盯着它写解析迟早会被上游改动打烂。块级增量已经够"看得见它在写"了。
    """
    prompt = _render_transcript(messages)
    if on_delta is None:
        return _ask_claude_cli(prompt, model)
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
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


def _chat_anthropic(messages: list[dict], provider: dict, model: str, on_delta) -> tuple[str, dict]:
    api_key = resolve_secret(provider.get("api_key")) or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMConfigError("anthropic provider 缺少 api_key（也可设置 ANTHROPIC_API_KEY）")
    base = (provider.get("base_url") or "https://api.anthropic.com").rstrip("/")
    max_tokens = int(provider.get("max_tokens", 16000))
    system, rest = _split_system(messages)
    payload = {"model": model, "max_tokens": max_tokens, "messages": rest}
    if system:
        payload["system"] = system
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    if on_delta is None:
        data = _post_json(f"{base}/v1/messages", headers, payload)
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return text, _anthropic_usage(data.get("usage") or {}, model)

    payload["stream"] = True
    parts, usage = [], empty_usage()
    usage["model"] = model
    for ev, data in _sse(f"{base}/v1/messages", headers, payload):
        if ev == "content_block_delta":
            piece = (data.get("delta") or {}).get("text") or ""
            if piece:
                parts.append(piece)
                on_delta(piece)
        elif ev in ("message_start", "message_delta"):
            raw = (data.get("message") or data).get("usage") or {}
            for k, v in (("input_tokens", "input_tokens"), ("output_tokens", "output_tokens"),
                         ("cache_read_input_tokens", "cache_read_tokens"),
                         ("cache_creation_input_tokens", "cache_write_tokens")):
                if raw.get(k):
                    usage[v] = int(raw[k])
    return "".join(parts), usage


def _chat_openai(messages: list[dict], provider: dict, model: str, on_delta) -> tuple[str, dict]:
    api_key = resolve_secret(provider.get("api_key")) or "none"
    base = provider["base_url"].rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"model": model, "messages": [{"role": m["role"], "content": m.get("content") or ""}
                                            for m in messages if (m.get("content") or "").strip()],
               "stream": on_delta is not None}
    if provider.get("max_tokens"):
        payload["max_tokens"] = int(provider["max_tokens"])
    if provider.get("temperature") is not None:
        payload["temperature"] = provider["temperature"]
    if on_delta is None:
        data = _post_json(f"{base}/chat/completions", headers, payload)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise SystemExit("OpenAI 兼容接口返回格式异常：\n" + json.dumps(data, ensure_ascii=False)[:800])
        return text, empty_usage()

    payload["stream_options"] = {"include_usage": True}   # 不要它就拿不到这轮的 token 数
    parts, usage = [], empty_usage()
    usage["model"] = model
    for _, data in _sse(f"{base}/chat/completions", headers, payload):
        for ch in data.get("choices") or []:
            piece = (ch.get("delta") or {}).get("content") or ""
            if piece:
                parts.append(piece)
                on_delta(piece)
        raw = data.get("usage") or {}
        if raw:
            usage["input_tokens"] = int(raw.get("prompt_tokens") or 0)
            usage["output_tokens"] = int(raw.get("completion_tokens") or 0)
            usage["cache_read_tokens"] = int((raw.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    return "".join(parts), usage


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
        detail = e.read().decode("utf-8", "replace")[:800]
        raise SystemExit(f"LLM 请求失败 HTTP {e.code}（{url}）：\n{detail}")
    except urllib.error.URLError as e:
        raise SystemExit(f"LLM 连接失败（{url}）：{e.reason}")
