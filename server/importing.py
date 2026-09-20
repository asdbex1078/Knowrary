"""导入：方案 JSON → 新建节点 / 补充老节点 / 待审边。

翻译在 core.translate；写回复用 /api/changes 那条链路（core.plan → diff → core.commit），
所以这里没有第二套 Markdown 写法。落盘时顺手做三件只有服务端知道时机的事：
记待审边、存方案存档、让索引缓存作废。
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import os

from . import curation
from .contracts import (FileDiff, ImportClaim, ImportProposal, ImportProposeRequest, ImportRequest, ImportResult,
                        PendingEdge, SourceFile, SourceText, SourcesRead)
from .index_service import current_index, invalidate
from .llm_call import ask
from .paths import core

MAX_SOURCE_BYTES = 1_000_000
SOURCE_SUFFIXES = (".md", ".markdown", ".txt")
# 素材只可能在这些目录之外：节点与领域总览本身就是图谱，机器目录和代码目录里没有笔记
SKIP_DIRS = {"nodes", "fields", ".knowrary", ".git", ".claude", ".obsidian", ".venv", "node_modules",
             "web", "server", "tools", "harness", "assets", "__pycache__"}


class ImportRejected(Exception):
    """方案本身写不进去：id 不合法、目标文件被外部改过等。"""


class StaleIndex(Exception):
    def __init__(self, current: int) -> None:
        super().__init__(f"索引已更新（当前 revision {current}）")
        self.current = current


def run(vault: Path, req: ImportRequest) -> ImportResult:
    index = current_index(vault)
    if req.base_revision is not None and req.base_revision != index["revision"]:
        raise StaleIndex(index["revision"])
    target = core.ImportTarget(req.field.strip(), req.source.strip(), (req.folder or "").strip() or None)
    # 先提升再改名：卡上待审边的 key 是按**卡上显示的 id** 拼的，改名之后就对不上了
    plan = core.promote_in_plan(req.plan, req.promote)
    for old_id, new_id in req.renames.items():
        plan = core.rename_in_plan(plan, old_id, new_id)
    tr = core.translate(plan, index, core.load_relation_types(vault), target)
    try:
        edits = core.plan(vault, tr.changes, index)
    except (core.ChangeRejected, core.WriteConflict) as exc:
        core.record_issue(vault, "write", str(exc), where="/api/import")
        raise ImportRejected(str(exc)) from exc

    files = [FileDiff(path=e.rel, notes=e.notes, diff=curation.diff_of(e)) for e in edits]
    pending = [PendingEdge(**{k: v for k, v in p.items() if k in PendingEdge.model_fields}) for p in tr.pending]
    base = dict(files=files, pending=pending, warnings=tr.warnings, counts=tr.counts(), summary=tr.summary)
    if req.dry_run:
        return ImportResult(applied=False, index_revision=index["revision"], **base)

    snapshot = core.commit(vault, edits) if edits else ""
    origin = {"source": target.source, "imported_at": target.date}
    if tr.pending:
        core.add_pending(vault, tr.pending, origin)
    home = plan.get("suggest_home")
    if isinstance(home, dict):
        # 只记这次真建出来、又被模型点名孤立的那几个；Inbox 上显示，不自动建域 / 建项目
        lonely = [i for i in (home.get("isolated") or []) if i in tr.new_ids]
        core.add_home(vault, lonely, home, origin)
    log = _archive(vault, plan, tr, target)
    invalidate(vault)
    return ImportResult(applied=True, backup=snapshot or None, log=log,
                        index_revision=current_index(vault)["revision"], **base)


def _archive(vault: Path, plan: dict, tr, target) -> str:
    """方案存档到 .knowrary/imports/：和 CLI 同一个位置、同一个文件名规则。"""
    stem = re.sub(r"[^\w一-鿿-]+", "-", target.source)[:60]
    rel = f".knowrary/imports/{dt.date.today().isoformat()}-{stem}.json"
    core.write(vault / rel, json.dumps({"plan": plan, "changes": tr.changes, "pending": tr.pending,
                                        "warnings": tr.warnings}, ensure_ascii=False, indent=2))
    return rel


# ---------------------------------------------------------------- 文章 → 方案（一次 LLM 调用）

def propose(vault: Path, req: ImportProposeRequest) -> ImportProposal:
    """文章 → 方案 → 顺手 dry-run。三级匹配在这里算完：
    先对当前项目里没建的点（认领幽灵），再看方案连到全局哪些已有节点，两边都沾不上的标孤立。"""
    text = _article_text(vault, req)
    index = current_index(vault)
    rt = core.load_relation_types(vault)
    points = core.project_points(vault, index, req.project)
    prompt = core.build_article_prompt(core.cards_from_index(index), rt, text, req.field.strip(), points)
    raw = ask(vault, "learn", prompt, op="import")
    plan = core.parse_json(raw, f"import source={req.source}", vault=vault)
    if not isinstance(plan, dict) or not plan.get("nodes"):
        raise ImportRejected("模型没给出可用的方案（没有 nodes）。原文已记进问题流，换个模型或缩短文章再试")

    plan, raw_claims = core.normalize_claims(plan, points, core.rename_in_plan)
    claims = [ImportClaim(**c) for c in raw_claims]
    preview = run(vault, ImportRequest(plan=plan, field=req.field, source=req.source, folder=req.folder,
                                       dry_run=True))
    claimed = {c.node_id for c in claims}
    near = [ImportClaim(**c) for c in core.near_misses(plan, points, claimed)]
    isolated = core.isolated(plan, [p.model_dump() for p in preview.pending], index, claimed)
    home = plan.get("suggest_home") if isinstance(plan.get("suggest_home"), dict) else None
    return ImportProposal(plan=plan, preview=preview, claims=claims, near_misses=near, isolated=isolated,
                          suggest_home=home, project_points=len(points), prompt_chars=len(prompt))


def _article_text(vault: Path, req: ImportProposeRequest) -> str:
    text = (req.text or "").strip()
    if not text and req.file:
        text = read_source(vault, req.file).text.strip()
    if not text:
        raise ImportRejected("没有文章：粘贴正文，或选一个 vault 里的文件")
    try:
        return core.check_length(text)
    except ValueError as exc:
        raise ImportRejected(str(exc)) from exc


# ---------------------------------------------------------------- 素材源：vault 里已有的笔记

def sources(vault: Path) -> SourcesRead:
    """vault 里可以当导入素材的 md / txt：节点目录、机器目录、代码目录之外的全部。按改动时间倒序。"""
    files: list[SourceFile] = []
    for root, dirs, names in os.walk(vault):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in names:
            if not name.lower().endswith(SOURCE_SUFFIXES):
                continue
            path = Path(root) / name
            try:
                st = path.stat()
            except OSError:
                continue
            files.append(SourceFile(path=path.relative_to(vault).as_posix(), name=name, size=st.st_size,
                                    modified=dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")))
    files.sort(key=lambda f: f.modified, reverse=True)
    return SourcesRead(files=files[:500])


def read_source(vault: Path, rel: str) -> SourceText:
    """读一个素材文件。路径必须落在 vault 里、在允许的目录、后缀对、不太大——一个 `..` 都不放过。"""
    clean = rel.strip().lstrip("/")
    path = (vault / clean)
    try:
        resolved = path.resolve()
        resolved.relative_to(vault.resolve())
    except (ValueError, OSError) as exc:
        raise ImportRejected(f"路径不在 vault 里：{rel}") from exc
    parts = resolved.relative_to(vault.resolve()).parts
    if not parts or parts[0] in SKIP_DIRS or any(p.startswith(".") for p in parts[:-1]):
        raise ImportRejected(f"这个目录不是素材目录：{rel}")
    if not resolved.name.lower().endswith(SOURCE_SUFFIXES):
        raise ImportRejected(f"只认 md / txt：{rel}")
    if not resolved.is_file():
        raise ImportRejected(f"文件不存在：{rel}")
    if resolved.stat().st_size > MAX_SOURCE_BYTES:
        raise ImportRejected(f"文件太大（{resolved.stat().st_size} 字节）")
    return SourceText(path=clean, text=core.read(resolved))
