"""FastAPI 本地服务：只读 index、读写 layout，静态托管前端构建产物。

边界（设计文档 3.4）：这一层永远不改 Markdown。改 md 只能走阶段 3 的 ChangeSet。
"""
from __future__ import annotations

import datetime as dt
import json
import logging

from pathlib import Path

from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import (assets, audit, chat as chat_svc, compare as compare_svc, copying, curation, importing,
               projects as projects_svc, summarize as summarize_svc, vaults, years as years_svc)
from .contracts import (AuditReport, AuditRequest, SourceRef, VaultConfigPatch, VaultConfigRead, CalendarRead, ChangeResult, ChangeSet, ChatRequest, CoachToday, FileDiff, ImportProposal,
                        ImportProposeRequest, ImportRequest, ImportResult, SourceText, SourcesRead, SummarizeRequest, SummaryDraft,
                        InboxRead,
                        LayoutPatch, LayoutRead,
                        LayoutSaved, MergeImpact, MergeRequest, MergeResult, NodeDetail,
                        PlanProposal, PlanProposeRequest, PlaceRequest,
                        PlaceResult, ProjectsRead, ProjectsSaved, ProjectsWrite, QuizDiagnoseRequest,
                        QuizDiagnosis, QuizGradeRequest, QuizGraded, QuizRequest, QuizSet, RenameImpact,
                        RenameRequest, RenameResult, ReviewDone, ReviewRequest, SettingsPatch,
                        SettingsRead, SuggestRequest, LLMConfigRead, LLMConfigWrite, LLMConfigTest,
                        LLMConfigTestRead,
                        SuggestResult, UsageRead, VaultBrowse, VaultPick, VaultRead,
                        CopyCatalog, CopyRequest, CopyResult, CopySource,
                        YearProposal, YearProposeRequest,
                        CompareProposal, CompareProposeRequest)
from .index_service import current_index, invalidate
from .llm_call import LLMFailed
from . import llm_config
from .layout_store import (LayoutBroken, PatchRejected, RevisionConflict, apply_patch, find_orphans,
                           load_or_init)
from .paths import DEFAULT_LAYOUT, WEB_DIST, core, vault_path

import llm_backend  # noqa: E402  (paths 注入工具模块路径)

log = logging.getLogger(__name__)

app = FastAPI(title="Knowrary 本地服务", version="0.2.0",
              description="结构视图的数据源：index 只读、layout 可写、Markdown 不动")

# 开发期前端跑在 Vite（5173），构建产物同源托管时用不到 CORS
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(LayoutBroken)
def _on_layout_broken(_request, exc: LayoutBroken) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc),
                                                  "hint": "原文件已保留，修好或删除后重启即可重新生成"})


@app.exception_handler(LLMFailed)
def llm_failed(request: Request, exc: LLMFailed) -> JSONResponse:
    """模型没答上来 → 502，把原话带回去。

    所有调模型的路由（出题、判分、关系建议、拆计划、补 year）共用这一处：
    它们的失败长得一模一样，各写一遍 try 只会有的写有的漏。对话那条路不走这里——
    它是 SSE，已经开始往外吐字节了，只能在流里发一个 `error` 事件。
    """
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(vaults.NoVaultSelected)
def _no_vault(_request, exc: vaults.NoVaultSelected) -> JSONResponse:
    """一个库都还没选 → 409。前端见到这个码就把人送进「设置 → 知识库」。

    不用 404/422：那两个是"你要的东西没有"和"你给的参数不对"，而这里是
    **程序还没被指向任何数据**，是一个需要人做一次选择才能继续的状态。
    """
    return JSONResponse(status_code=409, content={"detail": str(exc), "need_vault": True})


@app.exception_handler(vaults.VaultRejected)
def _vault_rejected(_request, exc: vaults.VaultRejected) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/api/health")
def health() -> dict:
    vault = vault_path()
    index = current_index(vault)
    layout, generated = load_or_init(vault, index)
    return {"vault": str(vault), "index_revision": index["revision"], "stats": index["stats"],
            "layout_revision": layout.revision, "layout_generated": generated,
            "web_dist": WEB_DIST.exists()}


@app.get("/api/index")
def get_index() -> dict:
    """派生缓存，只读。前端据此渲染节点与边，真值永远在 md。"""
    return current_index(vault_path())


