"""按审核意见改一版（2026-09-23）：审核报了意见之后，卡上除了「原样写入」和自己动手改，
多一条路——把勾选的意见交给 learn 角色，让它改一版摆回卡上。

几条口径：

1. **交给 learn，不交给 review。** 审校的提示词写死了"不写稿"；让它自己改，
   下一轮审核就成了自己审自己。
2. **只改卡，不写盘。** 改完停在卡上给人看改了哪儿（`delta`），人点写入时照常重审——
   改稿引入的新错，还得有人看一眼。
3. **改出来的必须过写回层的校验**（`curation.preview`）：模型改坏了结构就直接报错，
   不把一张写不进去的卡摆回去。
4. **拿不准的意见模型可以不改**，但得说出来（`skipped`）：硬改比不改更糟。
"""
from __future__ import annotations

import difflib
import json
from pathlib import Path

from . import curation
from .contracts import AuditIssue, Change, ReviseResult, ReviseSkip
from .llm_call import ask_meta, parse_json
from .paths import core


class ReviseFailed(Exception):
    """模型没交回一份能用的改法。`meta` 是那次调用的回执（谁改的、改了多久）：
    钱已经花了，卡上得说清楚是哪个模型花了多久、卡在哪一步。"""

    def __init__(self, message: str, meta: dict | None = None):
        super().__init__(message)
        self.meta = meta or {}


def _prompt() -> str:
    path = Path(__file__).resolve().parents[1] / "tools" / "knowrary" / "prompts" / "revise.md"
    return path.read_text(encoding="utf-8")


def _issues_text(issues: list[AuditIssue]) -> str:
    rows = []
    for k, it in enumerate(issues, 1):
        row = f"{k}. [{'必须改' if it.level == 'block' else '提醒'}] {it.path or '（没指明文件）'}：{it.message}"
        if it.why:
            row += f"\n   依据：{it.why}"
        if it.fix:
            row += f"\n   建议改法：{it.fix}"
        rows.append(row)
    return "\n".join(rows)


KIND = {"create_node": "新建", "update_body": "整篇替换", "append_body": "尾部追加",
        "update_frontmatter": "改字段", "set_fact": "速查", "move_node": "挪位置"}


def _render(changes: list[dict]) -> list[str]:
    """一份改法摊成给人读的行，前后两份一比就知道 AI 动了哪儿。
    不直接 diff JSON：正文在 JSON 里是一整行带 `\\n` 的字符串，改一个字整段都标红。"""
    out: list[str] = []
    for ch in changes:
        kind, src = ch.get("type"), ch.get("source")
        if kind in ("add_edge", "remove_edge", "update_edge"):
            verb = {"add_edge": "加边", "remove_edge": "删边", "update_edge": "改边"}[kind]
            out.append(f"【{verb}】{src} —{ch.get('relation')}→ {ch.get('target')}")
            continue
        out.append(f"【{KIND.get(kind, kind)}】{src}")
        for key, val in (ch.get("fields") or {}).items():
            out.append(f"  {key}：{val}")
        for key in ("key", "value", "path", "target", "relation"):
            if ch.get(key):
                out.append(f"  {key}：{ch[key]}")
        if ch.get("body"):
            out.extend(str(ch["body"]).splitlines())
        out.append("")
    return out


def delta(before: list[dict], after: list[dict]) -> str:
    lines = difflib.unified_diff(_render(before), _render(after), fromfile="改前", tofile="改后",
                                 lineterm="", n=2)
    return "\n".join(lines)


def run(vault: Path, index: dict, changes: list[dict], issues: list[AuditIssue]) -> ReviseResult:
    prompt = (_prompt().replace("{{changes}}", json.dumps(changes, ensure_ascii=False, indent=2))
              .replace("{{issues}}", _issues_text(issues)))
    raw, meta = ask_meta(vault, "learn", prompt, op="revise")
    data = parse_json(raw, "revise", vault=vault)
    got = data.get("changes") if isinstance(data, dict) else None
    if not isinstance(got, list) or not got:
        raise ReviseFailed("模型没交回改好的改法（回答不是预期的 JSON）", meta)
    try:
        after = [Change.model_validate(c).model_dump(exclude_none=True) for c in got]
    except ValueError as exc:
        raise ReviseFailed(f"模型交回的改法结构不对：{exc}", meta) from exc
    try:
        files = curation.preview(vault, after, index)
    except (core.ChangeRejected, core.WriteConflict) as exc:
        raise ReviseFailed(f"改出来的版本过不了写回校验：{exc}", meta) from exc
    skipped = [ReviseSkip(what=str(s.get("what") or "").strip(), why=str(s.get("why") or "").strip())
               for s in (data.get("skipped") or []) if isinstance(s, dict) and s.get("what")]
    return ReviseResult(changes=after, files=files, delta=delta(changes, after),
                        summary=str(data.get("summary") or "").strip(), skipped=skipped,
                        before=changes, **meta)
