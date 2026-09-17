"""项目读写（重构方案 一期）：整份替换 + base_revision 乐观并发，进度与时间账一律现算。

为什么不像 layout 那样上 JSON Merge Patch：项目是人手编排的小文档（一个项目几十个点），
整份发过来最直白；而 layout 是高频拖拽、必须增量。并发仍然用同一套 base_revision 挡住。

**项目是视角不是容器**：它只引用 node_id，掌握度与复习调度全局唯一（重构方案 §1）。
一个项目下挂 N 份清单，`kind` 属于清单（决定拆解模板与出题口径），不属于项目。
"""
from __future__ import annotations

import datetime as dt
import logging
import re

from pathlib import Path

from .contracts import (ID_PATTERN, PlanPoint, PlanProposal, PlanProposeRequest, PlanStage,
                        Project, ProjectsDoc, ProjectsRead, ProjectsSaved, ProjectsWrite)
from .index_service import current_index
from .levels import fragment as level_fragment
from .llm_call import ask, clean_layer, clean_year, parse_json
from .paths import core


log = logging.getLogger(__name__)
_ID_RE = re.compile(ID_PATTERN)
MAX_STAGES = 6           # 再多就不是计划了，是清单
MAX_POINTS = 40          # 整份计划的点数上限：凑出来的点一样要花时间学


class PlansRejected(Exception):
    """请求本身不合法：重复的知识点、阶段为空等。"""


class PlansConflict(Exception):
    """base_revision 与服务端不一致，客户端要重新拉取后再提交。"""

    def __init__(self, current: int) -> None:
        super().__init__(f"项目已被改过（当前 revision {current}）")
        self.current = current


def _derived(vault: Path, doc: dict) -> tuple[dict, dict]:
    """进度、学/考双态、时间账全部现算：存下来就是第二份真值，必然与复习记录漂移。

    双态里的「考」要错题本参与判定（错题压过自评，重构方案 §4），所以这里要多读一份 quiz-log。
    """
    wrong = {w["id"] for w in core.wrong_nodes(core.load_quiz_log(vault), 999) if w["wrong"]}
    progress = core.all_progress(doc, current_index(vault), core.load_log(vault), wrong=wrong)
    return progress, core.all_schedules(doc, progress)


def read(vault: Path) -> ProjectsRead:
    doc = core.load_projects(vault)
    progress, schedules = _derived(vault, doc)
    return ProjectsRead(doc=ProjectsDoc(**doc), progress=progress, schedules=schedules,
                        index_revision=current_index(vault)["revision"])


def _check(projects: dict[str, Project]) -> None:
    """重复点**按清单逐份查**：同一个点在「学习主线」和「面试清单」里各出现一次是合法的
    （项目是视角，两份清单本来就会重叠），只有同一份清单内重复才算错。"""
    for pid, project in projects.items():
        if not core.ID_OK.match(pid or ""):
            raise PlansRejected(f"项目 id `{pid}` 不合法：只允许 ASCII 字母、数字、`_`、`-`"
                                f"（它会成为对话留档的目录名）")
        for ls in project.lists:
            seen: set[str] = set()
            for stage in ls.stages:
                for point in stage.points:
                    if point.id in seen:
                        raise PlansRejected(
                            f"项目「{project.name}」的清单「{ls.name}」里，知识点 `{point.id}` 出现了两次")
                    seen.add(point.id)


def write(vault: Path, req: ProjectsWrite) -> ProjectsSaved:
    _check(req.projects)
    doc = core.load_projects(vault)
    if req.base_revision != doc.get("revision", 0):
        raise PlansConflict(doc.get("revision", 0))
    doc["projects"] = {pid: pr.model_dump() for pid, pr in req.projects.items()}
    doc = core.save_projects(vault, doc)
    progress, schedules = _derived(vault, doc)
    return ProjectsSaved(revision=doc["revision"], progress=progress, schedules=schedules)


# ---------------------------------------------------------------- 目标 → 知识点清单（LLM）

_PROMPTS: dict[str, str] = {}
# 三套口径，三份模板。同一个 prompt 加 if/else 只会三头不讨好（F10.3b）：
#   学习 = 按依赖顺序拆（先学的在前）
#   面试 = 按会怎么问拆（组名像面试轮次）
#   领域 = 按覆盖度铺（组名是子领域，求不漏而不是求顺序）
# 数据结构、调度、进度、时间账完全共用——**「初始化一个领域」不是第二个功能，是第三种口径**。
TEMPLATES = {"学习": "plan", "面试": "plan-interview", "领域": "plan-map"}
OPS = {"学习": "plan-propose", "面试": "plan-interview", "领域": "plan-map"}