def _layout_target(vault, layout: str | None):
    """`?layout=<id>` 指到项目画布或对比画布；不给就是全局图。

    返回 (文件名, builder)：builder 是"这份 layout 还不存在时拿什么填"，
    存储层不需要认识项目和对比组这两个概念。

    id 直接当文件名用，所以先过一道形状校验再确认它**真的是**一个项目 / 对比组——
    后面这一步才是真闸：查无此 id 的名字根本进不来，也就拼不出路径。
    项目 id 是生成的，只允许 ASCII（`core.ID_OK`）；对比组 id 是你自己起的名字，
    可以带中文，按节点 id 那条规则来（`core.RE_ID_OK`：不许空白和路径分隔符）。
    """
    if not layout or layout == DEFAULT_LAYOUT:
        return DEFAULT_LAYOUT, None
    if not core.RE_ID_OK.match(layout) or layout.startswith("."):
        raise HTTPException(status_code=422, detail=f"非法的 layout 名 `{layout}`")
    if core.ID_OK.match(layout):
        project = (core.load_projects(vault).get("projects") or {}).get(layout)
        if project is not None:
            return layout, lambda index: core.build_project_layout(project, index)
    index = current_index(vault)
    if any(n["id"] == layout and n.get("type") == core.COMPARE_TYPE for n in index["nodes"]):
        members = [r["id"] for r in core.compare_table(vault, index, layout)["rows"]]
        return layout, lambda idx: core.build_compare_layout(members, idx)
    raise HTTPException(status_code=404, detail=f"没有 `{layout}` 这个项目或对比组")


@app.get("/api/layout", response_model=LayoutRead)
def get_layout(layout: str | None = None) -> LayoutRead:
    vault = vault_path()
    index = current_index(vault)
    name, build = _layout_target(vault, layout)
    doc, generated = load_or_init(vault, index, name, build)
    return LayoutRead(layout=doc, orphans=find_orphans(doc, index, vault),
                      index_revision=index["revision"], generated=generated)


@app.patch("/api/layout", response_model=LayoutSaved)
def patch_layout(patch: LayoutPatch, layout: str | None = None) -> LayoutSaved:
    """高频写入口：拖拽、折叠、便签。只改 layout 文件，不进确认流程。"""
    vault = vault_path()
    index = current_index(vault)
    name, build = _layout_target(vault, layout)
    try:
        doc, orphans, backup = apply_patch(vault, patch, index, name, build)
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={
            "message": str(exc), "current_revision": exc.current.revision,
            "hint": "重新 GET /api/layout 后基于新 revision 重试"}) from exc
    except PatchRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return LayoutSaved(revision=doc.revision, updated_at=doc.updated_at or "", orphans=orphans,
                       backup=backup)


def _source_refs(vault: Path, items: list[str]) -> list[SourceRef]:
    """把节点的 sources 逐项认成原文 / 外部来源。原文在的话给一个 Obsidian 打开链接。"""
    resolve = core.SourceResolver(vault)
    out = []
    for item in items:
        ref = {k: v for k, v in resolve(item).items() if k != "legacy"}
        if ref["exists"]:
            ref["uri"] = f"obsidian://open?vault={quote(vault.name)}&file={quote(ref['path'])}"
        out.append(SourceRef(**ref))
    return out


@app.get("/api/node/{node_id}", response_model=NodeDetail)
def get_node(node_id: str) -> NodeDetail:
    """单个节点：md 原文 + 元数据 + 出入边 + Obsidian 打开链接。只读。"""
    vault = vault_path()
    index = current_index(vault)
    meta = next((n for n in index["nodes"] if n["id"] == node_id), None)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"节点 `{node_id}` 不在索引里")
    if meta.get("virtual") or not meta.get("path"):
        raise HTTPException(status_code=404, detail=f"`{node_id}` 只是被引用的占位 stub，还没有 md 文件")
    path = vault / meta["path"]
    edges = {e["id"]: e for e in index["edges"]}
    return NodeDetail(
        id=node_id, path=meta["path"], raw=core.read(path), digest=meta.get("digest", ""),
        meta={k: v for k, v in meta.items() if k not in ("out", "in", "path", "digest")},
        out=[edges[i] for i in meta.get("out", []) if i in edges],
        in_edges=[edges[i] for i in meta.get("in", []) if i in edges],
        obsidian_uri=f"obsidian://open?vault={quote(vault.name)}&file={quote(meta['path'])}",
        sources=_source_refs(vault, meta.get("sources") or []),
    )


@app.get("/api/inbox", response_model=InboxRead)
def get_inbox() -> InboxRead:
    """索引里有、画布上还没有的节点，附带建议分组。"""
    return curation.inbox(vault_path())


@app.post("/api/place", response_model=PlaceResult)
def post_place(req: PlaceRequest) -> PlaceResult:
    """把 Inbox 节点放上画布：只在已有分组框里找空位，放不下就留在 Inbox。"""
    try:
        return curation.place(vault_path(), req)
    except curation.PlaceRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={
            "message": str(exc), "current_revision": exc.current.revision,
            "hint": "重新 GET /api/layout 后基于新 revision 重试"}) from exc


@app.post("/api/place/regroup", response_model=PlaceResult)
def post_regroup(body: dict) -> PlaceResult:
    """把节点挪进它那一层的道。

    不给 `ids` = 批量扫一遍，只动草稿；给了 `ids` = 人点了具体某个点，定稿的也挪。
    `create_lane` 为真时，目标那条道不存在就现开一条。
    """
    try:
        return curation.regroup(vault_path(), int(body.get("base_revision") or 0),
                                only_draft=body.get("only_draft", True),
                                ids=body.get("ids"), create_lane=bool(body.get("create_lane")))
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={
            "message": str(exc), "current_revision": exc.current.revision,
            "hint": "重新 GET /api/layout 后基于新 revision 重试"}) from exc


