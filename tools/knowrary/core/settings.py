"""偏好设置（`~/.knowrary/settings.json`）：**会改变程序行为、而且后端也要读的那些开关**。

和 localStorage 的分界是故意的：「这台机器上怎么看图」（主题、对齐吸附、小地图、周边一跳）
留在浏览器里，换台机器本来就该各看各的；而「复习和出题要不要出现」得后端也知道——
教练的系统提示词是服务端拼的，开关只存在浏览器里的话，界面安静了，教练照样每轮
开场看 today、结尾出 check 题。

**2026-09-22 从库里挪到了用户级**：一个人有好几个库（自己的、参考的、示例的），
"别在聊天里考我"这种偏好不该换个库就得重设一遍，更不该跟着参考库进别人的仓库。
**只存开关，不存数据**：复习记录照常积累在各个库的 review-log 里，关掉的只是"要不要拿它来烦人"。
"""
from __future__ import annotations

from pathlib import Path

from .mdio import load_json, user_dir, write_json_atomic

SCHEMA_VERSION = 1

# 开关全集：名字 → 默认值。**默认全开**——新装一个 vault 该有完整体验，
# 关掉是明确的选择。加开关只动这张表，前后端都从它派生。
DEFAULTS = {
    "review_enabled": True,     # 总闸：复习这一整套在不在（今日面板的错题与到期、日历、金点）
    "review_in_chat": True,     # 教练会不会考我 / 催我。**和总闸分开**，见 review_in_chat()
    "review_brief": True,       # 晨间简报
    "review_marks": True,       # 画布上的到期金点、活动栏「今日」角标
}


def settings_path() -> Path:
    return user_dir() / "settings.json"


def empty_settings() -> dict:
    return {"schema_version": SCHEMA_VERSION, **DEFAULTS}


def load_settings() -> dict:
    """读设置。文件不在、读坏了、字段缺了，一律回落到默认——
    **设置文件坏掉不该让整个库打不开**，最差也就是回到全开的初始体验。"""
    path = settings_path()
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


def save_settings(patch: dict) -> dict:
    """合并写回：只认 DEFAULTS 里登记过的键，别的静默忽略（前端加字段忘了加默认值时，
    让它明确地不生效，而不是悄悄存进一份谁也不认的文件）。"""
    doc = load_settings()
    for key, value in (patch or {}).items():
        if key in DEFAULTS and isinstance(value, bool):
            doc[key] = value
    write_json_atomic(settings_path(), doc)
    return doc


def review_on() -> bool:
    """复习这一整套现在该不该出现。总闸关了，下面几个子开关一律当关。"""
    return bool(load_settings().get("review_enabled"))


def review_in_chat() -> bool:
    """**教练那一侧**该不该出题、催进度。

    为什么要和总闸分开：这两件事原来是一个开关，于是"别在聊天里考我"只能靠关总闸达成，
    而总闸一关，今日面板里的错题与到期**也跟着被摘掉**——想专心复习的那个地方恰好空了
    （2026-09-19 复盘核出来的：设置在 09-18 21:15 关掉，此后今日面板就没有复习区了）。
    现在总闸只管"这套功能在不在"，聊天里考不考是它下面的一档。
    """
    doc = load_settings()
    return bool(doc.get("review_enabled")) and bool(doc.get("review_in_chat"))