def _load_prompt(kind: str) -> str:
    name = TEMPLATES.get(kind, TEMPLATES["学习"])
    if name not in _PROMPTS:
        path = Path(__file__).resolve().parents[1] / "tools" / "knowrary" / "prompts" / f"{name}.md"
        _PROMPTS[name] = path.read_text(encoding="utf-8")
    return _PROMPTS[name]


def _budget_text(req: PlanProposeRequest, today: dt.date) -> str:
    """把时间预算写成人话喂给模型。**算术在这边做**——模型只负责给每个点估一档负荷。

    没填目标日期就只交代每周投入；一句不提时间的 prompt，模型排出来的阶段永远没有截止日
    （现网 plans.json 里 20 个阶段 deadline 全是 null，就是这么来的）。
    """
    target = core.as_date(req.target_date)
    weekly = max(1, int(req.weekly_hours or core.DEFAULT_WEEKLY_HOURS))
    if not target:
        return f"我每周大概能拿出 **{weekly} 小时**学这件事，没有硬性截止日期。"
    days = (target - today).days
    if days <= 0:
        return (f"我的目标日期是 **{req.target_date}**，但它已经到了或过了。"
                f"我每周大概能拿出 **{weekly} 小时**——按最精炼的口径拆，只留真正绕不开的点。")
    return (f"我的目标日期是 **{req.target_date}**，距今 **{days} 天**；"
            f"每周大概能拿出 **{weekly} 小时**，也就是这段时间总共约 "
            f"**{round(days * weekly / 7)} 小时**。")


# 抽象层与年份：**三份模板共用的一小段**。
#
# 口径（学习 / 面试 / 领域）决定"拆成什么样"，而"自注意力属于哪一层、哪年提出"
# 和口径无关——所以和 level / mode 那两段一样抽出来注入，不在三份模板里各抄一遍。
#
# 它们不参与排期、不影响进度，唯一的用途是**新建知识点时把两个下拉预填好**。
# 之所以塞进拆解这一次调用：模型此刻正在逐个点地想"这是什么"，顺手多答两个字段几乎不要钱；
# 等到新建对话框打开时再单开一次调用去问，既慢又贵。
# 不带编号：三份模板的要求列表长度不一样（8 / 6 / 7 条），写死 9. 10. 会有两份对不上号。
_LAYER_YEAR = """
- **`layer`（抽象层）**，从这七档里挑一个原样填：
  {{layers}}。
  它决定这个点将来落在历史视图的哪条泳道里。
  七档的意思：`理论`＝数学与计算模型；`硬件`＝电路与器件；`体系结构`＝指令集与处理器组织；
  `汇编接口`＝ ABI、链接、系统调用这一层；`系统软件`＝操作系统、编译器、运行时；
  `高级语言`＝语言与框架；`AI应用`＝模型与应用层。
  **拿不准就留空字符串**——填错比不填更麻烦（它会把点放进错的泳道）。
- **`year`（年份）**：这个概念**被提出 / 定型**的那一年，四位数字。
  只填**查得准**的（论文、标准、首个实现的年份）；含糊的、我自己造的名字一律留 `null`。
  宁可空着：填错的年份会在历史视图上把这个点摆到错误的位置。

"""


_COMPRESS = """
## 这次要的是「速学版」

我知道时间不够，但我坚持在这个期限内学完，所以这一份要**砍到最精炼**：

- 阶段最多 3 个，每个阶段 3～5 个点，整份**不超过 12 个**；
- 只留**绕不开**的点：不学它后面就看不懂、或者目标直接落空的那些；
- `why` 写清楚**为什么它砍不掉**；
- 被砍掉的点**必须列进顶层的 `dropped` 数组**（与 `stages` 平级，同样带 id / name / why，
  why 写"为什么这次先不学"）——
  我要知道自己跳过了什么，否则会以为已经学全了。
"""


def _other_projects_points(vault: Path, exclude: str | None) -> dict[str, list[str]]:
    """别的项目已经列过的点：{点 id: [项目名…]}。

    项目是视角，重叠**合法且免费**（同一个点属于两个项目，掌握度还是同一个）——
    所以这里不去重、不阻止，只是把它标出来：
    你建 MHA 项目时会看见「自注意力」已经在 Transformer 项目里，
    然后自己决定是复用还是这次不列。
    """
    doc = core.load_projects(vault)
    out: dict[str, list[str]] = {}
    for pid, pr in (doc.get("projects") or {}).items():
        if pid == exclude:
            continue
        name = pr.get("name") or pid
        for ls in core.lists_of(pr):
            for nid in core.stage_points(ls.get("stages") or []):
                names = out.setdefault(nid, [])
                if name not in names:
                    names.append(name)
    return out


