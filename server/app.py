"""FastAPI 本地服务：只读 index、读写 layout，静态托管前端构建产物。

边界（设计文档 3.4）：这一层永远不改 Markdown。改 md 只能走阶段 3 的 ChangeSet。
"""
from __future__ import annotations

import datetime as dt
import json
import logging

from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import assets, chat as chat_svc, curation, projects as projects_svc
from .contracts import (CalendarRead, ChangeResult, ChangeSet, ChatRequest, CoachToday, FileDiff,
                        InboxRead,
                        LayoutPatch, LayoutRead,
                        LayoutSaved, MergeImpact, MergeRequest, MergeResult, NodeDetail,
                        PlanProposal, PlanProposeRequest, PlaceRequest,
                        PlaceResult, ProjectsRead, ProjectsSaved, ProjectsWrite, QuizDiagnoseRequest,
                        QuizDiagnosis, QuizGradeRequest, QuizGraded, QuizRequest, QuizSet, RenameImpact,
                        RenameRequest, RenameResult, ReviewDone, ReviewRequest, SuggestRequest,
                        SuggestResult, UsageRead)
from .index_service import current_index, invalidate
from .layout_store import (LayoutBroken, PatchRejected, RevisionConflict, apply_patch, find_orphans,
                           load_or_init)
from .paths import DEFAULT_LAYOUT, WEB3D_DIST, WEB_DIST, core, vault_path

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


@app.get("/api/health")
def health() -> dict:
    vault = vault_path()
    index = current_index(vault)
    layout, generated = load_or_init(vault, index)
    return {"vault": str(vault), "index_revision": index["revision"], "stats": index["stats"],
            "layout_revision": layout.revision, "layout_generated": generated,
            "web_dist": WEB_DIST.exists(), "web3d": WEB3D_DIST.exists()}


@app.get("/api/index")
def get_index() -> dict:
    """派生缓存，只读。前端据此渲染节点与边，真值永远在 md。"""
    return current_index(vault_path())


def _layout_target(vault, layout: str | None) -> tuple[str, dict | None]:
    """`?layout=<项目 id>` 指到项目画布；不给就是全局图。

    项目 id 走 `core.ID_OK`（只允许 ASCII）——它直接当文件名用，认不出的一律拒，
    绝不让它拼出路径。
    """
    if not layout or layout == DEFAULT_LAYOUT:
        return DEFAULT_LAYOUT, None
    if not core.ID_OK.match(layout):
        raise HTTPException(status_code=422, detail=f"非法的 layout 名 `{layout}`")
    project = (core.load_projects(vault).get("projects") or {}).get(layout)
    if project is None:
        raise HTTPException(status_code=404, detail=f"没有 `{layout}` 这个项目")
    return layout, project


@app.get("/api/layout", response_model=LayoutRead)
def get_layout(layout: str | None = None) -> LayoutRead:
    vault = vault_path()
    index = current_index(vault)
    name, project = _layout_target(vault, layout)
    doc, generated = load_or_init(vault, index, name, project)
    return LayoutRead(layout=doc, orphans=find_orphans(doc, index, vault),
                      index_revision=index["revision"], generated=generated)


@app.patch("/api/layout", response_model=LayoutSaved)
def patch_layout(patch: LayoutPatch, layout: str | None = None) -> LayoutSaved:
    """高频写入口：拖拽、折叠、便签。只改 layout 文件，不进确认流程。"""
    vault = vault_path()
    index = current_index(vault)
    name, project = _layout_target(vault, layout)
    try:
        doc, orphans, backup = apply_patch(vault, patch, index, name, project)
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={
            "message": str(exc), "current_revision": exc.current.revision,
            "hint": "重新 GET /api/layout 后基于新 revision 重试"}) from exc
    except PatchRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return LayoutSaved(revision=doc.revision, updated_at=doc.updated_at or "", orphans=orphans,
                       backup=backup)


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


@app.get("/api/digest")
def get_digest() -> dict:
    """图谱欠账清单：草稿 / 待复习 / stub / 跨分组桥 / 重复候选 / 环。只读。"""
    return curation.digest(vault_path())


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
    try:
        return projects_svc.write(vault_path(), req)
    except projects_svc.PlansConflict as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc), "current_revision": exc.current}) from exc
    except projects_svc.PlansRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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


@app.get("/api/llm/usage", response_model=UsageRead)
def get_llm_usage() -> UsageRead:
    """模型调用账本：今天 / 累计 / 分功能 + 最近几十条明细。只读。"""
    vault = vault_path()
    data = core.usage_summary(core.load_usage(vault))
    cfg, _ = _llm_config(vault)
    roles = {r: v for r, v in (cfg.get("roles") or {}).items() if isinstance(v, str)}
    return UsageRead(**data, roles=roles, provider="、".join(sorted(set(roles.values()))),
                     cost_known=bool(data["totals"]["cost_usd"]) or _reports_cost(cfg, roles))


def _llm_config(vault):
    from .paths import core as _c  # noqa: F401  确保 sys.path 已注入
    import llm_backend
    return llm_backend.load_config(vault)


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
        return ChangeResult(applied=False, files=files, index_revision=index["revision"])
    snapshot = core.commit(vault, edits)
    invalidate(vault)                      # md 变了，索引缓存作废
    return ChangeResult(applied=True, files=files, backup=snapshot or None,
                        index_revision=current_index(vault)["revision"])


def _mount_web() -> None:
    """有构建产物时同源托管前端（运行期零 Node）。/3d 是只读的 3D 总览原型，可随时删。"""
    if WEB3D_DIST.exists():
        app.mount("/3d", StaticFiles(directory=str(WEB3D_DIST), html=True), name="web3d")
    if not WEB_DIST.exists():
        return
    assets = WEB_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/")
    def index_html() -> FileResponse:
        return FileResponse(str(WEB_DIST / "index.html"))


_mount_web()


