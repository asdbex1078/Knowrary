"""写入审核的第二段：review 角色在落盘前看一眼（2026-09-22）。

**三层，从硬到软**（第一层第二层见 `core/audit.py` 的模块注释）：
硬拒（`writer.plan`，永远在）→ 确定性检查（`core.audit_precheck`，永远跑，开关只决定挡不挡）
→ 这一段（花钱、要等，`settings.audit_enabled` 说了算）。

两条纪律：

1. **能算的不要问。** 类型在不在表里、链接指不指得到，代码算得出来，问模型既慢又不稳。
   所以第一段的结果是喂给这一段的**输入**，提示词里明说了不要重复报。
2. **审核只审"写什么"，不审"怎么写回"。** 它看的是 `edit.after` 里的内容，
   不看 ChangeSet 的机械结构——后者是写回层的事，它已经把不合法的挡在外面了。

强制写入（`force`）**跳过这一段但不跳过第一段**：你要绕开的是模型的判断，
不是那些算得出来的事实。每次强制都记一条进 `issues.jsonl`——
不然"审核被绕过了多少次"没有任何证据，这个开关就是摆设。
"""
from __future__ import annotations

from pathlib import Path

from .contracts import AuditIssue, AuditReport
from .llm_call import ask, parse_json
from .paths import core

# 只有"往正文里写东西"才值得花一次调用。加一条边、改一个 year 不审——
# 每加一条边都卡几秒的话，两天之内这个开关就会被关掉（F10.5 同一个道理：
# 一个总被绕开的规矩，比没有规矩更糟）。
CONTENT_TYPES = ("create_node", "update_body", "append_body")
MAX_DIFF_CHARS = 12000     # 一次审核最多给模型看这么多字：再多就该拆成几次写入
MAX_CONTEXT = 12           # 给多少个相关的已有节点当对照


def _prompt() -> str:
    path = Path(__file__).resolve().parents[1] / "tools" / "knowrary" / "prompts" / "audit.md"
    return path.read_text(encoding="utf-8")


def has_content(changes: list[dict]) -> bool:
    return any(c.get("type") in CONTENT_TYPES for c in changes)


def _diffs(edits: list, budget: int = MAX_DIFF_CHARS) -> str:
    """给模型看的是**写完之后的样子**，不是 unified diff。

    diff 里一半是上下文行和 `@@` 头，模型要先在脑子里把它还原成文章才能判对错——
    那一步既费 token 又容易还原错。审的是内容，就直接给内容。
    """
    out, used = [], 0
    for edit in edits:
        if not edit.changed:
            continue
        text = (edit.after or "").strip()
        if used + len(text) > budget:
            text = text[: max(0, budget - used)] + "\n…（太长，截断了）"
        out.append(f"### {edit.rel}\n\n{text}")
        used += len(text)
        if used >= budget:
            out.append("（还有几个文件没给：这一次要写的东西已经超过一次审核的预算，"
                       "建议拆成几批写）")
            break
    return "\n\n".join(out) or "（没有文件变化）"


def _context(index: dict, edits: list) -> str:
    """相关的已有节点：这次动到的那些点的一跳邻居 + 同领域。

    给对照是为了让"和库里已有内容冲突"这一类判得出来——没有对照，
    模型只能凭自己的世界知识，那正是它最容易编的地方。
    """
    touched = {(e.rel or "").rsplit("/", 1)[-1][:-3] for e in edits if (e.rel or "").endswith(".md")}
    by_id = {n["id"]: n for n in index.get("nodes", []) if not n.get("virtual")}
    edges = {e["id"]: e for e in index.get("edges", [])}
    near: dict[str, dict] = {}
    for nid in touched:
        node = by_id.get(nid)
        if not node:
            continue
        for eid in list(node.get("out") or []) + list(node.get("in") or []):
            edge = edges.get(eid)
            for other in ((edge or {}).get("source"), (edge or {}).get("target")):
                if other and other != nid and other in by_id:
                    near[other] = by_id[other]
    if not near:                       # 全是新点、还没有邻居：退回同领域的几个
        fields = {by_id[i].get("field") for i in touched if i in by_id}
        for node in by_id.values():
            if node.get("field") in fields and node["id"] not in touched:
                near[node["id"]] = node
    rows = [f"- {n['id']}｜{n.get('desc') or ''}｜year={n.get('year') or '—'}"
            for n in list(near.values())[:MAX_CONTEXT]]
    return "\n".join(rows) or "（没有相关的已有节点）"


def _precheck_text(issues: list[dict]) -> str:
    if not issues:
        return "（确定性检查没报任何问题）"
    return "\n".join(f"- [{i['code']}] {i['path']}：{i['message']}" for i in issues)


