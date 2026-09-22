"""结构化读取与保存 LLM 配置，给设置页使用。"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from .paths import core
import llm_backend

NAME_RE = re.compile(r"^[^\s/\\:]+$")
EDITABLE_KEYS = ("type", "model", "base_url", "api_key", "max_tokens", "temperature")
PREV_NAME = "prev_name"      # 改名时前端带上的旧名字，用来把密钥认回来（不落盘）
REQUIRED_ROLES = ("learn", "review")


class LLMConfigRejected(ValueError):
    pass


def _real_keys(section: dict) -> list[str]:
    return [str(k) for k in section if not str(k).startswith("_")]


def _source() -> tuple[dict, Path | None]:
    try:
        return llm_backend.load_config()
    except llm_backend.LLMConfigError as exc:
        raise LLMConfigRejected(str(exc)) from exc


def _provider_read(name: str, provider: dict) -> dict:
    return {"name": name, "type": provider["type"], "model": provider.get("model"),
            "base_url": provider.get("base_url"),
            "api_key_set": bool(provider.get("api_key")),
            "max_tokens": provider.get("max_tokens"), "temperature": provider.get("temperature")}


def read() -> dict:
    """模型配置**只有一份**，所有知识库共用，所以这里不再需要知道当前是哪个库。"""
    cfg, path = _source()
    providers = cfg.get("providers") or {}
    roles = cfg.get("roles") or {}
    return {"path": str(path or llm_backend.config_path()), "exists": bool(path and path.exists()),
            "providers": [_provider_read(name, providers[name]) for name in _real_keys(providers)],
            "roles": {name: roles[name] for name in _real_keys(roles)},
            "required_roles": list(REQUIRED_ROLES), "provider_types": list(llm_backend.TYPES)}


def _validate_name(name: str, label: str) -> str:
    if not isinstance(name, str) or not name.strip() or not NAME_RE.fullmatch(name):
        raise LLMConfigRejected(f"{label} 名称不能为空，且不能包含空白或路径字符")
    return name.strip()


def _build(payload: dict) -> dict:
    old, _ = _source()
    old_providers = old.get("providers") or {}
    rows = payload.get("providers")
    roles = payload.get("roles")
    if not isinstance(rows, list) or not rows:
        raise LLMConfigRejected("至少需要一个 provider")
    if not isinstance(roles, dict):
        raise LLMConfigRejected("roles 必须是对象")
    providers = {}
    for row in rows:
        if not isinstance(row, dict):
            raise LLMConfigRejected("provider 条目格式不正确")
        name = _validate_name(row.get("name"), "provider")
        if name in providers:
            raise LLMConfigRejected(f"provider `{name}` 重复")
        kind = row.get("type")
        if kind not in llm_backend.TYPES:
            raise LLMConfigRejected(f"provider `{name}` 的类型不受支持")
        if kind == "openai" and not str(row.get("base_url") or "").strip():
            raise LLMConfigRejected(f"provider `{name}` 缺少 base_url")
        # 密钥留空表示"保持原值"，而原值要按**旧名字**去找：只按新名字找的话，
        # 改一次名字密钥就静默消失（2026-09-22 真把一份线上密钥冲掉了）。
        prev = row.get(PREV_NAME) or name
        old_row = next((old_providers[k] for k in (name, prev)
                        if isinstance(old_providers.get(k), dict)), {})
        provider = {k: old_row[k] for k in old_row if str(k).startswith("_")}
        for key in EDITABLE_KEYS:
            if key not in row:
                continue
            value = row[key]
            if key == "api_key" and not str(value or "").strip():
                if old_row.get("api_key"):
                    provider[key] = old_row["api_key"]
                continue
            if value is not None and value != "":
                provider[key] = value
        provider["type"] = kind
        providers[name] = provider
    clean_roles = {}
    for role, provider in roles.items():
        role = _validate_name(role, "role")
        if not isinstance(provider, str) or provider not in providers:
            raise LLMConfigRejected(f"角色 `{role}` 指向不存在的 provider `{provider}`")
        clean_roles[role] = provider
    for role in REQUIRED_ROLES:
        clean_roles.setdefault(role, next(iter(providers)))
    cfg = {k: old[k] for k in old if str(k).startswith("_")}
    cfg["providers"] = providers
    cfg["roles"] = {k: old["roles"][k] for k in (old.get("roles") or {}) if str(k).startswith("_")}
    cfg["roles"].update(clean_roles)
    return cfg


def build_test_config(provider: dict) -> dict:
    """把单个未保存 provider 合成临时配置，复用保存时的密钥保留规则。"""
    name = provider.get("name")
    return _build({"providers": [provider], "roles": {"test": name}})


def write(payload: dict) -> dict:
    cfg = _build(payload)
    path = llm_backend.config_path()
    if path.exists():
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        # 备份放在配置文件自己旁边：用户级那份不该按"当时切着哪个库"散落到各个库里
        backup = path.parent / "backup" / f"llm-config-{stamp}" / path.name
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(path.read_bytes())
    core.write_json_atomic(path, cfg)
    return read()