def _build_prompt(req: PlanProposeRequest, index: dict, today: dt.date,
                  elsewhere: dict[str, list[str]] | None = None) -> str:
    real = [n for n in index["nodes"] if not n.get("virtual")]
    ids = "、".join(sorted(n["id"] for n in real)) or "（图谱还是空的）"
    fields = "、".join(sorted({n.get("field") for n in real if n.get("field")})) or "（还没有领域）"
    goal = req.goal if not req.plan_name else f"{req.plan_name}：{req.goal}"
    coach = f"，方向是{req.coach.strip()}" if req.coach.strip() else ""
    mine = "、".join(req.known_points) or "（这份清单还是空的）"
    # 别的项目已经列过的点也喂进去：不喂它就会把「自注意力」在每个项目里各拆一遍
    others = "；".join(f"{nid}（在{'、'.join(names)}里）"
                       for nid, names in sorted((elsewhere or {}).items())[:80])
    known = mine + (f"\n\n**别的项目已经列过的点**（可以复用同一个 id，不要另起近义的新名字）："
                    f"\n{others}" if others else "")
    return (_load_prompt(req.kind)
            .replace("{{known_points}}", known)
            .replace("{{coach}}", coach)
            .replace("{{goal}}", goal)
            .replace("{{budget}}", _budget_text(req, today))
            .replace("{{mode}}", _COMPRESS if req.mode == "速学" else "")
            .replace("{{layer_year}}", _LAYER_YEAR.replace("{{layers}}", " / ".join(core.LAYERS)))
            .replace("{{level}}", level_fragment(req.level, "plan"))
            .replace("{{node_count}}", str(len(real)))
            .replace("{{existing_ids}}", ids)
            .replace("{{fields}}", fields))


def propose(vault: Path, req: PlanProposeRequest) -> PlanProposal:
    """目标 → 分阶段知识点清单。

    走 **learn 角色**而不是 review：拆大纲是"产生新结构"，和文章拆节点同一类活儿；
    review 那个角色留给审校、去重、判分。

    只提议，不写任何文件——人在面板上逐条增删、点了保存才进 plans.json（4.4）。
    """
    index = current_index(vault)
    today = dt.date.today()
    op = OPS.get(req.kind, OPS["学习"])
    if req.mode == "速学":
        op += "-fast"           # 速学单独记账，否则算不清"赶工"烧了多少
    elsewhere = _other_projects_points(vault, req.project)
    raw = ask(vault, "learn", _build_prompt(req, index, today, elsewhere), op=op)
    known = {n["id"] for n in index["nodes"] if not n.get("virtual")}
    built = {n["id"] for n in index["nodes"]
             if not n.get("virtual") and not n.get("stub") and n.get("path")}
    return _parse_proposal(parse_json(raw, f"plan goal={req.goal[:30]}", vault=vault), known, built, req, today,
                           elsewhere)


def _clean_points(items, seen: set[str], warnings: list[str]) -> list[PlanPoint]:
    """挑出合法的知识点。id 将来是 md 文件名，非法字符必须在这里就拦掉。"""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        pid = str(it.get("id") or "").strip()
        if not pid or not _ID_RE.match(pid):
            warnings.append(f"丢弃非法的知识点 id `{pid or '(空)'}`（不能有空格或 / \\ : * ? \" < > |）")
            continue
        if pid in seen:
            warnings.append(f"知识点 `{pid}` 重复出现，只保留第一处")
            continue
        seen.add(pid)
        load = str(it.get("load") or "").strip()
        out.append(PlanPoint(id=pid, name=str(it.get("name") or pid), why=str(it.get("why") or ""),
                             load=load if load in core.LOADS else core.DEFAULT_LOAD,
                             layer=clean_layer(it.get("layer")), year=clean_year(it.get("year"))))
    return out



