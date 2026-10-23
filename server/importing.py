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

from . import audit, curation
from .contracts import (FileDiff, ImportArticle, ImportClaim, ImportProposal, ImportProposeRequest, ImportRequest,
                        ImportReshapeRequest, ImportReshapeResult, ImportResult, PendingEdge, SourceFile, SourceText,
                        SourcesRead)
from .index_service import current_index, invalidate
from .llm_call import ask, ask_meta
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
    article = _article_plan(vault, req)
    target = core.ImportTarget(req.field.strip(), req.source.strip(), (req.folder or "").strip() or None,
                               keep_field=req.keep_field, article=article)
    # 先提升再改名：卡上待审边的 key 是按**卡上显示的 id** 拼的，改名之后就对不上了
    plan = core.promote_in_plan(req.plan, req.promote)
    for old_id, new_id in req.renames.items():
        plan = core.rename_in_plan(plan, old_id, new_id)
    # 卡上点了「并进已有节点」的：不新建，补充已有的（边挂过去、名字进别名）
    for src, dst in req.relink.items():
        if dst not in {n["id"] for n in index["nodes"] if not n.get("virtual")}:
            raise ImportRejected(f"要并进的 `{dst}` 不在图里")
        try:
            plan = core.relink_in_plan(plan, src, dst)
        except ValueError as exc:
            raise ImportRejected(str(exc)) from exc
    tr = core.translate(plan, index, core.load_relation_types(vault), target)
    try:
        edits = core.plan(vault, tr.changes, index)
    except (core.ChangeRejected, core.WriteConflict) as exc:
        core.record_issue(vault, "write", str(exc), where="/api/import")
        raise ImportRejected(str(exc)) from exc

    files = [FileDiff(path=e.rel, notes=e.notes, diff=curation.diff_of(e)) for e in edits]
    pending = [PendingEdge(**{k: v for k, v in p.items() if k in PendingEdge.model_fields}) for p in tr.pending]
    base = dict(files=files, pending=pending, warnings=tr.warnings, counts=tr.counts(), summary=tr.summary,
                article=_article_info(article))
    if req.dry_run:
        return ImportResult(applied=False, index_revision=index["revision"],
                            audit=_not_yet(audit.preview(vault, index, edits), article), **base)

    # 先放原文再过审核：原文还不在的话，确定性检查会给每个节点都报一条「来源指不到原文」，
    # 那串噪音还会喂进审核模型的提示词。挡下了、写失败了，都把刚放进来的撤掉
    created = core.write_article(vault, article, _origin_of(req), target.date) if article else False
    try:
        # 导入走的是同一条闸：一篇文章拆出来的新节点正是最该被审的那批内容
        report = audit.gate(vault, index, edits, tr.changes, req.force)
        if report.blocked:
            _undo_article(vault, article, created)
            return ImportResult(applied=False, index_revision=index["revision"], audit=report, **base)
        snapshot = core.commit(vault, edits) if edits else ""
    except BaseException:
        _undo_article(vault, article, created)
        raise
    # 原文进了库就记下它在哪：审待审边时要能点开看「模型当时是看着什么说的」
    origin = {"source": target.source, "imported_at": target.date,
              **({"article": target.article.path} if target.article else {})}
    if tr.pending:
        core.add_pending(vault, tr.pending, origin)
    if tr.proposed_types:
        known = set(core.load_relation_types(vault).types)
        core.add_type_proposals(vault, tr.proposed_types, origin, known)
    home = plan.get("suggest_home")
    if isinstance(home, dict):
        # 只记这次真建出来、又被模型点名孤立的那几个；Inbox 上显示，不自动建域 / 建项目
        lonely = [i for i in (home.get("isolated") or []) if i in tr.new_ids]
        core.add_home(vault, lonely, home, origin)
    log = _archive(vault, plan, tr, target)
    invalidate(vault)
    return ImportResult(applied=True, backup=snapshot or None, log=log, audit=report,
                        index_revision=current_index(vault)["revision"], **base)


def _archive(vault: Path, plan: dict, tr, target) -> str:
    """方案存档到 .knowrary/imports/：和 CLI 同一个位置、同一个文件名规则。"""
    stem = re.sub(r"[^\w一-鿿-]+", "-", target.source)[:60]
    rel = f".knowrary/imports/{dt.date.today().isoformat()}-{stem}.json"
    core.write(vault / rel, json.dumps({"plan": plan, "changes": tr.changes, "pending": tr.pending,
                                        "warnings": tr.warnings,
                                        "article": target.article.path if target.article else None},
                                       ensure_ascii=False, indent=2))
    return rel


# ---------------------------------------------------------------- 原文进库

