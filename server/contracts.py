"""三份数据契约（pydantic v2）：index / layout / ChangeSet。

- index：派生缓存，真值在 md，服务只读（阶段 1 的 core 生成与校验）。
- layout：结构视图的用户数据，唯一可写入口是 PATCH，带 revision 乐观并发。
- ChangeSet：所有 Markdown 写回的唯一入口（阶段 3 使用，这里先把形状定下来）。

三者互不覆盖：改 layout 不碰 md，改 md 不动 layout（详见设计文档 3.4）。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

LAYOUT_SCHEMA_VERSION = 2
NODE_W, NODE_H = 160.0, 60.0


class Strict(BaseModel):
    """契约默认拒绝未知字段：拼错的键必须报错，不能被静默丢弃。"""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- layout

class Point(Strict):
    x: float
    y: float


class Viewport(Strict):
    zoom: float = 0.8
    cx: float = 0.0
    cy: float = 0.0


class GroupBox(Strict):
    name: str
    x: float
    y: float
    w: float
    h: float
    parent: str | None = None
    collapsed: bool = False
    pinned: Literal["expanded", "collapsed"] | None = None
    color: str | None = None
    doc: str | None = None    # 这个域的总览文档（一个普通节点 id）；知识仍然只住在 md 里


class NodeBox(Strict):
    x: float
    y: float
    w: float = NODE_W
    h: float = NODE_H
    group: str | None = None
    state: Literal["final", "draft", "ghost"] = "final"
    """ghost = 计划里有、图里还没建的占位。**只出现在项目画布**，不进全局 layout、不进 vault。"""
    placedAt: str | None = None   # noqa: N815  （layout.json 里就是这个键名）
    anchor: str | None = None


class RefCard(Strict):
    id: str
    target: str
    x: float
    y: float
    w: float = NODE_W
    h: float = NODE_H
    group: str | None = None


class StickyNote(Strict):
    id: str
    text: str
    x: float
    y: float
    w: float = 180.0
    h: float = 60.0
    group: str | None = None
    color: str | None = None


class ImageBox(Strict):
    id: str
    file: str
    x: float
    y: float
    w: float
    h: float
    group: str | None = None


class EdgeStyle(Strict):
    """只记录用户手工调过的边（拐点、路由）；key 为 `源->目标#类型`。"""

    vertices: list[Point] = Field(default_factory=list)
    router: str | None = None


class LayoutDoc(Strict):
    schema_version: int = LAYOUT_SCHEMA_VERSION
    revision: int = 0
    updated_at: str | None = None
    viewport: Viewport = Field(default_factory=Viewport)
    groups: dict[str, GroupBox] = Field(default_factory=dict)
    nodes: dict[str, NodeBox] = Field(default_factory=dict)
    refs: list[RefCard] = Field(default_factory=list)
    notes: list[StickyNote] = Field(default_factory=list)
    images: list[ImageBox] = Field(default_factory=list)
    edges: dict[str, EdgeStyle] = Field(default_factory=dict)


# ---------------------------------------------------------------- layout patch

class GroupPatch(Strict):
    """分组的部分更新：只发改动过的字段。"""

    name: str | None = None
    x: float | None = None
    y: float | None = None
    w: float | None = None
    h: float | None = None
    parent: str | None = None
    collapsed: bool | None = None
    pinned: Literal["expanded", "collapsed"] | None = None
    color: str | None = None
    doc: str | None = None


class NodePatch(Strict):
    x: float | None = None
    y: float | None = None
    w: float | None = None
    h: float | None = None
    group: str | None = None
    state: Literal["final", "draft", "ghost"] | None = None
    placedAt: str | None = None   # noqa: N815
    anchor: str | None = None


class LayoutPatch(Strict):
    """JSON Merge Patch 风格：只发改动过的条目，条目值为 null 表示删除该条目。

    `groups` / `nodes` / `edges` 按条目合并（条目内再按字段合并）；
    `refs` / `notes` / `images` 是带 id 的小集合，给出时整体替换。
    """

    base_revision: int
    viewport: Viewport | None = None
    groups: dict[str, GroupPatch | None] | None = None
    nodes: dict[str, NodePatch | None] | None = None
    edges: dict[str, EdgeStyle | None] | None = None
    refs: list[RefCard] | None = None
    notes: list[StickyNote] | None = None
    images: list[ImageBox] | None = None


class LayoutSaved(Strict):
    revision: int
    updated_at: str
    orphans: list[dict[str, Any]] = Field(default_factory=list)
    backup: str | None = None   # 整体重排前的快照路径（vault 相对路径）


class LayoutRead(Strict):
    layout: LayoutDoc
    orphans: list[dict[str, Any]] = Field(default_factory=list)
    index_revision: int = 0
    generated: bool = False   # 本次读取时刚自动生成了初始布局


# ---------------------------------------------------------------- index（只读）

class IndexNode(BaseModel):
    model_config = ConfigDict(extra="allow")   # 派生字段可增长，前端按需取用

    id: str
    name: str | None = None
    field: str | None = None
    desc: str | None = None
    status: str = "active"
    year: int | None = None
    stub: bool = False
    out: list[str] = Field(default_factory=list)
    in_: list[str] = Field(default_factory=list, alias="in")
    degree: int = 0


class IndexEdge(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    source: str
    target: str
    type: str
    family: str
    year: int | None = None


class IndexDoc(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: int
    revision: int
    generated_at: str
    content_hash: str
    nodes: list[IndexNode]
    edges: list[IndexEdge]
    families: list[dict[str, Any]]
    stubs: list[str]
    stats: dict[str, Any]
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]


# ---------------------------------------------------------------- ChangeSet（阶段 3）

class Change(Strict):
    type: Literal["add_edge", "remove_edge", "update_edge", "update_frontmatter", "create_node",
                  "update_body", "append_body"]
    source: str                        # create_node 时是新节点的 id（= 文件名）
    path: str | None = None            # create_node 时的落点，vault 相对路径，必须在 nodes/ 下
    target: str | None = None
    relation: str | None = None
    from_relation: str | None = None   # update_edge 时用来定位原来那条边
    year: int | None = None
    note: str | None = None
    fields: dict[str, Any] | None = None
    body: str | None = None            # update_body：整段替换的新正文；append_body：往正文尾部补的那一段
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 1.0


class ChangeSet(Strict):
    base_revision: int                 # 基于哪个 index revision 提出的变更
    changes: list[Change]
    dry_run: bool = True               # 默认只预览；确认后再发一次 dry_run=false


class FileDiff(Strict):
    path: str
    notes: list[str] = Field(default_factory=list)
    diff: str = ""                     # 统一 diff 片段，给人看"改了哪几行"


class ChangeResult(Strict):
    applied: bool
    files: list[FileDiff] = Field(default_factory=list)
    backup: str | None = None          # 写回前的原文快照目录
    index_revision: int = 0


# ---------------------------------------------------------------- Inbox / 放置 / Digest / 复习（阶段 4）

class InboxItem(Strict):
    """一条待入画布的节点：带上建议分组，前端不必再自己算一遍。"""

    id: str
    name: str
    field: str | None = None
    desc: str | None = None
    stub: bool = False
    degree: int = 0
    suggested_group: str | None = None
    suggested_group_name: str | None = None


class InboxRead(Strict):
    items: list[InboxItem] = Field(default_factory=list)
    index_revision: int = 0
    layout_revision: int = 0


class PlaceRequest(Strict):
    """把 Inbox 里的节点放到画布上。不给 group/at 就自动找位置（设计文档 3.9）。"""

    base_revision: int
    ids: list[str]
    group: str | None = None
    at: Point | None = None                          # 只在放单个节点时有效
    state: Literal["final", "draft"] = "draft"


class Placed(Strict):
    id: str
    x: float
    y: float
    group: str
    state: Literal["final", "draft"]
    anchor: str | None = None


class PlaceResult(Strict):
    revision: int
    placed: list[Placed] = Field(default_factory=list)
    skipped: list[dict[str, str]] = Field(default_factory=list)   # {id, reason}
    grown_groups: list[str] = Field(default_factory=list)         # 为放下新节点而加高的分组


class ReviewDone(Strict):
    id: str
    reviews: int
    step: int = 0                      # 间隔序号：忘了归 0、模糊不变、记得 +1
    lapses: int = 0                    # 累计答"忘了"的次数
    next_due: str | None = None


class NodeDetail(Strict):
    id: str
    path: str
    raw: str                           # md 原文，逐字返回
    digest: str
    meta: dict[str, Any] = Field(default_factory=dict)
    out: list[dict[str, Any]] = Field(default_factory=list)
    in_edges: list[dict[str, Any]] = Field(default_factory=list)
    obsidian_uri: str = ""


# ---------------------------------------------------------------- Suggest（AI 建议）

class SuggestRequest(Strict):
    node_id: str
    index_revision: int | None = None


class SuggestEdge(Strict):
    type: str
    target: str
    direction: Literal["out", "in"] = "out"
    reason: str = ""
    confidence: float = 0.8


class SuggestDuplicate(Strict):
    existing_id: str
    reason: str = ""
    confidence: float = 0.8


class SuggestResult(Strict):
    node_id: str
    edges: list[SuggestEdge] = Field(default_factory=list)
    duplicates: list[SuggestDuplicate] = Field(default_factory=list)
    suggested_field: str | None = None
    # 抽象层和年份：同一次调用顺手要出来的，模型此刻正在读这个节点。
    # 覆盖的是**不走计划建出来的点**——从计划进来的在拆解时就填好了（PlanPoint.layer / year）。
    suggested_layer: str | None = None
    suggested_year: int | None = None
    suggested_group: str | None = None
    suggested_group_name: str | None = None
    raw_llm: str | None = None


# ---------------------------------------------------------------- Quiz（测验与三档反馈）

Grade = Literal["记得", "模糊", "忘了"]


# 学到什么份上。一个维度决定三件事：出题深浅、对话展开到哪一层、拆点拆多细。
# 档位名在 core.LEVELS（数据），对应的口径文本在 server/levels.py（行为）。
Level = Literal["了解", "会用", "精通"]


class QuizRequest(Strict):
    """对哪些节点出题。节点多了 prompt 会超长，服务端按 MAX_NODES 截断并在 warnings 里说明。"""

    node_ids: list[str]
    count: int = Field(default=3, ge=1, le=10)
    style: Literal["复习", "面试"] = "复习"
    level: Level | None = None               # 难度档：决定问到多深、标准答案写多细
    coach: str = ""                          # 面试口径下的方向，例如「Java 后端开发」


class QuizQuestion(Strict):
    """一道题的四份文本里，这里只放两份：题干和标准答案。

    我答题写的那份叫 `QuizAnswer.my_answer`，模型批改时补的那份叫 `QuizDiagnosisItem.full_answer`，
    都不在这里——**这个模型里的文本全部来自我的笔记**，出题时照节点正文抄，所以能当判分依据。
    """

    type: str = "回忆题"                     # 回忆题 / 关系题 / 辨析题
    stem: str
    ref_answer: str = ""                     # 标准答案，照节点 md 正文抄的（不是我答题写的那份）
    points: list[str] = Field(default_factory=list)   # 考点节点 id，答错时按这些安排复习
    hint: str = ""


class QuizSet(Strict):
    questions: list[QuizQuestion] = Field(default_factory=list)
    index_revision: int = 0                  # 提交批改时带回来，索引变了就该重新出题
    level: Level | None = None               # 这份卷子按哪一档出的；批改要按同一档判，不重新猜
    warnings: list[str] = Field(default_factory=list)


class QuizAnswer(Strict):
    question: QuizQuestion
    grade: Grade
    my_answer: str = ""                      # 我写下的答案；诊断靠它跟标准答案比对
    missed: list[str] = Field(default_factory=list)    # 诊断给出的漏掉点，交卷时一并留档
    wrong_points: list[str] = Field(default_factory=list)   # 诊断给出的记错点


class QuizGradeRequest(Strict):
    answers: list[QuizAnswer]
    index_revision: int | None = None


class QuizDiagnoseRequest(Strict):
    """整轮一次性比对：逐题调模型会让每道题都卡几秒，答题节奏全毁。"""

    answers: list[QuizAnswer]
    level: Level | None = None               # 按哪一档判；空 = 服务端从没交的那份卷子里取


class QuizDiagnosisItem(Strict):
    """一道题的诊断。三组字段各管一件事，别混：

    - `missed / wrong / suggested_grade`：**按当前档位**判我这次答得怎么样，喂复习调度。
    - `beyond / next_gap`：档位之间的信号。答超了就该考虑升档，没超也能知道下一档还差什么。
      「超分」做成升档建议而不是第四个档位，是因为 `Grade` 三档是遗忘曲线的输入，
      动它要改 review-log 和所有历史记录，而升档本来就是另一件事。
    - `full_answer / beyond_vault`：这题的完整答案，答完顺手把视野撑开一点。
      `beyond_vault` 是其中**我笔记里没有**的那些点——既是提醒"这是模型说的、未必对"，
      也是"要不要补进节点 md"的候选。
    - `ref_answer`：这题本来没有标准答案时（聊天攒的题只有题干和考点），照节点正文补出来的那一份，
      会填进 `QuizQuestion.ref_answer`。**和 `full_answer` 是两回事**：它只许用我笔记里的原话，
      补出来是要当以后判分依据的；`full_answer` 允许超出笔记，永远只给我看。
    """

    n: int                                   # 第几题，从 1 开始
    missed: list[str] = Field(default_factory=list)
    wrong: list[str] = Field(default_factory=list)
    suggested_grade: Grade = "模糊"
    comment: str = ""
    beyond: bool = False                     # 答得超出了当前档位的要求
    next_gap: str = ""                       # 按上一档看还缺什么；已是最高档则留空
    full_answer: str = ""                    # 完整答案：标准答案之上补全的那一版
    beyond_vault: list[str] = Field(default_factory=list)   # 完整答案里我笔记没有的点
    ref_answer: str = ""                     # 本来没有标准答案时，照节点正文补出来的那一份


class QuizDiagnosis(Strict):
    items: list[QuizDiagnosisItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class QuizGraded(Strict):
    reviewed: list[dict[str, Any]] = Field(default_factory=list)   # {id, grade, step, lapses, next_due}
    wrong: list[str] = Field(default_factory=list)                 # 这一轮答"忘了"的节点


class ReviewRequest(Strict):
    """POST /api/review/:id 的可选 body。不带 body 等价于「记得」，兼容旧前端。"""

    grade: Grade = "记得"


# ---------------------------------------------------------------- Plans（学习计划 F10）

# 知识点 id 同时是将来 md 的文件名，所以沿用节点 id 的忌讳字符。
# 写成"允许的字符类"而不是前瞻断言：pydantic 的 Rust 正则引擎不支持 look-around。
ID_PATTERN = r'^[^\\/:*?"<>|\s]+$'


PointLoad = Literal["轻", "中", "重"]

# 三种拆解口径，不是三套架构（F10.3b）：模板与出题口径不同，数据结构和链路完全共用。
#   学习 = 按依赖顺序拆   面试 = 按会怎么问拆   领域 = 按覆盖度铺一张地图
# **kind 属于「清单」，不属于「项目」**——它说的是"这份清单怎么拆出来的"，
# 不是"项目的类型"；一个项目下可以同时有主线、面试清单和领域地图（重构方案 §3）。
ListKind = Literal["学习", "面试", "领域"]



class PlanPoint(Strict):
    """清单里的一个知识点。

    **id 允许指向图里还不存在的节点**——这正是它的用途：你要学 Transformer 的时候
    这些节点一个都还没有。`name` / `why` 让清单在节点建出来之前也读得懂。
    """

    id: str = Field(min_length=1, max_length=200, pattern=ID_PATTERN)
    name: str = ""
    why: str = ""
    load: PointLoad = "中"      # 学习负荷三档；换算成小时是 core.LOAD_HOURS 那一张表
    # 下面两个是**给新建对话框的预填值**，不参与排期也不影响进度。
    # 拆解那一次调用顺手要出来的：模型正在读这个概念，问它属于哪一层、哪年提出，
    # 比之后单开一次调用去猜便宜得多（也比本地按领域投多数派准）。
    layer: str = ""             # 抽象层七档之一（core.LAYERS）；拿不准就留空
    year: int | None = None     # 提出年份；只有查得准的才填


class PlanStage(Strict):
    name: str = Field(min_length=1, max_length=120)
    deadline: str | None = None
    points: list[PlanPoint] = Field(default_factory=list)


class ProjectList(Strict):
    """项目下的一份清单：学习主线 / 某一家的面试方案 / 某个领域的全景图。

    截止日在这里（面试有面试的日子），**每周投入在项目上**——你的时间只有一份，
    不会因为多开一份清单就变多。
    """

    kind: ListKind = "学习"
    name: str = Field(default="主线", max_length=120)
    goal: str = ""                     # 面试清单里这里放岗位要求原文
    coach: str = ""                    # 教练侧写，例如「Java 后端开发」；注入拆解与出题
    field: str = ""                    # 这份清单的落脚领域；空则用项目的
    target_date: str | None = None
    stages: list[PlanStage] = Field(default_factory=list)


class Project(Strict):
    """项目是**视角**，不是容器：它不拥有节点，只引用一组 node_id。

    掌握度、复习调度、错题本一律全局唯一——同一个大脑不可能"在 NLP 项目里记得、
    在 Transformer 项目里忘了"。项目只是过滤器（重构方案 §1）。
    """

    name: str = Field(min_length=1, max_length=120)
    created: str | None = None
    field: str = ""                    # 默认落脚领域，清单没写自己的就用它
    level: Level = "会用"              # 学到什么份上：出题深浅、对话详细度、拆点粒度都看它
    weekly_hours: int = Field(default=7, ge=1, le=80)   # 每周能投入几小时；时间账的分母
    daily_quota: int = Field(default=2, ge=1, le=20)    # 今日清单一次摆几个建设项
    legacy_id: str | None = None       # 从 plans.json 迁来的旧 id，对话目录迁移用
    lists: list[ProjectList] = Field(default_factory=list)


class ProjectsDoc(Strict):
    schema_version: int = 2
    revision: int = 0
    updated_at: str | None = None
    projects: dict[str, Project] = Field(default_factory=dict)


class ProjectsRead(Strict):
    doc: ProjectsDoc
    progress: dict[str, Any] = Field(default_factory=dict)   # {project_id: {lists: [...], all: {...}}}
    schedules: dict[str, Any] = Field(default_factory=dict)  # {project_id: {lists: [时间账]}}
    index_revision: int = 0


class ProjectsWrite(Strict):
    """整份替换。项目是人手编排的小文档，没必要上 Merge Patch——
    但 revision 仍然要挡并发，语义与 layout 的 base_revision 一致。"""

    base_revision: int
    projects: dict[str, Project]


class ProjectsSaved(Strict):
    revision: int
    progress: dict[str, Any] = Field(default_factory=dict)
    schedules: dict[str, Any] = Field(default_factory=dict)


ProposeMode = Literal["标准", "速学"]


class PlanProposeRequest(Strict):
    """目标 → 知识点清单。只提议，不落盘；人在面板上逐条增删后才进 projects.json。"""

    goal: str = Field(min_length=1, max_length=8000)   # 面试清单要塞得下一整份 JD
    plan_name: str = ""
    kind: ListKind = "学习"
    level: Level | None = None                        # 拆多细：了解 = 少而粗，精通 = 拆到机制
    coach: str = ""
    target_date: str | None = None                    # 有它模型才排得出阶段截止日
    weekly_hours: int = Field(default=7, ge=1, le=80)
    mode: ProposeMode = "标准"                         # 速学＝时间装不下时，砍到最精炼的一份
    project: str | None = None                        # 拆给哪个项目：算"别的项目已有的点"时排除它自己
    known_points: list[str] = Field(default_factory=list, max_length=200)
    """这份清单里已经有的点（`id` 或 `id（名字）`）。不喂给模型，它就会把同一个目标
    再拆一遍近义词——`RNN` / `RNN与长程依赖` 这种，靠 id 去重是拦不住的。"""


class PlanProposal(Strict):
    stages: list[PlanStage] = Field(default_factory=list)
    schedule: dict[str, Any] = Field(default_factory=dict)   # 这份提议排进给定时间后的时间账
    dropped: list[PlanPoint] = Field(default_factory=list)   # 速学模式砍掉的点，必须留痕
    duplicates: list[str] = Field(default_factory=list)      # 这份清单里已经有的点，面板上默认划掉
    in_projects: dict[str, list[str]] = Field(default_factory=dict)
    """{点 id: [别的项目名…]}。**只标不拦**——项目是视角，重叠合法且免费
    （同一个点属于两个项目，掌握度还是同一个）；但你得看得见它已经在别处列过。"""
    suggested_field: str = ""          # 这份清单该落在哪个领域，采纳时填进项目
    notes: str = ""
    existing: list[str] = Field(default_factory=list)    # 提议里已经在图谱中的 id，面板上标出来
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- Coach（今日清单 F10.3）

class CoachItem(Strict):
    """今天可以动手的一条。kind 决定点下去干什么，前端按它分派。"""

    kind: Literal["wrong", "due", "unbuilt", "shell", "inbox"]
    id: str
    name: str
    why: str = ""                      # 清单里写的"为什么要学它"
    detail: str = ""                   # 「逾期 3 天」「错过 2 次」「放进某某组」
    project: str | None = None
    project_name: str | None = None
    list: str | None = None
    stage: str | None = None
    state: dict[str, str] = Field(default_factory=dict)   # {study, exam}，学/考双态，现算


class CoachPlanLine(Strict):
    """一行一份**清单**，不是一行一个项目——同一个项目里学习主线和面试清单是两回事。"""

    id: str                            # 项目 id
    name: str                          # 项目名
    list: str = ""                     # 清单名
    kind: str = "学习"
    stage: str = ""                    # 当前阶段；全建完了是空串
    done: bool = False
    built: int = 0
    total: int = 0
    behind: int = 0                    # 已经过了阶段截止日、却还没建出来的点数
    verdict: str = ""                  # 充裕 / 紧 / 不可能；没填目标日期就是空串
    days_left: int | None = None
    suggested_quota: int = 0           # 按剩余点数和剩余天数算的每日建议量
    stage_deadline: str | None = None  # 当前阶段的截止日（人手填的优先，否则用建议日）


class CoachToday(Strict):
    generated_at: str
    items: list[CoachItem] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    projects: list[CoachPlanLine] = Field(default_factory=list)
    pools: dict[str, list[str]] = Field(default_factory=dict)   # 出题范围：今日 / 没考过 / 本项目 / 已建全部
    estimate_hours: float = 0.0        # 今天这一屏大概要多久（建设按负荷、复习按每个几分钟）
    elsewhere: dict[str, int] = Field(default_factory=dict)
    """按项目过滤时，**别的项目还欠着多少**（{wrong, due}）。过滤可以，藏起来不行——
    藏起来的复习等于没有复习。"""


# ---------------------------------------------------------------- 对话式教练（阶段 12）

class ChatMessage(Strict):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=100000)


class ChatRequest(Strict):
    """整段对话每次都重发：会话状态在前端，服务端不持有——
    刷新页面不会"丢一半上下文"，也不用管会话过期。"""

    messages: list[ChatMessage] = Field(min_length=1, max_length=200)
    stance: Literal["教练", "面试", "聊天"] = "教练"
    """口径：决定用哪套系统提示词、开哪几个工具。**不是三个 agent**——
    同一条链路、同一份图、同一套复习记录，只是提示词和工具白名单不同（重构方案 §4 之后的补记）。"""
    session: str | None = Field(default=None, max_length=64)
    """这一段对话的标签。前端生成，服务端只是记在留档行上——
    **不建会话表**，会话列表是从这些行聚合出来的（同"进度不落盘"那条纪律）。"""
    project: str | None = None
    """聊的是哪个项目。留档按它分目录；没给就落进 `_scratch/`——
    「对话」是默认入口，冷启动时一个项目都还没有，随手问一句也得有地方落（重构方案 §8）。"""


# ---------------------------------------------------------------- 学习日历（五期）

class CalendarRead(Strict):
    """每天一格的热力图 + 某天的明细。**全部派生，不新增任何记录**（重构方案 §6）。"""

    model_config = ConfigDict(extra="forbid")

    from_: str = Field(alias="from")
    to: str
    days: dict[str, Any] = Field(default_factory=dict)      # {日期: {built, reviews, answers, …}}
    detail: dict[str, Any] = Field(default_factory=dict)    # {日期: {built: [...], reviews: [...]}}
    totals: dict[str, Any] = Field(default_factory=dict)
    streak: int = 0                                          # 连续学习天数
    busiest: str | None = None


# ---------------------------------------------------------------- LLM 用量

class UsageBucket(Strict):
    calls: int = 0
    errors: int = 0
    cost_usd: float = 0.0              # 只有 provider 自己报了才非 0（目前只有 claude-cli 报）
    ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class UsageRead(Strict):
    date: str
    today: UsageBucket
    totals: UsageBucket
    by_op: dict[str, UsageBucket] = Field(default_factory=dict)
    recent: list[dict[str, Any]] = Field(default_factory=list)
    provider: str = ""                 # 当前各角色用的是谁，方便对账
    roles: dict[str, str] = Field(default_factory=dict)
    cost_known: bool = False           # false = 这个 provider 不报价，页面上只显示 token


# ---------------------------------------------------------------- 重命名（改 id）

class RenameRequest(Strict):
    """改一个知识点的 id。文件名就是 id，所以这同时是改文件名。"""

    old_id: str
    new_id: str = Field(min_length=1, max_length=200, pattern=ID_PATTERN)
    base_revision: int
    dry_run: bool = True               # 默认只算影响面；确认后再发一次 dry_run=false


class RenameImpact(Strict):
    old_id: str
    new_id: str
    path: str
    new_path: str
    links: int = 0                     # `[[旧id]]` 一共出现多少处
    files: list[str] = Field(default_factory=list)
    layout: bool = False               # 画布位置要不要跟着迁
    layout_edges: int = 0
    refs: int = 0
    docs: int = 0                      # 有几个分组把它当总览文档
    reviews: int = 0
    quiz: int = 0
    projects: list[str] = Field(default_factory=list)


class RenameResult(Strict):
    impact: RenameImpact
    applied: bool = False
    index_revision: int = 0


# ---------------------------------------------------------------- 合并重复节点

class MergeRequest(Strict):
    """把两张讲同一件事的卡并成一张。保留谁、丢弃谁由人定，服务端不猜。"""

    keep_id: str
    drop_id: str
    base_revision: int
    dry_run: bool = True


class MergeImpact(Strict):
    keep_id: str
    drop_id: str
    keep_path: str
    drop_path: str
    moved_edges: list[dict[str, str]] = Field(default_factory=list)   # 会迁到保留那张上的边
    dropped_edges: list[str] = Field(default_factory=list)            # 并完没意义、会丢掉的边（附原因）
    links: int = 0
    files: list[str] = Field(default_factory=list)
    body_chars: int = 0                # 会被追加到保留那张末尾的正文长度
    layout: bool = False
    layout_edges: int = 0
    refs: int = 0
    reviews: int = 0
    quiz: int = 0
    projects: list[str] = Field(default_factory=list)


class MergeResult(Strict):
    impact: MergeImpact
    applied: bool = False
    index_revision: int = 0