@app.get("/api/compare")
def get_compare_groups() -> dict:
    """对比组目录。现算，不存第二份——改名删除自动跟着走。"""
    return compare_svc.directory(vault_path())


@app.get("/api/schools")
def get_schools() -> dict:
    """流派：历史视图要画的时间带 + 累计走势。现算，不存第二份。

    **没有独立入口是故意的**：流派只在历史视图里有形态（一条横跨年份区间的带子），
    带子本身就是目录。对比组要全局入口是因为它的产物（表 + 自己的画布）没别处可去。

    算在服务端而不是前端从 index 里推：成员筛选、按年排序、累计曲线这几条要是两边
    各写一遍，迟早对不上——和工具表/白名单必须同一份数据是同一个道理。
    """
    vault = vault_path()
    index = current_index(vault)
    # `homes` 只覆盖跨多条线的那几个点：它们的真身画在哪条道上是个历史事实，
    # 推不出来，按 md 里 `- 属于::` 的书写顺序定（见 core.line_homes）。
    return {"schools": core.schools(index), "homes": core.line_homes(vault, index)}


@app.get("/api/schools/{kind}")
def get_schools_of_kind(kind: str) -> dict:
    """只要某一种线（`流派` / `领域线`）。

    **两者分档不是洁癖**：流派之间是竞争（互斥的世界观），领域线之间是并列；
    混在一档里，「这个点同时属于两条线」的含义就跟着混了——
    前者重叠是件值得盯着看的事，后者是日常。
    """
    if kind not in core.LINE_TYPES:
        raise HTTPException(status_code=404, detail=f"没有 `{kind}` 这种线，只有：{core.LINE_TYPES}")
    return {"schools": core.schools(current_index(vault_path()), kind)}


@app.get("/api/compare/{group_id}")
def get_compare_table(group_id: str) -> dict:
    """一个对比组的表：列 = dimensions，行 = 成员（按 md 里的书写顺序），外加残差列。"""
    data = compare_svc.table(vault_path(), group_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"没有 `{group_id}` 这个对比组")
    return data


@app.post("/api/compare/{group_id}/propose", response_model=CompareProposal)
def post_compare_propose(group_id: str, req: CompareProposeRequest) -> CompareProposal:
    """把这张表的空格子补一轮。调 LLM（review 角色）**一次**，只读不写。

    写回走 /api/changes 的 `set_fact`，那条路才有 diff 预览、备份和写前指纹校验。
    """
    data = compare_svc.propose(vault_path(), group_id, req.cells)
    if data is None:
        raise HTTPException(status_code=404, detail=f"没有 `{group_id}` 这个对比组")
    return data


@app.get("/api/digest")
def get_digest() -> dict:
    """图谱欠账清单：草稿 / 待复习 / stub / 跨分组桥 / 重复候选 / 环。只读。"""
    return curation.digest(vault_path())


@app.post("/api/years/propose", response_model=YearProposal)
def post_years_propose(req: YearProposeRequest) -> YearProposal:
    """给缺 year 的节点批量提议年份。调 LLM（review 角色）**一次**，只读不写。

    写回仍然走 /api/changes（update_frontmatter），所以 diff 预览、备份、指纹校验一样不少。
    """
    return years_svc.propose(vault_path(), req.node_ids)


@app.get("/api/years/missing")
def get_years_missing() -> dict:
    """还有哪些节点没填 year。不调 LLM，纯查。"""
    rows = years_svc.missing(current_index(vault_path()))
    return {"count": len(rows),
            "items": [{"id": n["id"], "name": n.get("name") or n["id"],
                       "field": n.get("field") or "", "desc": (n.get("desc") or "")[:120]}
                      for n in rows]}


@app.post("/api/suggest", response_model=SuggestResult)
def post_suggest(req: SuggestRequest) -> SuggestResult:
    """AI 建议：关系、去重、分类。调用 LLM（review 角色），可能耗时数秒。"""
    vault = vault_path()
    index = current_index(vault)
    meta = next((n for n in index["nodes"] if n["id"] == req.node_id), None)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"节点 `{req.node_id}` 不在索引里")
    from . import suggest as sug
    return sug.suggest(vault, req.node_id)


@app.get("/api/projects", response_model=ProjectsRead)
def get_projects() -> ProjectsRead:
    """学习计划 + 每个知识点的掌握度（五档，现算不落盘）。"""
    return projects_svc.read(vault_path())