def _article_plan(vault: Path, req) -> "core.ArticlePlan | None":
    """保留原文的话它该怎么进库。同名不同文在这里就挡下（`ImportRejected`）——
    `propose` 在调模型之前先跑这一步，免得付了钱才发现写不进去。"""
    # 成品即原文（2026-10-22）：html 不存副本，节点直接链 `[[works/….html#锚点]]`。
    # keep_article 对成品没有意义——链接是成品进图谱的全部意义，关掉等于白导
    if req.file and req.file.lower().endswith(core.WORK_SUFFIXES):
        try:
            return core.plan_work(vault, req.file)
        except core.WorkRejected as exc:
            raise ImportRejected(str(exc)) from exc
    if not req.keep_article:
        return None
    # 不 strip：原文要一字不改地存下来，连结尾的换行也算
    text = req.text or ""
    if not text.strip() and req.file:
        text = read_source(vault, req.file).text
    if not text.strip():
        return None
    try:
        return core.plan_article(vault, req.source.strip(), text, req.file or None)
    except core.ArticleConflict as exc:
        raise ImportRejected(str(exc)) from exc


def _article_info(article) -> ImportArticle | None:
    if article is None:
        return None
    return ImportArticle(path=article.path, action=article.action, link=article.link,
                         headings=len(article.headings))


def _origin_of(req) -> str:
    """复制过来那份原文的 origin：库里原来的路径 / 本地文件名 / 粘贴。"""
    return (req.file or req.origin or "粘贴").strip()


def _undo_article(vault: Path, article, created: bool) -> None:
    """只撤自己这次新放进来的那份：就地引用、复用的原文本来就在，一个字节都不碰。"""
    if created:
        (vault / article.path).unlink(missing_ok=True)


def _not_yet(report, article):
    """预览时原文还没进库（真写入那一下才放），「来源指不到原文」里指向它的那几条不算数。"""
    if article is None or article.action != "copy":
        return report
    report.issues = [i for i in report.issues
                     if not (i.code == "dead_source" and f"`{article.path}`" in i.message)]
    return report


# ---------------------------------------------------------------- 文章 → 方案（一次 LLM 调用）

def propose(vault: Path, req: ImportProposeRequest) -> ImportProposal:
    """文章 → 方案 → 顺手 dry-run。三级匹配在这里算完：
    先对当前项目里没建的点（认领幽灵），再看方案连到全局哪些已有节点，两边都沾不上的标孤立。"""
    text = _article_text(vault, req)
    ask_for = ImportRequest(plan={}, field=req.field, source=req.source, folder=req.folder, dry_run=True,
                            keep_article=req.keep_article, text=req.text, file=req.file, origin=req.origin)
    _article_plan(vault, ask_for)          # 同名冲突在调模型之前挡下
    index = current_index(vault)
    rt = core.load_relation_types(vault)
    points = core.project_points(vault, index, req.project)
    prompt = core.build_article_prompt(core.cards_from_index(index), rt, text, req.field.strip(), points, req.grain)
    raw = ask(vault, "learn", prompt, op="import")
    plan = core.parse_json(raw, f"import source={req.source}", vault=vault)
    if not isinstance(plan, dict) or not plan.get("nodes"):
        raise ImportRejected("模型没给出可用的方案（没有 nodes）。原文已记进问题流，换个模型或缩短文章再试")

    plan, raw_claims = core.normalize_claims(plan, points, core.rename_in_plan)
    claims = [ImportClaim(**c) for c in raw_claims]
    preview = run(vault, ask_for.model_copy(update={"plan": plan}))
    claimed = {c.node_id for c in claims}
    near = [ImportClaim(**c) for c in core.near_misses(plan, points, claimed)]
    isolated = core.isolated(plan, [p.model_dump() for p in preview.pending], index, claimed)
    home = plan.get("suggest_home") if isinstance(plan.get("suggest_home"), dict) else None
    shape = str(plan.get("shape") or "").strip()
    near_existing = _near_existing(index, plan, plan.get("nodes") or [], claimed)
    return ImportProposal(plan=plan, preview=preview, claims=claims, near_misses=near, isolated=isolated,
                          suggest_home=home, project_points=len(points), prompt_chars=len(prompt),
                          shape=shape if shape in ("single", "multi") else "", near_existing=near_existing)


def _near_existing(index: dict, plan: dict, nodes: list[dict], claimed: set) -> dict:
    """新节点 → 图里可能是同一个的（同名 / 用字一样 / 只差后缀）。方案里别的新点不算。"""
    born = {n.get("id") for n in plan.get("nodes") or []}
    return {nid: got for n in nodes
            if (nid := n.get("id")) and nid not in claimed
            and (got := core.near_candidates(index, {"id": nid, "name": n.get("name"), "aliases": n.get("aliases")},
                                             exclude=born))}