def _as_issue(raw: dict, fallback_path: str) -> AuditIssue | None:
    what = str(raw.get("what") or "").strip()
    if not what:
        return None
    sev = str(raw.get("severity") or "warn").strip()
    return AuditIssue(level="block" if sev == "block" else "warn", code="llm",
                      path=str(raw.get("path") or fallback_path).strip(),
                      message=what, why=str(raw.get("why") or "").strip(),
                      fix=str(raw.get("fix") or "").strip())


def review(vault: Path, index: dict, edits: list, precheck: list[dict]) -> AuditReport:
    """问一次 review 角色。模型抽风（非 JSON、空回答）时**放行**并说明白。

    为什么抽风要放行而不是挡下：挡下意味着"模型不回话 = 你写不了东西"，
    那是把可用性押在一次网络请求上。审核是护栏，不是闸门总开关。
    """
    prompt = (_prompt().replace("{{diffs}}", _diffs(edits))
              .replace("{{precheck}}", _precheck_text(precheck))
              .replace("{{context}}", _context(index, edits)))
    raw = ask(vault, "review", prompt, op="audit")
    data = parse_json(raw, "audit", vault=vault)
    if not data:
        return AuditReport(checked=True, verdict="pass", summary="审核模型没给出可用的结论，这一次放行",
                           issues=[i for i in _wrap(precheck)], model_failed=True)
    first = (edits[0].rel if edits else "")
    llm = [x for x in (_as_issue(r, first) for r in (data.get("issues") or [])) if x]
    verdict = str(data.get("verdict") or "").strip()
    if verdict not in ("pass", "warn", "block"):
        verdict = "block" if any(i.level == "block" for i in llm) else ("warn" if llm else "pass")
    # 模型说 block、却一条具体问题都没给：这种"结论没有依据"的挡不作数，降成 warn。
    # 否则一句"我觉得不太对"就能把人挡在门外，而人连改哪儿都不知道。
    if verdict == "block" and not any(i.level == "block" for i in llm):
        verdict = "warn" if llm else "pass"
    return AuditReport(checked=True, verdict=verdict,
                       summary=str(data.get("summary") or "").strip(),
                       issues=_wrap(precheck) + llm)


def _wrap(precheck: list[dict]) -> list[AuditIssue]:
    return [AuditIssue(level=i["level"], code=i["code"], path=i["path"],
                       message=i["message"], fix=i.get("fix") or "") for i in precheck]


def preview(vault: Path, index: dict, edits: list) -> AuditReport:
    """预览阶段（dry_run）只跑第一段。

    **不在预览时问模型**：预览是人点「重算 diff」、改一行摘要就会重来一次的操作，
    每次都烧一次调用没道理。模型那一段留到真按下写入的那一下——
    那时人已经决定要写了，等几秒是值的。
    """
    return AuditReport(checked=False, verdict="pass", issues=_wrap(core.audit_precheck(vault, index, edits)))


def gate(vault: Path, index: dict, edits: list, changes: list[dict], force: bool) -> AuditReport:
    """落盘前的总闸。返回的报告**永远带着第一段的结果**，开关只决定要不要问模型、挡不挡。

    调用方按 `report.blocked` 决定写不写；`blocked` 为真时不要落盘，把报告交给人。
    """
    precheck = core.audit_precheck(vault, index, edits)
    if not core.audit_on():
        return AuditReport(checked=False, verdict="pass", issues=_wrap(precheck),
                           summary="写入审核没开：确定性检查照常跑，只是不拦路")
    if force:
        core.record_issue(vault, "audit", "审核被强制跳过", where=",".join(
            e.rel for e in edits if e.changed)[:200])
        return AuditReport(checked=False, forced=True, verdict="pass", issues=_wrap(precheck),
                           summary="这一次是强制写入：没问模型，确定性检查的结果照常留在这儿")
    if not has_content(changes):
        return AuditReport(checked=False, verdict="pass", issues=_wrap(precheck),
                           summary="这次没有正文改动（只动了关系或字段），不值得单开一次审核")
    try:
        return review(vault, index, edits, precheck)
    except Exception as exc:                                    # noqa: BLE001 —— 见下
        # 审核这一段挂了不该让写入也挂：它是护栏，不是闸门总开关。
        # 但要留痕，否则"审核其实一直在报错"会安静地演变成"审核形同虚设"。
        core.record_issue(vault, "audit", f"审核调用失败：{exc}", where="/api/changes")
        return AuditReport(checked=False, verdict="pass", issues=_wrap(precheck), model_failed=True,
                           summary=f"审核没跑成（{exc}），这一次放行")