@app.put("/api/projects", response_model=ProjectsSaved)
def put_projects(req: ProjectsWrite) -> ProjectsSaved:
    """整份替换计划。base_revision 对不上返回 409，客户端重新拉取后再提交。"""
    vault = vault_path()
    try:
        saved = projects_svc.write(vault, req)
    except projects_svc.PlansConflict as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc), "current_revision": exc.current}) from exc
    except projects_svc.PlansRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    core.card_applied(vault, req.card or "")           # 项目卡 / 拆点卡 / 清单卡的采纳都走这条路
    return saved


@app.post("/api/chat")
def post_chat(req: ChatRequest) -> StreamingResponse:
    """对话式教练：SSE 流式。**这条路永远不写 Markdown**——模型只能提议，
    变更卡走 `/api/changes` 由人按下写入（4.4）。"""
    vault = vault_path()

    def events():
        try:
            for ev in chat_svc.run(vault, req):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except chat_svc.ChatRejected as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"
        except BaseException as exc:            # SystemExit 是 llm_backend 的报错方式
            log.warning("对话失败：%s", exc)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)[:300]}, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/calendar", response_model=CalendarRead)
def get_calendar(days: int = 120, to: str | None = None) -> CalendarRead:
    """学习日历：每天建了几个、复习了几次、答了几道、烧了多少钱。纯读，不写任何文件。"""
    vault = vault_path()
    try:
        end = dt.date.fromisoformat(to) if to else dt.date.today()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"看不懂的日期 `{to}`") from exc
    start = end - dt.timedelta(days=max(1, min(days, core.CALENDAR_MAX_DAYS)))
    data = core.build_calendar(vault, current_index(vault), start, end)
    return CalendarRead(**{**data, "from": data.pop("from")})


@app.get("/api/chat/history")
def get_chat_history(project: str | None = None, limit: int = 40,
                     session: str | None = None) -> dict:
    """某一段对话，刷新页面后接着聊；不给 session 就取最近那一段。纯读留档，不调 LLM。"""
    rows = chat_svc.history(vault_path(), project, max(1, min(limit, 200)), session)
    return {"messages": rows, "project": project, "session": session}


@app.get("/api/chat/sessions")
def get_chat_sessions(project: str | None = None) -> dict:
    """这个项目下聊过几段。**从留档行聚合，不存会话表。**"""
    return {"sessions": chat_svc.sessions(vault_path(), project)}


@app.patch("/api/chat/sessions/{session}")
def patch_chat_session(session: str, body: dict) -> dict:
    """改一段对话的贴纸：给了 `title` 就改名，给了 `archived` 就归档 / 取消归档。
    都是「id → 值」的贴纸，不是会话表；改名留空就撕掉，回到自动取的标题（第一句我说的话）。"""
    project = body.get("project") or None
    out: dict = {"session": session}
    if "title" in body:
        out["title"] = chat_svc.rename_session(vault_path(), session, str(body.get("title") or ""),
                                               project)
    if "archived" in body:
        out["archived"] = chat_svc.archive_session(vault_path(), session, bool(body["archived"]),
                                                   project)
    return out


@app.delete("/api/chat/sessions/{session}")
def delete_chat_session(session: str, project: str | None = None) -> dict:
    """真删一段对话：留档里的行剔掉、贴纸撕掉。删了就没了，已经梳理进 md 的知识不受影响。"""
    try:
        dropped = chat_svc.delete_session(vault_path(), session, project or None)
    except chat_svc.ChatRejected as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"session": session, "deleted": dropped}


@app.post("/api/chat/tidied")
def post_chat_tidied(body: dict) -> dict:
    """推进某一段对话的「梳理游标」。**只在变更卡真写进 md 之后才调**——
    梳理过但没采纳的内容不算整理过，游标不能动。

    和改名一样存的是一张贴纸（`.knowrary/chat/<项目>/tidied.json`），不是真值：
    删掉它只会退回全量重梳，一个字的知识都不会丢。
    """
    try:
        mark = chat_svc.mark_tidied(vault_path(), str(body.get("session") or ""),
                                    str(body.get("upto") or ""), body.get("project") or None,
                                    int(body.get("turns") or 0))
    except chat_svc.ChatRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"session": body.get("session"), "tidied": mark or None}


@app.exception_handler(copying.CopyRejected)
def _copy_rejected(_request, exc: copying.CopyRejected) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/api/copy/sources", response_model=list[CopySource])
def copy_sources() -> list[CopySource]:
    """能从哪些库抄：当前库 + 最近用过的。**不许传任意路径进来**——
    这条链路会读另一个目录里的 md，放行名单和目录浏览是同一套。"""
    here = vault_path()
    out = []
    for path in copying.known_vaults():
        try:
            count = len([n for n in current_index(path)["nodes"] if not n.get("virtual")])
        except Exception:                      # 库坏了不该让整个下拉框打不开
            count = 0
        out.append(CopySource(path=str(path), name=path.name, nodes=count, current=path == here))
    return out


