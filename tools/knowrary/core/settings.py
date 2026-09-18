"""偏好设置（`.knowrary/settings.json`）：**会改变程序行为、而且后端也要读的那些开关**。

和 localStorage 的分界是故意的：「这台机器上怎么看图」（主题、对齐吸附、小地图、周边一跳）
留在浏览器里，换台机器本来就该各看各的；而「复习和出题要不要出现」得后端也知道——
教练的系统提示词是服务端拼的，开关只存在浏览器里的话，界面安静了，教练照样每轮
开场看 today、结尾出 check 题。所以它跟着 vault 走。

它和 md / index / layout / review / pending 并列，是第六份契约。**只存开关，不存数据**：
复习记录照常积累在 review-log 里，关掉的只是"要不要拿它来烦人"。
"""
from __future__ import annotations

from pathlib import Path

from .mdio import load_json, write_json_atomic

SCHEMA_VERSION = 1

# 开关全集：名字 → 默认值。**默认全开**——新装一个 vault 该有完整体验，
# 关掉是明确的选择。加开关只动这张表，前后端都从它派生。
DEFAULTS = {
    "review_enabled": True,     # 总闸：关掉后复习 / 出题在界面和教练那儿都不再主动出现
    "review_brief": True,       # 晨间简报
    "review_marks": True,       # 画布上的到期金点、活动栏「今日」角标
}


def settings_path(vault: Path) -> Path:
    return vault / ".knowrary" / "settings.json"


def empty_settings() -> dict:
    return {"schema_version": SCHEMA_VERSION, **DEFAULTS}


def load_settings(vault: Path) -> dict:
    """读设置。文件不在、读坏了、字段缺了，一律回落到默认——
    **设置文件坏掉不该让整个库打不开**，最差也就是回到全开的初始体验。"""
    path = settings_path(vault)
    if not path.exists():
        return empty_settings()
    try:
        doc = load_json(path) or {}
    except (ValueError, OSError):     # 手改坏了 / 读不动：回落默认，别让整个库打不开
        return empty_settings()
    if not isinstance(doc, dict):
        return empty_settings()
    out = empty_settings()
    for key in DEFAULTS:
        if isinstance(doc.get(key), bool):
            out[key] = doc[key]
    return out


def save_settings(vault: Path, patch: dict) -> dict:
    """合并写回：只认 DEFAULTS 里登记过的键，别的静默忽略（前端加字段忘了加默认值时，
    让它明确地不生效，而不是悄悄存进一份谁也不认的文件）。"""
    doc = load_settings(vault)
    for key, value in (patch or {}).items():
        if key in DEFAULTS and isinstance(value, bool):
            doc[key] = value
    write_json_atomic(settings_path(vault), doc)
    return doc


def review_on(vault: Path) -> bool:
    """复习这一整套现在该不该出现。总闸关了，下面几个子开关一律当关。"""
    return bool(load_settings(vault).get("review_enabled"))
