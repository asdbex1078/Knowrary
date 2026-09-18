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
from difflib import SequenceMatcher

from . import curation
from .contracts import (FileDiff, ImportClaim, ImportProposal, ImportProposeRequest, ImportRequest, ImportResult,
                        PendingEdge, SourceFile, SourceText, SourcesRead)
from .index_service import current_index, invalidate
from .llm_call import ask, parse_json
from .paths import core

MAX_ARTICLE_CHARS = 80_000        # 一篇文章的上限：再长就该先切（批量那一步的事），别一口气塞给模型
MAX_SOURCE_BYTES = 1_000_000
SOURCE_SUFFIXES = (".md", ".markdown", ".txt")
# 素材只可能在这些目录之外：节点与领域总览本身就是图谱，机器目录和代码目录里没有笔记
SKIP_DIRS = {"nodes", "fields", ".knowrary", ".git", ".claude", ".obsidian", ".venv", "node_modules",
             "web", "web3d", "server", "tools", "harness", "assets", "__pycache__"}
NEAR_MISS_RATIO = 0.6


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
    points = project_points(vault, index, req.project)
    prompt = core.build_article_prompt(core.cards_from_index(index), rt, text, req.field.strip(), points)
    raw = ask(vault, "learn", prompt, op="import")
    plan = parse_json(raw, f"import source={req.source}", vault=vault)
    if not isinstance(plan, dict) or not plan.get("nodes"):
        raise ImportRejected("模型没给出可用的方案（没有 nodes）。原文已记进问题流，换个模型或缩短文章再试")

    plan, claims = _normalize_claims(plan, points)
    preview = run(vault, ImportRequest(plan=plan, field=req.field, source=req.source, folder=req.folder,
                                       dry_run=True))
    near = _near_misses(plan, points, {c.node_id for c in claims})
    isolated = _isolated(plan, preview, index, {c.node_id for c in claims})
    home = plan.get("suggest_home") if isinstance(plan.get("suggest_home"), dict) else None
    return ImportProposal(plan=plan, preview=preview, claims=claims, near_misses=near, isolated=isolated,
                          suggest_home=home, project_points=len(points), prompt_chars=len(prompt))


def _article_text(vault: Path, req: ImportProposeRequest) -> str:
    text = (req.text or "").strip()
    if not text and req.file:
        text = read_source(vault, req.file).text.strip()
    if not text:
        raise ImportRejected("没有文章：粘贴正文，或选一个 vault 里的文件")
    if len(text) > MAX_ARTICLE_CHARS:
        raise ImportRejected(f"文章太长（{len(text)} 字，上限 {MAX_ARTICLE_CHARS}）：先切成几段，一段一段导")
    return text


def project_points(vault: Path, index: dict, project: str | None) -> list[dict]:
    """当前项目清单里**还没建**的点：id / 名字 / 为什么学。没选项目就没有待认领。"""
    if not project:
        return []
    pr = (core.load_projects(vault).get("projects") or {}).get(project)
    if not pr:
        return []
    real = {n["id"] for n in index["nodes"] if not n.get("virtual")}
    out, seen = [], set()
    for ls in core.lists_of(pr):
        for stage in ls.get("stages") or []:
            for pt in stage.get("points") or []:
                pid = pt.get("id")
                if pid and pid not in real and pid not in seen:
                    seen.add(pid)
                    out.append({"id": pid, "name": pt.get("name") or pid, "why": pt.get("why") or ""})
    return out


def _normalize_claims(plan: dict, points: list[dict]) -> tuple[dict, list[ImportClaim]]:
    """模型写了 `claims` 的节点：id 直接换成清单里的 id（连带关系、链接）。id 本来就等于清单 id 的也算认领。"""
    by_id = {p["id"]: p for p in points}
    claims: list[ImportClaim] = []
    for n in list(plan.get("nodes") or []):
        nid = str(n.get("id") or "")
        want = str(n.get("claims") or "").strip()
        if want and want in by_id and want != nid:
            plan = core.rename_in_plan(plan, nid, want)
            nid = want
        if nid in by_id:
            claims.append(ImportClaim(node_id=nid, point_id=nid, point_name=by_id[nid]["name"]))
    for n in plan.get("nodes") or []:
        n.pop("claims", None)
    return plan, claims


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _near_misses(plan: dict, points: list[dict], claimed: set[str]) -> list[ImportClaim]:
    """没认领、但名字和清单里某个点很像的新节点——很可能就是同一个东西起了两个名。"""
    out: list[ImportClaim] = []
    for n in plan.get("nodes") or []:
        nid = str(n.get("id") or "")
        if not nid or nid in claimed:
            continue
        mine = {_norm(nid), _norm(str(n.get("name") or ""))} - {""}
        best = None
        for p in points:
            theirs = {_norm(p["id"]), _norm(p["name"])} - {""}
            ratio = max((_similar(a, b) for a in mine for b in theirs), default=0.0)
            if ratio >= NEAR_MISS_RATIO and (best is None or ratio > best[0]):
                best = (ratio, p)
        if best:
            out.append(ImportClaim(node_id=nid, point_id=best[1]["id"], point_name=best[1]["name"],
                                   ratio=round(best[0], 2)))
    return out


def _similar(a: str, b: str) -> float:
    if len(a) >= 2 and len(b) >= 2 and (a in b or b in a):
        # 一个是另一个的子串（`RNN` ⊂ `RNN与长程依赖`）：按长度比给 0.5～1，短的越接近长的越像
        return round(min(len(a), len(b)) / max(len(a), len(b)) * 0.5 + 0.5, 2)
    return SequenceMatcher(None, a, b).ratio()


def _isolated(plan: dict, preview: ImportResult, index: dict, claimed: set[str]) -> list[str]:
    """孤立 = 所在的连通块里没有任何一条边碰到已有节点、也没人认领清单点。

    按连通块算而不是按单个节点：`RNN` 只连了同篇拆出来的 `注意力机制`，而后者连着图里的 `a`——
    `RNN` 上图后不是孤岛，不该报。真正要提醒的是整块和体系断开的那些：它们只会掉进 Inbox。
    """
    existing = {n["id"] for n in index["nodes"] if not n.get("virtual")}
    new_ids = [str(n.get("id")) for n in plan.get("nodes") or [] if n.get("id") and n["id"] not in existing]
    parent = {nid: nid for nid in new_ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    anchored: set[str] = set(claimed)                    # 块里有谁碰到了体系
    for n in plan.get("nodes") or []:
        nid = str(n.get("id"))
        if nid not in parent:
            continue
        for r in n.get("relations") or []:
            tgt = str(r.get("target") or "")
            if tgt in existing:
                anchored.add(nid)
            elif tgt in parent:
                union(nid, tgt)
    for pe in preview.pending:
        if pe.target in existing and pe.source in parent:
            anchored.add(pe.source)
    roots_ok = {find(x) for x in anchored if x in parent}
    return [nid for nid in new_ids if find(nid) not in roots_ok]


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