@app.get("/api/copy/catalog", response_model=CopyCatalog)
def copy_catalog(source: str, q: str = "", limit: int = 200) -> CopyCatalog:
    """列源库的节点，给「从别的库抽」那一侧挑。只读。"""
    return CopyCatalog(**copying.catalog(copying.resolve_source(source), q, max(1, min(limit, 500))))


@app.post("/api/copy", response_model=CopyResult)
def copy_nodes(req: CopyRequest) -> CopyResult:
    """把选中的点抄进一个库。不给 `target` 就是当前库（从别处拉）；
    给了就是往别的库推（站在参考库里看到好东西，抄进自己的库）。
    dry_run=true 只预览 diff，false 才落盘。"""
    try:
        return CopyResult(**copying.run(vault_path(), req.source, req.ids,
                                        with_neighbors=req.with_neighbors,
                                        dry_run=req.dry_run, renames=req.renames,
                                        target_raw=req.target))
    except importing.StaleIndex as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except importing.ImportRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/vault", response_model=VaultRead)
def get_vault() -> VaultRead:
    """当前知识库与最近用过的几个。这份状态在用户级配置里，不属于任何 vault。"""
    return VaultRead(**vaults.read())


@app.get("/api/vault/browse", response_model=VaultBrowse)
def browse_vault(path: str | None = None) -> VaultBrowse:
    """列子目录，给前端画文件夹选择器。只许在家目录里逛（越界一律弹回家目录）。"""
    return VaultBrowse(**vaults.browse(path))


@app.post("/api/vault/init", response_model=VaultRead)
def init_vault(req: VaultPick) -> VaultRead:
    """把一个空目录建成知识库并切过去。已经是库就直接切，不覆盖任何数据。"""
    target = vaults.init(Path(req.path), sample=req.sample)
    vaults.switch(target)
    return VaultRead(**vaults.read())


@app.put("/api/vault/current", response_model=VaultRead)
def switch_vault(req: VaultPick) -> VaultRead:
    """切换当前知识库。只接受已经是库的目录——要新建走 init。"""
    vaults.switch(Path(req.path))
    return VaultRead(**vaults.read())


@app.delete("/api/vault/recent", response_model=VaultRead)
def forget_vault(path: str) -> VaultRead:
    """把一条从「最近使用」里去掉。只动列表，磁盘上的库一个字节都不碰。"""
    vaults.forget(Path(path))
    return VaultRead(**vaults.read())


def _vault_config_read(vault: Path, extra: dict | None = None) -> VaultConfigRead:
    cfg = core.load_vault_config(vault)
    return VaultConfigRead(**cfg, articles=len(core.list_articles(vault / cfg["articles_dir"])),
                           candidates=core.articles_dir_candidates(vault), **(extra or {}))


@app.get("/api/vault/config", response_model=VaultConfigRead)
def get_vault_config() -> VaultConfigRead:
    """这个库的结构配置（现在只有原文目录）。在库里，跟着 git 走——和「用哪个库」那份用户级选择不是一回事。"""
    return _vault_config_read(vault_path())


@app.put("/api/vault/config", response_model=VaultConfigRead)
def put_vault_config(req: VaultConfigPatch) -> VaultConfigRead:
    """改原文目录。旧目录里还有文章而没说 `move` 就 409——前端据此问一句「一起搬过去？」。"""
    vault = vault_path()
    try:
        done = core.save_articles_dir(vault, req.articles_dir, move=req.move, dry_run=req.dry_run)
    except core.ArticlesNeedMove as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "articles": exc.count,
                                                     "from": exc.old}) from exc
    except core.VaultConfigRejected as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc)}) from exc
    if req.dry_run:
        cfg = done["config"]
        return VaultConfigRead(**cfg, articles=len(core.list_articles(vault / cfg["articles_dir"])))
    return _vault_config_read(vault, {"moved": done["moved"], "backup": done["backup"]})


@app.get("/api/settings", response_model=SettingsRead)
def get_settings() -> SettingsRead:
    """偏好设置。文件不在就返回默认（全开）——新 vault 该有完整体验。"""
    return SettingsRead(**core.load_settings())


@app.put("/api/settings", response_model=SettingsRead)
def put_settings(req: SettingsPatch) -> SettingsRead:
    """改设置。合并写回，只认契约里登记过的开关。"""
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    return SettingsRead(**core.save_settings(patch))


@app.get("/api/llm/config", response_model=LLMConfigRead)
def get_llm_config() -> LLMConfigRead:
    """读取脱敏后的 LLM 配置，API key 只返回是否存在。"""
    try:
        return LLMConfigRead(**llm_config.read())
    except llm_config.LLMConfigRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.put("/api/llm/config", response_model=LLMConfigRead)
def put_llm_config(req: LLMConfigWrite) -> LLMConfigRead:
    """校验并原子保存结构化 LLM 配置，旧文件先备份。"""
    try:
        return LLMConfigRead(**llm_config.write(req.model_dump()))
    except llm_config.LLMConfigRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except llm_backend.LLMConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/llm/config/test", response_model=LLMConfigTestRead)
