"""认证 / 欠费熔断（2026-09-30，架构提升点 #15）。

401 / 402 / 403 本来就不重试，但下一次请求照样发出去、照样失败：批量导入时每一篇文章都要
再撞一次同一个错，出错流水里刷满同样的记录。这里记下「这个 provider 的密钥被拒了 / 欠费了」，
之后的调用不发请求直接报，**配置文件一改（保存新密钥、换 provider）就自动解除**——修好本来就得改配置。

只在进程内存里记：单用户改完配置自然就恢复了，重启也会恢复，不需要持久化。
「测试连接」那条路不走这里：试新密钥不该被旧的熔断挡住。
"""
from __future__ import annotations

import re
import threading

from .paths import core  # noqa: F401  （副作用：把 tools/knowrary/ 注入 sys.path）

import llm_backend  # noqa: E402

HALT_KINDS = ("auth", "billing")
_LOCK = threading.Lock()
_DOWN: dict[str, dict] = {}          # provider 名 → {kind, code, stamp}


def _stamp() -> tuple | None:
    """配置文件现在的样子：mtime + 大小。变了就说明人改过配置。"""
    path = llm_backend.config_path()
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def hint(name: str, kind: str, code: str = "") -> str:
    """给人看的下一步：别只甩一段原始 HTTP 报错。"""
    tag = f"（{code}）" if code else ""
    if kind == "billing":
        return f"provider `{name}` 余额不足或欠费{tag}：去充值，或者在 设置 → 模型 里给这个角色换一个 provider。"
    return f"provider `{name}` 的密钥被拒了{tag}：去 设置 → 模型 里改密钥。保存之后自动恢复，不用重启。"


def check(name: str) -> str | None:
    """这个 provider 还熔断着吗？是的话返回要报给人的那句话；配置改过了就解除。"""
    with _LOCK:
        down = _DOWN.get(name)
        if not down:
            return None
        if down["stamp"] != _stamp():
            _DOWN.clear()                # 配置改了：所有 provider 都给一次机会
            return None
        return "这次没有发请求——" + hint(name, down["kind"], down["code"])


def trip(name: str, exc: BaseException) -> str | None:
    """这次失败是认证 / 欠费就熔断，返回提示；别的失败不管，返回 None。"""
    kind = getattr(exc, "kind", "")
    if kind not in HALT_KINDS:
        return None
    m = re.search(r"HTTP (\d{3})", str(exc))
    code = f"HTTP {m.group(1)}" if m else ""
    with _LOCK:
        _DOWN[name] = {"kind": kind, "code": code, "stamp": _stamp()}
    return hint(name, kind, code)


def reset() -> None:
    with _LOCK:
        _DOWN.clear()