# ---------------------------------------------------------------- 审核卡上调整拆法（2026-10-13）

def reshape(vault: Path, req: ImportReshapeRequest) -> ImportReshapeResult:
    """勾几个点「合并」/ 对一个点「再拆」：调一次 learn，只重写动到的点，其余原样。只改卡不写盘。

    模型交回的点先过 `core.reshape_in_plan` 的结构检查（数量对不对、id 撞没撞），
    改完的方案由卡上照常「重算 diff」——那一步会过写回层的校验，坏方案摆不上去。
    """
    ids = list(dict.fromkeys(req.ids))
    article = (req.text or "").strip() or (read_source(vault, req.file).text if req.file else "")
    try:
        core.check_reshape(req.plan, req.op, ids)
    except ValueError as exc:                      # 选得不对（没这几个点、合并只勾了一个）：不白花一次钱
        raise ImportRejected(str(exc)) from exc
    prompt = core.build_reshape_prompt(req.plan, req.op, ids, article, core.load_relation_types(vault), req.hint)
    raw, meta = ask_meta(vault, "learn", prompt, op=f"import-{req.op}")
    data = core.parse_json(raw, f"import-{req.op}", vault=vault)
    made = [n for n in (data.get("nodes") if isinstance(data, dict) else None) or [] if isinstance(n, dict)]
    try:
        after = core.reshape_in_plan(req.plan, req.op, ids, made, str(data.get("main") or "") or None)
    except ValueError as exc:
        core.record_issue(vault, "reshape", f"{exc}：{raw[:600]}", where="/api/import/reshape")
        raise ImportRejected(f"模型交回的拆法用不了：{exc}（{meta['model']}，{meta['ms'] / 1000:.1f}s）") from exc
    made_ids = [n["id"] for n in made]
    return ImportReshapeResult(
        plan=after, before=req.plan, made=made_ids, summary=str(data.get("summary") or "").strip(),
        delta=_reshape_delta([n for n in req.plan["nodes"] if n.get("id") in ids], made),
        near_existing=_near_existing(current_index(vault), after, made, set()), **meta)


def _render_nodes(nodes: list[dict]) -> list[str]:
    out: list[str] = []
    for n in nodes:
        out.append(f"【{n.get('id')}】{n.get('name') or ''}")
        out.append(f"  摘要：{n.get('desc') or ''}")
        out.extend(str(n.get("body") or "").splitlines())
        out.extend(f"  - {r.get('type')}:: {r.get('target')}" for r in n.get("relations") or [])
        out.append("")
    return out


def _reshape_delta(before: list[dict], after: list[dict]) -> str:
    import difflib
    return "\n".join(difflib.unified_diff(_render_nodes(before), _render_nodes(after),
                                         fromfile="改前", tofile="改后", lineterm="", n=2))


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
    """vault 里可以当导入素材的：节点目录、机器目录、代码目录之外的 md / txt，
    加上成品目录里的 html（成品即原文，拆出来的节点直接链它）。按改动时间倒序。"""
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
    for path in core.list_works(core.works_dir(vault)):
        try:
            st = path.stat()
        except OSError:
            continue
        files.append(SourceFile(path=path.relative_to(vault).as_posix(), name=path.name, size=st.st_size,
                                modified=dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")))
    files.sort(key=lambda f: f.modified, reverse=True)
    return SourcesRead(files=files[:500])


def read_source(vault: Path, rel: str) -> SourceText:
    """读一个素材文件。路径必须落在 vault 里、在允许的目录、后缀对、不太大——一个 `..` 都不放过。
    成品（html）只认成品目录里的，回的是抽出来的纯文本（预览和提示词都用它，原页面走 /api/work 看）。"""
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
    is_work = resolved.name.lower().endswith(core.WORK_SUFFIXES)
    if is_work:
        root = core.load_vault_config(vault)["works_dir"]
        if not clean.startswith(root + "/"):
            raise ImportRejected(f"html 只认成品目录 `{root}/` 里的——成品要自己整理进去再导入")
    elif not resolved.name.lower().endswith(SOURCE_SUFFIXES):
        raise ImportRejected(f"只认 md / txt，和成品目录里的 html：{rel}")
    if not resolved.is_file():
        raise ImportRejected(f"文件不存在：{rel}")
    if resolved.stat().st_size > MAX_SOURCE_BYTES:
        raise ImportRejected(f"文件太大（{resolved.stat().st_size} 字节）")
    return SourceText(path=clean, text=core.work_text(resolved) if is_work else core.read(resolved))