def test_llm_config(req: LLMConfigTest) -> LLMConfigTestRead:
    """用临时配置发一个最小请求，不写入配置文件。"""
    try:
        provider = req.provider
        name = provider.get("name")
        cfg = llm_config.build_test_config(provider)
        _, selected = llm_backend.resolve_provider(cfg, "test", name)
        text, used = llm_backend.ask_detailed(
            "只回复 OK，不要添加其它文字。", selected)
        return LLMConfigTestRead(ok=True, model=used.get("model") or selected.get("model"),
                                 message=(text or "已收到响应").strip()[:120])
    except (llm_config.LLMConfigRejected, llm_backend.LLMConfigError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BaseException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/llm/usage", response_model=UsageRead)
def get_llm_usage() -> UsageRead:
    """模型调用账本：今天 / 累计 / 分功能 + 最近几十条明细。只读。"""
    vault = vault_path()
    data = core.usage_summary(core.load_usage(vault))
    cfg, _ = _llm_config(vault)
    roles = {r: v for r, v in (cfg.get("roles") or {}).items() if isinstance(v, str)}
    # 卡片产出和花费**在这里才第一次凑到一起**：账本知道花了多少，卡片流水知道换来了什么。
    # 两边都不存对方的数，接口现拼（同日历那条纪律：派生，不落第二份）。
    return UsageRead(**data, roles=roles, provider="、".join(sorted(set(roles.values()))),
                     cards=core.card_stats(vault, data["date"]),
                     # **只看当前角色指向谁**。原来还 or 了一句 `totals.cost_usd 非零`，
                     # 于是换到不报价的 provider 之后，账本里那些历史金额会把这个开关顶成真，
                     # 页面继续摆 $ ——今天花了多少显示成 $0.00、每张落地的卡 ≈ $0.00。
                     # 那不是"今天没花钱"，是这个 provider 压根不报价（2026-09-21 千问）。
                     cost_known=_reports_cost(cfg, roles))


def _llm_config(vault):
    from .paths import core as _c  # noqa: F401  确保 sys.path 已注入
    import llm_backend
    return llm_backend.load_config()


def _reports_cost(cfg: dict, roles: dict) -> bool:
    """只有 claude-cli 会把花了多少钱一起返回；其他 provider 只有 token。"""
    providers = cfg.get("providers") or {}
    return any((providers.get(p) or {}).get("type") == "claude-cli" for p in roles.values())


@app.get("/api/coach/today", response_model=CoachToday)
def get_coach_today(project: str | None = None) -> CoachToday:
    """今天可以动手的事，按固定优先级排。不调 LLM——"今天干什么"是排序不是生成。"""
    return curation.coach_today(vault_path(), project)


@app.post("/api/projects/{project_id}/sync")
def post_project_sync(project_id: str, body: dict) -> dict:
    """把项目里已建成、还没上全局图的点放到全局图上（落 draft）。**坐标不搬。**"""
    try:
        return projects_svc.sync_to_global(vault_path(), project_id,
                                           int(body.get("base_revision") or 0))
    except projects_svc.PlansRejected as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except curation.PlaceRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/projects/propose", response_model=PlanProposal)
def post_projects_propose(req: PlanProposeRequest) -> PlanProposal:
    """目标 → 分阶段知识点清单（LLM，learn 角色）。只提议，不写任何文件。"""
    return projects_svc.propose(vault_path(), req)


@app.get("/api/review/due")
def get_review_due() -> dict:
    return curation.review_due(vault_path())


@app.post("/api/review/{node_id}", response_model=ReviewDone)
def post_review(node_id: str, req: ReviewRequest | None = None) -> ReviewDone:
    """记一次复习：只写 review-log.json，不碰 md，也不碰 layout。

    body 可选带三档 grade（记得 / 模糊 / 忘了）；不带 body 等价于「记得」。
    """
    try:
        return curation.mark_reviewed(vault_path(), node_id, (req or ReviewRequest()).grade)
    except curation.PlaceRejected as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/quiz", response_model=QuizSet)
def post_quiz(req: QuizRequest) -> QuizSet:
    """出题：调 LLM（review 角色），可能耗时数秒。只读，不写任何文件。"""
    from . import quiz as qz
    return qz.generate(vault_path(), req)


@app.get("/api/quiz/open")
def get_quiz_open() -> dict:
    """上次出了还没交卷的那份题。前端启动时问一次，别让烧掉的那次调用白费。"""
    return {"quiz": core.load_open(vault_path())}


@app.delete("/api/quiz/open")
def drop_quiz_open() -> dict:
    """明确放弃这份卷子。"""
    core.clear_open(vault_path())
    return {"ok": True}


@app.post("/api/quiz/diagnose", response_model=QuizDiagnosis)
def post_quiz_diagnose(req: QuizDiagnoseRequest) -> QuizDiagnosis:
    """整轮比对我的作答与标准答案：漏掉点、记错点、按档位的建议档位，外加一份完整答案。

    **不碰 md，但会写题库**（`.knowrary/question-pool.json` 的 `full_answer`）——
    完整答案已经生成出来了，不落盘等于下次为同一道题再付一次钱。复习调度仍然只由交卷那一头动。
    """
    from . import quiz as qz
    return qz.diagnose(vault_path(), req.answers, req.level)


@app.post("/api/quiz/grade", response_model=QuizGraded)
def post_quiz_grade(req: QuizGradeRequest) -> QuizGraded:
    """交卷：答题明细进 quiz-log.json，每个考点按最差档位推进一次复习调度。不碰 md。"""
    from . import quiz as qz
    return qz.grade(vault_path(), req.answers)


@app.get("/api/assets")
def get_assets() -> dict:
    """vault 的 assets/ 里有哪些图片。位置记在 layout.images，文件本身是用户的东西。"""
    return {"items": assets.listing(vault_path())}


@app.get("/api/asset/{name}")
def get_asset(name: str) -> FileResponse:
    try:
        path = assets.resolve(vault_path(), name)
    except assets.AssetRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"assets/{name} 不存在")
    return FileResponse(str(path), media_type=assets.MIME.get(path.suffix.lower()))