def _parse_proposal(data: dict, known: set[str], built: set[str], req: PlanProposeRequest,
                    today: dt.date, elsewhere: dict[str, list[str]] | None = None) -> PlanProposal:
    stages, warnings, seen = [], [], set()
    for st in (data.get("stages") or [])[:MAX_STAGES]:
        if not isinstance(st, dict):
            continue
        points = _clean_points(st.get("points"), seen, warnings)
        if len(seen) > MAX_POINTS:
            warnings.append(f"整份计划截到 {MAX_POINTS} 个点，剩下的没要")
            points = points[: max(0, MAX_POINTS - (len(seen) - len(points)))]
        if points:
            stages.append(PlanStage(name=str(st.get("name") or f"第 {len(stages) + 1} 阶段"), points=points))
        if len(seen) >= MAX_POINTS:
            break
    if (data.get("stages") or []) and not stages:
        warnings.append("模型给的清单里没有一个合法的知识点")
    field = str(data.get("field") or "").strip()
    if field and _ID_RE.match(field) is None:
        warnings.append(f"丢弃非法的领域名 `{field}`（它会成为一个目录名）")
        field = ""
    dropped = _clean_points(data.get("dropped"), set(seen), []) if req.mode == "速学" else []
    mine = {p.split("（")[0] for p in req.known_points}
    duplicates = sorted(seen & mine)
    if req.target_date and not core.as_date(req.target_date):
        warnings.append(f"看不懂的目标日期 `{req.target_date}`，时间账按「没有截止日」算了")
    schedule = _schedule_for(stages, built, req, today)
    in_projects = {nid: names for nid, names in (elsewhere or {}).items() if nid in seen}
    return PlanProposal(stages=stages, schedule=schedule, dropped=dropped, duplicates=duplicates,
                        in_projects=in_projects,
                        suggested_field=field, notes=str(data.get("notes") or ""),
                        existing=sorted(seen & known), warnings=warnings)


def _schedule_for(stages: list[PlanStage], built: set[str],
                  req: PlanProposeRequest, today: dt.date) -> dict:
    """给提议算一份时间账，顺手把建议截止日**写进阶段**——采纳时就带着日期进清单。

    **可行性判断在这里做完，不问模型。** 模型估负荷（生成），除法归服务端（确定性计算），
    与 F10.3「调度不用 LLM」是同一条分工。
    """
    ls = {"target_date": req.target_date, "stages": [st.model_dump() for st in stages]}
    schedule = core.schedule_of(ls, built, req.weekly_hours, today)
    for st, row in zip(stages, schedule["stages"]):
        st.deadline = row["suggested_deadline"]
        row["deadline"] = st.deadline
    return schedule


# ---------------------------------------------------------------- 同步到全局（重构方案 §5A）

def sync_to_global(vault: Path, project_id: str, base_revision: int) -> dict:
    """把项目里已经建出来、但还没上全局图的点放到全局图上，**落 draft**。

    三条纪律：

    1. **不搬坐标。** 项目画布里的排版是你为了想清楚而摆的，全局图有自己的结构；
       搬过去只会打乱主图。所以这里调的是 `/api/place`（按邻居投票找位置），
       而不是把项目画布的 x/y 复制过去。
    2. **已经在全局里的跳过**，不重复放。
    3. **命中重复候选的不静默跳过，列出来让人选**——去重要发生在写入之前，不是之后。
       只按 id 精确比对拦不住近义词（`RNN` / `RNN与长程依赖` 这种），
       而"回头去欠账里清"正是最容易不做的那件事。这里只提议，绝不自动合并：
       自动并掉两个看起来像的节点，比多一个重复节点严重得多——前者会悄悄吃掉一份正文。
    """
    from . import curation                      # 延迟导入：curation 也要用到本模块的 read
    from .contracts import PlaceRequest

    doc = core.load_projects(vault)
    project = (doc.get("projects") or {}).get(project_id)
    if project is None:
        raise PlansRejected(f"没有 `{project_id}` 这个项目")

    index = current_index(vault)
    _, layout = curation.load_pair(vault)          # load_pair 返回 (index, layout)
    on_canvas = set(layout.model_dump()["nodes"])
    built = {n["id"] for n in index["nodes"]
             if not n.get("virtual") and not n.get("stub") and n.get("path")}

    dup_of: dict[str, list[dict]] = {}
    for pair in core.duplicates(index):
        dup_of.setdefault(pair["a"], []).append({"id": pair["b"], "reason": pair["reason"]})
        dup_of.setdefault(pair["b"], []).append({"id": pair["a"], "reason": pair["reason"]})

    todo, skipped, dups = [], [], []
    for nid in core.point_ids(doc, project_id):
        if nid in on_canvas:
            skipped.append({"id": nid, "reason": "已经在全局图上了"})
        elif nid not in built:
            skipped.append({"id": nid, "reason": "还没建出来（或只有壳），先把它建了"})
        elif nid in dup_of:
            dups.append({"id": nid, "candidates": dup_of[nid][:3]})
        else:
            todo.append(nid)

    placed = []
    if todo:
        result = curation.place(vault, PlaceRequest(base_revision=base_revision, ids=todo,
                                                    state="draft"))
        placed = [p.model_dump() for p in result.placed]
        skipped += result.skipped
    return {"placed": placed, "skipped": skipped, "duplicates": dups,
            "layout_revision": base_revision + (1 if placed else 0)}