@app.post("/api/asset/{name}")
async def post_asset(name: str, request: Request, overwrite: bool = False) -> dict:
    """上传一张图片到 assets/。原始 body 直传，不引入 multipart 依赖。"""
    try:
        return assets.save(vault_path(), name, await request.body(), overwrite=overwrite)
    except assets.AssetRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/rename", response_model=RenameResult)
def post_rename(req: RenameRequest) -> RenameResult:
    """改一个知识点的 id，并把所有指向它的引用一起迁走。默认只算影响面。

    为什么要有这个接口：文件名就是 id，在 Obsidian 或 IDE 里直接改名会一次断四类引用
    （`[[链接]]` / 画布位置与边样式 / 复习与答题记录 / 学习计划），而且事后补不回来——
    旧 id 已经没了，系统只看得到"少了一个、多了一个"。所以改名必须是一个动作。
    """
    vault = vault_path()
    index = current_index(vault)
    if req.base_revision != index["revision"]:
        raise HTTPException(status_code=409, detail={"error": "索引已经变了，请重新拉取后再改名",
                                                     "current_revision": index["revision"]})
    try:
        impact = core.plan_rename(vault, index, req.old_id, req.new_id)
    except core.RenameRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if req.dry_run:
        return RenameResult(impact=RenameImpact(**impact), index_revision=index["revision"])
    core.backup_rename(vault, impact)
    core.apply_rename(vault, impact)
    invalidate(vault)
    return RenameResult(impact=RenameImpact(**impact), applied=True,
                        index_revision=current_index(vault)["revision"])


@app.post("/api/merge", response_model=MergeResult)
def post_merge(req: MergeRequest) -> MergeResult:
    """把两张重复的卡并成一张：边、引用、位置、复习与答题记录全并到保留的那张上。

    正文是**追加**不是智能合并——两段讲同一件事的话怎么揉只有人知道，
    这里只保证内容不丢，并标出它从哪并来。默认只算影响面。
    """
    vault = vault_path()
    index = current_index(vault)
    if req.base_revision != index["revision"]:
        raise HTTPException(status_code=409, detail={"error": "索引已经变了，请重新拉取后再合并",
                                                     "current_revision": index["revision"]})
    try:
        impact = core.plan_merge(vault, index, req.keep_id, req.drop_id)
    except core.MergeRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if req.dry_run:
        return MergeResult(impact=MergeImpact(**impact), index_revision=index["revision"])
    core.backup_rename(vault, {"path": impact["drop_path"],
                               "files": [impact["keep_path"], *impact["files"]]}, op="merge")
    core.apply_merge(vault, impact)
    invalidate(vault)
    return MergeResult(impact=MergeImpact(**impact), applied=True,
                       index_revision=current_index(vault)["revision"])


@app.post("/api/audit", response_model=AuditReport)
def post_audit(req: AuditRequest) -> AuditReport:
    """卡上的「审核」：只审不写，结论记在这张卡名下（cards.jsonl 的 audited 事件）。

    之后点「写入」，只要改法一个字没动，`/api/changes` 就直接用这份结论，不再问第二遍。
    审核开关关着时也能调：确定性检查照跑，只是不问模型。
    """
    vault = vault_path()
    index = current_index(vault)
    payload = [c.model_dump(exclude_none=True) for c in req.changes]
    try:
        edits = core.plan(vault, payload, index)
    except core.WriteConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except core.ChangeRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    report = audit.check(vault, index, edits, payload)
    report.stamp = audit.stamp_of(payload)
    if req.card:
        core.card_audited(vault, req.card, report.stamp, report.model_dump())
    return report


@app.post("/api/changes", response_model=ChangeResult)
def post_changes(changeset: ChangeSet) -> ChangeResult:
    """Markdown 写回的唯一入口：默认只预览，dry_run=false 才落盘（落盘前自动备份）。"""
    vault = vault_path()
    index = current_index(vault)
    if changeset.base_revision and changeset.base_revision != index["revision"]:
        raise HTTPException(status_code=409, detail={
            "message": f"索引已更新（当前 revision {index['revision']}）", "current_revision": index["revision"],
            "hint": "重新拉取 /api/index 后再提交"})
    payload = [c.model_dump(exclude_none=True) for c in changeset.changes]
    try:
        edits = core.plan(vault, payload, index)
    except core.WriteConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except core.ChangeRejected as exc:
        # 写回被拒往往说明**工具本身有问题**（字段白名单漏一项这种），不是人手滑，
        # 攒起来才看得出"同一类拒绝反复出现"
        core.record_issue(vault, "write", str(exc), where="/api/changes")
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    files = [FileDiff(path=e.rel, notes=e.notes, diff=curation.diff_of(e)) for e in edits]
    if changeset.dry_run:
        # 预览只跑确定性那一段：改一行摘要就重算一次 diff，每次都问模型没道理
        return ChangeResult(applied=False, files=files, index_revision=index["revision"],
                            audit=audit.preview(vault, index, edits))
    if changeset.force and not core.audit_force_allowed():
        raise HTTPException(status_code=422, detail="「仍然写入」在设置里被关掉了：先改审核意见，或去设置里打开它")
    report = audit.gate(vault, index, edits, payload, changeset.force, changeset.card or "")
    if report.blocked:
        # **不是 HTTP 错误**：审核挡下是这条链路的正常结局之一，卡片要把结论和建议摆出来，
        # 人看完可以改、也可以「仍然写入」。抛 4xx 的话前端只剩一句红字。
        return ChangeResult(applied=False, files=files, index_revision=index["revision"], audit=report)
    snapshot = core.commit(vault, edits)
    invalidate(vault)                      # md 变了，索引缓存作废
    core.card_applied(vault, changeset.card or "")     # 采纳率的分子：**只在真落盘之后记**
    return ChangeResult(applied=True, files=files, backup=snapshot or None,
                        index_revision=current_index(vault)["revision"], audit=report)


@app.post("/api/import", response_model=ImportResult)
def post_import(req: ImportRequest) -> ImportResult:
    """导入一篇笔记的方案：新建节点 / 补充老节点 / 待审边。默认只预览（每个文件的 diff），
    dry_run=false 才落盘；低置信边只在落盘时记进 pending.json。"""
    try:
        return importing.run(vault_path(), req)
    except importing.StaleIndex as exc:
        raise HTTPException(status_code=409, detail={
            "message": str(exc), "current_revision": exc.current,
            "hint": "重新拉取 /api/index 后再提交"}) from exc
    except importing.ImportRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/import/propose", response_model=ImportProposal)
def post_import_propose(req: ImportProposeRequest) -> ImportProposal:
    """文章 → 方案（调一次 learn 角色的 LLM）→ 顺手 dry-run。返回方案、diff、待审边、认领 / 撞名 / 孤立。"""
    try:
        return importing.propose(vault_path(), req)
    except importing.ImportRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LLMFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/import/sources", response_model=SourcesRead)
def get_import_sources() -> SourcesRead:
    """vault 里可以当素材的 md / txt（节点目录之外）。"""
    return importing.sources(vault_path())


@app.get("/api/import/source", response_model=SourceText)
def get_import_source(path: str) -> SourceText:
    try:
        return importing.read_source(vault_path(), path)
    except importing.ImportRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/summarize", response_model=SummaryDraft)
def post_summarize(req: SummarizeRequest) -> SummaryDraft:
    """把几个点概括成一个上位节点的草稿（一次 learn 调用，不写盘）。写入走 /api/changes。"""
    try:
        return summarize_svc.propose(vault_path(), req)
    except summarize_svc.SummarizeRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LLMFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class FreshStatic(StaticFiles):
    """每次都回源问一句"变了没"（`Cache-Control: no-cache`）。

    构建产物的文件名**不带 content hash**（见 web/vite.config.js 里的理由），所以
    "文件换了"这件事没法靠名字告诉浏览器——只能靠这个头。no-cache 不是不缓存：
    浏览器照旧存着，只是每次用之前拿 ETag 问一下，没变就 304，一个字节都不下。
    """

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


def _mount_web() -> None:
    """有构建产物时同源托管前端（运行期零 Node）。"""
    if not WEB_DIST.exists():
        return
    assets = WEB_DIST / "assets"
    if assets.exists():
        app.mount("/assets", FreshStatic(directory=str(assets)), name="assets")

    @app.get("/")
    def index_html() -> FileResponse:
        # index.html 本来就每次都要重读：它是那张指向各个 chunk 的清单
        return FileResponse(str(WEB_DIST / "index.html"),
                            headers={"Cache-Control": "no-cache"})


_mount_web()
