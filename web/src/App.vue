<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import {
  fetchSettings, putSettings, fetchCalendar, fetchDigest, postSyncToGlobal, fetchDue, fetchProjects, putProjects, postPlanPropose, fetchToday, fetchUsage, postMerge, postRename, postQuiz, postQuizDiagnose, postQuizGrade, fetchOpenQuiz, dropOpenQuiz, postRegroup, fetchIndex, fetchInbox, fetchLayout, fetchNode,
  patchLayout, postChanges, postPlace, postReview, postSuggest, postSummarize, postYearsPropose,
} from './api.js'
import AppHeader from './components/AppHeader.vue'
import ActivityBar from './components/ActivityBar.vue'
import { panelOk } from './components/activity-items.js'
import CanvasTools from './components/CanvasTools.vue'
import ZoomBar from './components/ZoomBar.vue'
import HistoryPlayer from './components/HistoryPlayer.vue'
import StatusBar from './components/StatusBar.vue'
import Inspector from './components/Inspector.vue'
import HelpDialog from './components/HelpDialog.vue'
import ContextMenu from './components/ContextMenu.vue'
import RelationDialog from './components/RelationDialog.vue'
import NodeDialog from './components/NodeDialog.vue'
import QuizDialog from './components/QuizDialog.vue'
import UsageDialog from './components/UsageDialog.vue'
import RenameDialog from './components/RenameDialog.vue'
import MergeDialog from './components/MergeDialog.vue'
import YearDialog from './components/YearDialog.vue'
import GroupBar from './components/GroupBar.vue'
import MiniMap from './components/MiniMap.vue'
import ToastHost from './ui/ToastHost.vue'
import Icon from './ui/Icon.vue'
import ImportPanel from './panels/ImportPanel.vue'
import InboxTray from './panels/InboxTray.vue'
import DigestPanel from './panels/DigestPanel.vue'
import StudyPanel from './panels/StudyPanel.vue'
import CalendarPanel from './panels/CalendarPanel.vue'
import StatsPanel from './panels/StatsPanel.vue'
import ChatView from './views/ChatView.vue'
import MorningBrief from './components/MorningBrief.vue'
import SettingsDialog from './components/SettingsDialog.vue'
import ProjectsPanel from './panels/ProjectsPanel.vue'
import ImagePicker from './panels/ImagePicker.vue'
import TimelinePanel from './panels/TimelinePanel.vue'
import TourPanel from './components/TourPanel.vue'
import { clone, createHistory, diffPatch, isEmptyPatch } from './canvas/history.js'
import { createPatcher } from './canvas/patcher.js'
import { useHistory } from './composables/useHistory.js'
import { useChat } from './composables/useChat.js'
import { useCamera } from './composables/useCamera.js'
import { usePathSearch } from './composables/usePathSearch.js'
import { ancestors as groupAncestors, computeCollapsed } from './canvas/lod.js'
import {
  applyViewport, buildCells, contentBBox, createGraph,
  highlightEdges, highlightPath, mount, movedPositions, setSnap, snapDelta,
} from './canvas/render.js'
import { communityLayout, compareWithGroups } from './canvas/communities.js'
import { mindmapLayout, toPatch } from './canvas/layouts.js'
import { GROUP_LAYOUTS, layoutGroup, membersOf } from './canvas/groupLayout.js'
import { buildMenu } from './canvas/menus.js'
import { FAMILIES, HEAD_MAX as GROUP_HEAD, NODE_H, NODE_W, setTheme } from './canvas/shapes.js'

const canvasEl = ref(null)
const headerEl = ref(null)
const graph = shallowRef(null)
const patcher = shallowRef(null)
const indexDoc = shallowRef(null)
const layoutDoc = shallowRef(null)

const revision = ref(0)
const indexRevision = ref(0)
const status = ref('saved')
const selected = ref(null)
const detail = shallowRef(null)          // GET /api/node/:id 的结果（md 原文 + 出入边）
const pending = ref([])                  // 待提交的 ChangeSet（本地攒着，未确认不碰 md）
const changePreview = shallowRef(null)   // 预览结果（每个文件的 diff）
const suggestions = shallowRef(null)    // SuggestResult from /api/suggest
const suggesting = ref(false)           // LLM 正在生成建议
const yearsOpen = ref(false)            // year 批量回填对话框
const yearProposal = shallowRef(null)   // YearProposal；null = 还在问
const yearsBusy = ref(false)
const stats = reactive({ nodes: 0, edges: 0, stubs: 0 })
/**
 * 哪些关系族画出来。
 *
 * 原来结构族默认关着：那时 86 条结构边里 74 条两端同框，和分组框重复（设计文档 3.6）。
 * 2026-09-14 全图关系清空、改由人手工重连之后这个前提没了——手工连的第一批多半就是
 * 「部件 / 包含」这种层次骨架，默认藏起来会让人以为没连上。
 * 勾选状态记在 localStorage：它是"这台机器上怎么看图"的偏好，和主题同级，不进 layout.json。
 */
const FAMILY_KEY = 'knowrary-families'
const visible = reactive(loadFamilies())

function loadFamilies() {
  const def = Object.fromEntries(FAMILIES.map((f) => [f, true]))
  try {
    return { ...def, ...(JSON.parse(localStorage.getItem(FAMILY_KEY) || '{}') || {}) }
  } catch {
    return def
  }
}
const edgesShown = ref(0)
const aggShown = ref(0)
const inboxCount = ref(0)
const inboxItems = shallowRef([])        // GET /api/inbox：索引里有、画布上还没有的节点
const digest = shallowRef(null)          // GET /api/digest：欠账清单
const dueIds = shallowRef(new Set())     // 今天该复习的节点，画布上点一个金色小圆点
const placing = ref(false)
// 历史视图（阶段 6）：X 轴锁在年份上，坐标不持久化，进来一次算一次
// 四个模式（三期）：对话 / 项目图 / 全局图 / 历史。
// **默认落在「对话」**——启动成本最低的入口应该是默认入口。
const mode = ref(localStorage.getItem('knowrary-mode') || 'chat')
const autoLod = ref(true)                // 缩小自动折叠成簇卡片（设计文档 3.7）
// 对齐线 + 落点吸附：和主题、小地图一样是"这台机器上怎么摆图"的偏好，不进 layout.json
const snap = ref(localStorage.getItem('knowrary-snap') !== '0')
// 连线绕开卡片：默认不开，它会把线掰成直角，是另一种观感
// 默认**开**：线被卡片盖住是实打实看不见信息，直角走线只是观感问题
const avoidNodes = ref(localStorage.getItem('knowrary-avoid') !== '0')
// 项目画布上把「一跳外部邻居」也借过来画（GPU 前面的 CPU）。
// **默认关，而且不记在本机**：项目图的本分是专心，周边是"想看一眼"时才要的东西。
// 不持久化还顺手绕开一个已知问题——首屏 render() 跑在 refreshPlans() 回来之前，
// 那时 projectIds 还是空的，借不出任何点，之后也没有东西触发重画；
// 开关状态要是留在"开"，刷新回来就是一片空白，反倒像坏了。每次点一下，行为永远一致。
const borrowOn = ref(false)
const focusGroup = ref(null)             // 聚焦的域：点簇卡片进入，只展开它
const search = ref('')                   // 顶栏搜索词
let panorama = null                      // 进入聚焦前的视口，退出时还原
const collapsedIds = shallowRef(new Set())
const theme = ref(localStorage.getItem('knowrary-theme') || 'light')
const aggregate = ref(true)              // 跨分组边默认聚合成「分组→分组 (n)」
const expanded = ref(new Set())          // 被点开看明细的分组对
const preview = shallowRef(null)         // 换布局的预览态：{ serverLayout, result }，未落盘
const history = createHistory()
const histVer = ref(0)                   // 栈深度变化时触发按钮可用状态刷新
let dirtyBefore = null                   // 当前这批未保存改动之前的快照（撤销用）
const canUndo = computed(() => histVer.value >= 0 && history.depth()[0] > 0)
const canRedo = computed(() => histVer.value >= 0 && history.depth()[1] > 0)
// 只有用户真的操作过画布才允许落盘：既避免"打开页面就涨 revision"，
// 也不依赖 requestAnimationFrame（后台标签页 / 无头浏览器里 rAF 不触发）
const ready = ref(false)
// 程序化设置视口期间不落盘，否则切族/展开都白涨一个 revision。
// 是 ref 不是裸 let：结构视图和历史视图（composables/useHistory）共用同一把闸。
const applyingViewport = ref(false)

// 镜头：视口读写、缩放、平滑飞行、小地图取景框、域工具条定位。
// 它们共用同一把写盘闸（applyingViewport）——程序化挪镜头不该把视口写回 layout.json。
const {
  zoom, labelsOn, viewBox, activeGroup, groupBarAt,
  activeGroupBox, activeGroupCount, activeFolded,
  saveViewport, setActiveGroup, placeGroupBar, syncView, onZoom,
  stepZoom, resetZoom, jumpTo, boxOf, flyToPair, flyTo, cancelFly,
} = useCamera({
  graph, indexDoc, layoutDoc, patcher, mode, collapsedIds, autoLod, focusGroup,
  ready, applyingViewport,
  writable: () => writable(),
  render: (...a) => render(...a),
})

// 路径搜索：两个知识点之间最短的那条解释链
const {
  pathFrom, pathHit, nodeName, edgeCellId, applyPath, clearPathHighlight, startPath, endPath,
} = usePathSearch({
  graph, indexDoc, layoutDoc, aggregate, expanded,
  visibleFamilies: () => visibleFamilies(),
  setBanner: (...a) => setBanner(...a),
})

// 历史 / 谱系 / 回放 / 导览：整块在 composables/useHistory.js。
// 它们共用同一条时间游标，所以是一个整体；这里只把画布那几样递进去。
const {
  hist, histPlan, linPlan, timelines, histActiveCount, isPlaying, speed, tour,
  histChain, timelineChoices, yearRange, layeredHint, lineageChains,
  tourChain, tourStopId, tourStop, tourVia,
  renderHistory, renderLineage, paintTime, markHistoryContainer, setUpto,
  togglePlay, setSpeed, stopPlay, evoGap, startTour, tourGo, paintTour, scheduleTour,
  toggleTourAuto, stopTour, toggleTimeline, toggleHistFamily, histActiveIds,
} = useHistory({
  graph, indexDoc, layoutDoc, zoom, applyingViewport,
  setBanner: (...a) => setBanner(...a),
  flyTo: (...a) => flyTo(...a),
  cancelFly: () => cancelFly(),
})

// —— 右键菜单 / 建立关系 / 小地图 / 只看邻居 ——
const ctx = ref(null)          // 菜单浮层的 props：{ x, y, title, subtitle, items }
let ctxTarget = null           // 菜单指着谁：{ kind, id, at }，不进 props（会漏成 DOM 属性）
const relating = shallowRef(null)        // 建立关系对话框的源节点
const relatePreset = shallowRef(null)    // 欠账清单的连边建议带过来的默认类型与目标
watch(relating, (v) => { if (!v) relatePreset.value = null })
const creating = shallowRef(null)        // 新建知识点对话框：{ at, group }
const writeNonce = ref(0)                // ++ 一次 = 让检查器展开正文编辑框
const neighbor = ref(null)               // 只看这个节点和它的直接邻居
const showMap = ref(localStorage.getItem('knowrary-map') !== '0')

// —— 界面状态：左侧工具窗口、右侧检查器、浮层提示、帮助 ——
const panel = ref('')                    // '' | inbox | plans | study | digest | assets | timeline
const plansDoc = shallowRef(null)        // 项目；只写 projects.json，不碰 md 也不碰 layout
// 当前项目：今日清单、出题范围、对话留档、**画布用哪份 layout** 都按它走；空串 = 全局。
// **开局就从 localStorage 读**：挂载时第一次 fetchLayout 就要知道该拉哪一份，
// 等 refreshPlans 回来再改就晚了——那时画布已经画成全局图了（刷新后跳回全局的那个 bug）。
const currentProject = ref((() => {
  try { return localStorage.getItem('knowrary-project') || '' } catch { return '' }
})())
let projectPicked = false                // 是否已经定过当前项目（避免每次刷新都被首个项目顶掉）

// 学 / 考双态，画布上画成左下角两个小方块。**只覆盖当前项目里的点**——
// 全图每个节点都算一遍没有意义：双态是"我正在推进的东西卡在哪一步"，不是节点属性。
// 晨间简报：当天第一次打开弹一次。**只记日期，不记"看过没有"**——
// 换成布尔值的话，跨天要靠别的机制去重置，日期本身就是最简单的那把钥匙。
const BRIEF_KEY = 'knowrary-brief-day'
const briefOn = ref(false)
// 设置：**只有"后端也要读"的开关在这里**（复习要不要出现）。
// 画布 / 外观那些仍旧各自记在 localStorage——它们是"这台机器上怎么看图"。
// 先给默认值（全开）：接口还没回来的那一瞬间不该先闪一下"关着"的样子。
const settings = ref({ review_enabled: true, review_in_chat: true, review_brief: true,
                       review_marks: true })
const settingsOn = ref(false)
const reviewOn = computed(() => !!settings.value.review_enabled)
const reviewMarks = computed(() => reviewOn.value && settings.value.review_marks !== false)

const syncing = ref(false)
const calendar = shallowRef(null)     // 学习日历：全派生，每次打开重算

async function refreshCalendar() {
  try {
    calendar.value = await fetchCalendar(120)
  } catch (err) {
    setBanner(`日历加载失败：${err.message}`, 'error')
  }
}
const nodeStates = computed(() => plansProgress.value?.[currentProject.value]?.all?.states || {})

/** 把刚建好的点补进某份清单（变更卡上写明了要加到哪）。 */
async function addToList({ project, list, points }) {
  const doc = await fetchProjects()
  const next = JSON.parse(JSON.stringify(doc.doc.projects || {}))
  const ls = next[project]?.lists?.[list]
  if (!ls) return
  const mine = new Set(ls.stages.flatMap((st) => st.points.map((p) => p.id)))
  const fresh = points.filter((id) => !mine.has(id)).map((id) => ({ id, name: id, load: '中',
                                                                    why: '聊出来的' }))
  if (!fresh.length) return
  const hit = ls.stages.find((st) => st.name === '聊出来的')
  if (hit) hit.points.push(...fresh)
  else ls.stages.push({ name: '聊出来的', deadline: null, points: fresh })
  await putProjects({ base_revision: doc.doc.revision, projects: next })
  await refreshPlans()
}

/** 对话里提议的项目卡：点「创建」才写 projects.json。整份替换，沿用 base_revision 乐观锁。 */
async function applyProjectCard({ card, i, j }) {
  chatBusy.value = true
  try {
    const doc = await fetchProjects()
    const next = JSON.parse(JSON.stringify(doc.doc.projects || {}))
    const old = next[card.id]
    next[card.id] = old
      ? { ...old, lists: [...(old.lists || []), ...card.lists] }      // 已有项目：加清单，不覆盖
      : { name: card.name, field: card.field, level: card.level || '会用',
          weekly_hours: card.weekly_hours,
          daily_quota: card.daily_quota, created: new Date().toISOString().slice(0, 10),
          lists: card.lists }
    await putProjects({ base_revision: doc.doc.revision, projects: next })
    chatLog.value[i].projects[j].applied = true
    await refreshPlans()
    // **直接把人送到那一项**：建完还要自己去左侧栏找项目面板、再在下拉里挑一遍，
    // 这一步的摩擦比建项目本身还大。
    await switchProject(card.id)
    openPanel('plans', { force: true })
    setBanner(old ? `已往「${card.name}」加了 ${card.lists.length} 份清单，面板已经切过去了`
                  : `已创建项目「${card.name}」，面板已经切过去了——点「让 AI 拆一份」把点填进来`, 'success')
  } catch (err) {
    setBanner(`创建失败：${err.body?.detail || err.message}`, 'error')
  } finally {
    chatBusy.value = false
  }
}

/** 对话里提议的清单改动：改 id / 删条目 / 改字段。**只动 projects.json，不碰 md**。
 *  和采纳拆点同一条路（整份替换 + base_revision 乐观锁），只是这一张是改已有的条目。 */
async function applyListEditCard({ card, i, j }) {
  chatBusy.value = true
  try {
    const doc = await fetchProjects()
    const next = JSON.parse(JSON.stringify(doc.doc.projects || {}))
    const ls = next[card.project]?.lists?.[card.list]
    if (!ls) throw new Error('这份清单不在了（项目可能被改过）')
    let done = 0
    for (const e of card.edits) {
      for (const st of ls.stages || []) {
        const at = (st.points || []).findIndex((p) => p.id === e.id)
        if (at < 0) continue
        if (e.op === 'drop') st.points.splice(at, 1)
        else if (e.op === 'rename') st.points[at].id = e.to
        else Object.assign(st.points[at], e.fields)
        done += 1
        break                       // 一个 id 只改一处：清单里本来就不该有重复条目
      }
    }
    if (!done) throw new Error('这些点在清单里都找不到了')
    await putProjects({ base_revision: doc.doc.revision, projects: next })
    chatLog.value[i].listEdits[j].applied = true
    await refreshPlans()
    await switchProject(card.project)
    openPanel('plans', { force: true })
    setBanner(`已改「${card.project_name}·${card.list_name}」${done} 条，面板已经切过去了`, 'success')
  } catch (err) {
    setBanner(`改清单失败：${err.body?.detail || err.message}`, 'error')
  } finally {
    chatBusy.value = false
  }
}

/** 对话里拆出来的点：采纳进那份清单。和面板上的「采纳」写的是同一份 projects.json。 */
async function applyPointsCard({ card, i, j }) {
  chatBusy.value = true
  try {
    const doc = await fetchProjects()
    const next = JSON.parse(JSON.stringify(doc.doc.projects || {}))
    const ls = next[card.project]?.lists?.[card.list]
    if (!ls) throw new Error('这份清单不在了（项目可能被改过）')
    const mine = new Set(ls.stages.flatMap((st) => st.points.map((p) => p.id)))
    let added = 0
    for (const st of card.stages) {
      const points = st.points.filter((p) => !mine.has(p.id))
      if (!points.length) continue
      points.forEach((p) => mine.add(p.id))
      added += points.length
      const hit = ls.stages.find((x) => x.name === st.name)
      if (hit) { hit.points.push(...points); hit.deadline = hit.deadline || st.deadline || null }
      else ls.stages.push({ name: st.name, deadline: st.deadline || null, points })
    }
    if (!ls.field && card.suggested_field) ls.field = card.suggested_field
    await putProjects({ base_revision: doc.doc.revision, projects: next })
    chatLog.value[i].points[j].applied = true
    await refreshPlans()
    await switchProject(card.project)
    openPanel('plans', { force: true })
    setBanner(`已把 ${added} 个点采纳进「${card.project_name}·${card.list_name}」，面板已经切过去了`,
              'success')
  } catch (err) {
    setBanner(`采纳失败：${err.body?.detail || err.message}`, 'error')
  } finally {
    chatBusy.value = false
  }
}

/** 在清单里找这个点（幽灵节点要它的 name / why，新建对话框要它的 layer / year）。
 *
 * 默认在当前项目里找。今日清单那条路送进来的条目可能属于别的项目，
 * 所以 `pid` 可以指定——找不到再退回当前项目，两边都空才返回 null。
 */
function projectPoint(id, pid) {
  for (const key of [pid, currentProject.value]) {
    if (!key) continue
    for (const ls of plansDoc.value?.projects?.[key]?.lists || []) {
      for (const stage of ls.stages || []) {
        const hit = (stage.points || []).find((p) => p.id === id)
        if (hit) return hit
      }
    }
  }
  return null
}

/** 同步到全局：只放"已经建出来、还没上全局图"的点，落 draft。**坐标不搬**——
 *  项目画布里的排版是你为了想清楚而摆的，全局图有自己的结构，搬过去只会打乱主图。 */
async function syncToGlobal() {
  if (!currentProject.value || syncing.value) return
  syncing.value = true
  try {
    await patcher.value.flush()
    const glob = await fetchLayout()            // 同步动的是全局图，要拿它的 revision
    const out = await postSyncToGlobal(currentProject.value, glob.layout.revision)
    const parts = []
    if (out.placed.length) parts.push(`${out.placed.length} 个点已放到全局图（金色虚线的草稿，确认位置后定稿）`)
    if (out.duplicates.length) {
      parts.push(`${out.duplicates.length} 个点疑似和图里已有的重复，先没放：`
        + out.duplicates.map((d) => `${d.id} ↔ ${d.candidates.map((c) => c.id).join('/')}`).join('；'))
    }
    const already = out.skipped.filter((x) => x.reason.includes('已经在')).length
    const unbuilt = out.skipped.length - already
    if (already) parts.push(`${already} 个本来就在图上`)
    if (unbuilt) parts.push(`${unbuilt} 个还没建出来`)
    setBanner(parts.join('；') || '没有需要同步的点', out.duplicates.length ? 'error' : 'success')
  } catch (err) {
    setBanner(`同步失败：${err.body?.detail || err.message}`, 'error')
  } finally {
    syncing.value = false
  }
}

function projectField() {
  return plansDoc.value?.projects?.[currentProject.value]?.field || ''
}

/** 重新拉当前这份 layout（换画布时用）。 */
async function reloadLayout() {
  let fresh
  try {
    fresh = await fetchLayout(layoutName())
  } catch (err) {
    if (err.status !== 404) throw err
    // 项目画布没了（项目被删）：退回全局图，别让整屏卡在"加载失败"
    currentProject.value = ''
    setBanner('那个项目已经不在了，已退回全局图')
    fresh = await fetchLayout(null)
  }
  layoutDoc.value = fresh.layout
  revision.value = fresh.layout.revision
}

/** 当前这块画布写去哪一份 layout：项目图写项目自己的，其余都写全局图。 */
function layoutName() {
  // **对话模式右边那块图也用项目画布**：在某个项目下聊天，背后却摆着整张全局图，
  // "聊到哪、图上亮哪"就完全失灵了——你聊的点多半还没建，只在项目画布上有幽灵占位。
  // 选了「🌐 全局」就回到全局图（那条线本来就不绑项目）。
  const scoped = mode.value === 'project' || mode.value === 'chat'
  return scoped && currentProject.value ? currentProject.value : null
}

// 当前项目里的点。项目图有**自己的一份 layout**（四期），里面还带着「未建」的幽灵占位。
const projectIds = computed(() =>
  new Set(Object.keys(plansProgress.value?.[currentProject.value]?.all?.points || {})))
const plansProgress = shallowRef({})     // 每个知识点的掌握度，服务端现算
/** 清单里有、这块项目画布上却没有的点：清单后来加的点、或被「从画布上去掉」的。只在项目画布上有意义。 */
const missingPoints = computed(() => {
  if (mode.value !== 'project' || !currentProject.value) return []
  const on = layoutDoc.value?.nodes || {}
  return [...projectIds.value].filter((id) => !on[id])
})
const plansSchedules = shallowRef({})     // 时间账：装不装得下、每阶段排到哪天、落后几个；同样现算

// —— 阶段 12：对话式教练 ——
// 会话、留档回放、流式收发、梳理游标整块在 composables/useChat.js。
// **卡片落地不在那儿**：写 md、上画布、补进清单要同时动图谱和项目，留在这里编排。
const {
  chatLog, chatBusy, chatSessions, chatSession, chatTidied, chatStance, chatFocus,
  graphPane, chatFresh,
  setStance, toggleGraphPane, loadChatHistory, renameSession,
  newChatSession, pickChatSession, sendChat, advanceTidied, stopChat,
} = useChat({
  graph, currentProject,
  setBanner: (...a) => setBanner(...a),
  pushToast: (...a) => pushToast(...a),
  onReview: () => refreshDue(),
})
const plansBusy = ref(false)
const planProposal = shallowRef(null)    // AI 拆出的要点；纯提议，人采纳了才进 draft
const planProposing = ref(false)
const todayList = shallowRef(null)      // 今日清单：纯排序，不调 LLM
const usage = shallowRef(null)          // 模型调用账本
const showUsage = ref(false)
const renaming = shallowRef(null)       // 正在改名的节点 { id, name }
const renameImpact = shallowRef(null)   // dry-run 算出的影响面
const renameBusy = ref(false)
const merging = shallowRef(null)        // { keep, drop }
const mergeImpact = shallowRef(null)
const mergeBusy = ref(false)
const dueList = ref([])                  // 今日到期明细（dueIds 只存 id，画布角标用）
const quiz = shallowRef(null)            // 本轮题目；null = 没在考试
const quizBusy = ref(false)              // 出题 / 诊断 / 交卷中（都要等服务端）
const quizDiag = shallowRef(null)        // 整轮比对结果；纯提议，档位仍由我点
const openQuiz = shallowRef(null)        // 上次出了还没交卷的那份题（服务端存着，刷新也在）
const inspectorHidden = ref(false)
const showHelp = ref(false)
const problems = ref([])                 // 加载时发现的待处理项，挂在状态栏上

const inspectorOpen = computed(() =>
  !inspectorHidden.value && (!!selected.value || pending.value.length > 0))

/** `force` = 无论当前开着什么都切到这个面板（程序主动带人过去时用，不能让它变成"切回关闭"）。 */
function openPanel(id, { force = false } = {}) {
  panel.value = !force && panel.value === id ? '' : id
  if (panel.value === 'digest' && !digest.value) refreshDigest()
  if (panel.value === 'inbox') refreshInbox()
  if (panel.value === 'study') { refreshToday(); refreshDue() }
  if (panel.value === 'plans') refreshPlans()
  if (panel.value === 'calendar') refreshCalendar()
}

const statusText = computed(() => ({
  saved: '已保存', saving: '保存中…', dirty: '待保存', retry: '已重试', error: '保存失败',
}[status.value] || status.value))

const visibleFamilies = () => new Set(FAMILIES.filter((f) => visible[f]))
const shownFamilies = computed(() => FAMILIES.filter((f) => visible[f]).length)
const focusName = computed(() =>
  (focusGroup.value && mode.value === 'structure' ? layoutDoc.value?.groups?.[focusGroup.value]?.name || '' : ''))

// —— 浮层提示（toast）：原来是顶在画布上方的一条 banner，会把画布压矮 ——
const toasts = ref([])
let toastSeq = 0
const toastTimers = new Map()
let statusToast = null
let lastKind = ''

function dismissToast(id) {
  const t = toastTimers.get(id)
  if (t) clearTimeout(t)
  toastTimers.delete(id)
  toasts.value = toasts.value.filter((x) => x.id !== id)
  if (statusToast === id) statusToast = null
}

function pushToast(text, kind = 'info') {
  const id = ++toastSeq
  toasts.value = [...toasts.value, { id, text, kind }]
  // 错误多留一会儿：这类提示往往要照着做下一步操作
  toastTimers.set(id, setTimeout(() => dismissToast(id), kind === 'error' ? 10000 : 5200))
  return id
}

/**
 * 单条状态提示，语义沿用原来的 banner：新的顶掉旧的，传空串就是清掉。
 * 其他地方（比如批量报告问题）要并排显示多条时直接用 pushToast。
 */
function setBanner(text, kind = '') {
  if (statusToast) dismissToast(statusToast)
  lastKind = kind
  statusToast = text ? pushToast(text, kind || 'info') : null
}

async function load() {
  let layout
  const [index, first] = await Promise.all([fetchIndex(), fetchLayout(layoutName()).catch((e) => e)])
  if (first instanceof Error) {
    if (first.status !== 404) throw first
    currentProject.value = ''          // localStorage 里存的项目已经没了：退回全局图
    layout = await fetchLayout(null)
  } else {
    layout = first
  }
  indexDoc.value = index
  layoutDoc.value = layout.layout
  revision.value = layout.layout.revision
  indexRevision.value = index.revision
  Object.assign(stats, { nodes: index.stats.nodes, edges: index.stats.edges, stubs: index.stats.stubs })
  inboxCount.value = index.nodes.filter((n) => !n.virtual && !layout.layout.nodes[n.id]).length
  refreshInbox()
  refreshDue()
  refreshUsage()
  ready.value = false
  const refit = !storedViewportUsable()
  render({ view: refit ? 'fit' : 'stored' })
  if (refit) {
    applyingViewport.value = true      // 自动贴合不算用户操作，别把视口写回去
    fitStable()
    applyingViewport.value = false
  }
  reportProblems(index, layout, refit)
}

// view: 'stored' 用 layout 里存的视口（首次加载）/ 'fit' 适应内容（换布局后）/ 'keep' 保持当前（切族、展开聚合束）
function render({ view = 'keep' } = {}) {
  if (mode.value === 'history') return renderHistory({ view })
  if (mode.value === 'lineage') return renderLineage({ view })
  const g = graph.value
  collapsedIds.value = computeCollapsed(layoutDoc.value, g.zoom(),
    { auto: autoLod.value, focus: focusGroup.value })
  // 借来的外部邻居只叠在**这一次渲染**用的 layout 上，layoutDoc 本身一个字都不改——
  // 它是要落盘的那份，混进不属于这个项目的点就再也分不干净了
  const borrowed = borrowedIds.value
  const forRender = borrowed.size
    ? { ...layoutDoc.value, nodes: { ...layoutDoc.value.nodes, ...borrowedNodes.value } }
    : layoutDoc.value
  const cells = buildCells(indexDoc.value, forRender, {
    families: visibleFamilies(), showLabels: labelsOn.value,
    aggregate: aggregate.value, expanded: expanded.value, collapsed: collapsedIds.value, zoom: g.zoom(),
    due: dueIds.value, states: nodeStates.value, avoidNodes: avoidNodes.value, borrowed,
    only: neighborSet.value
      || (mode.value === 'project' && projectIds.value.size
        ? new Set([...projectIds.value, ...borrowed])
        : null),
  })
  edgesShown.value = cells.edges.filter((e) => e.data.kind === 'edge').length
  aggShown.value = cells.edges.length - edgesShown.value
  const keep = view === 'keep' ? { zoom: g.zoom(), translate: g.translate() } : null
  applyingViewport.value = true
  mount(g, cells)
  if (keep) {
    g.zoomTo(keep.zoom)
    g.translate(keep.translate.tx, keep.translate.ty)
  } else if (view === 'fit') {
    g.zoomToFit({ padding: 60, maxScale: 1 })
  } else {
    applyViewport(g, layoutDoc.value.viewport)
  }
  applyingViewport.value = false
  zoom.value = g.zoom()
  if (pathHit.value) applyPath()   // 重绘会重建 cell，高亮得重新贴一遍
  syncView()
}

/**
 * 项目画布上「借来的」外部邻居：图里真有边连着、却不属于这个项目的点。
 *
 * 为什么要有：项目图只画项目内的点，于是 GPU 前面的 CPU 根本不出现——关系明明存在，
 * 一眼望过去却断在项目边界上；想看前后文只能退回全局图，那又把"专心"丢了。
 *
 * **坐标是算出来的，不落盘**：项目 layout 是你自己摆的版式，外部点写进去就污染了它
 * （而且项目一改清单，这些点还得跟着清理）。所以它们只活在这一次渲染里，也不许拖。
 */
const BORROW_GAP = 300           // 借来的点离锚点多远
const BORROW_MIN = 170           // 两个借来的点之间至少留这么多，免得叠在一起

const borrowedNodes = computed(() => {
  if (!borrowOn.value || mode.value !== 'project' || !currentProject.value) return {}
  const index = indexDoc.value
  const placed = layoutDoc.value?.nodes || {}
  if (!index?.edges?.length) return {}
  // **"项目内"以清单为准，不是以 layout 的键为准**：项目 layout 里会残留清单外的点
  // （从清单里删掉的、早年迁移留下的），它们被 `only` 过滤掉根本不画。
  // 拿 layout 的键当自己人的话，这些看不见的残留会把真正该借的邻居判成"已经在了"，
  // 于是一个都借不出来——第一版就栽在这里。
  const inside = new Set(projectIds.value)
  // 谁连着项目内的点、自己却不在这块画布上
  const anchors = new Map()
  for (const e of index.edges) {
    for (const [a, b] of [[e.source, e.target], [e.target, e.source]]) {
      if (!inside.has(a) || inside.has(b) || !placed[a]) continue   // 锚点得先有坐标
      if (!index.nodes.some((n) => n.id === b && !n.virtual)) continue   // 只借真存在的节点
      if (!anchors.has(b)) anchors.set(b, [])
      anchors.get(b).push(a)
    }
  }
  if (!anchors.size) return {}
  // 画布重心：借来的点一律从重心往外推，才不会插进你摆好的版式中间
  const pts = [...inside].map((id) => placed[id]).filter(Boolean)
  const cx = pts.reduce((a, n) => a + (n.x || 0), 0) / (pts.length || 1)
  const cy = pts.reduce((a, n) => a + (n.y || 0), 0) / (pts.length || 1)
  const out = {}
  const taken = []
  // 连得越多的先摆：它更该待在"正确"的方位上，零散的那些让位
  for (const [id, list] of [...anchors].sort((a, b) => b[1].length - a[1].length)) {
    const ax = list.reduce((a, n) => a + (placed[n]?.x || 0), 0) / list.length
    const ay = list.reduce((a, n) => a + (placed[n]?.y || 0), 0) / list.length
    let vx = ax - cx
    let vy = ay - cy
    const len = Math.hypot(vx, vy) || 1
    vx /= len
    vy /= len
    // 沿着"重心 → 锚点"的方向往外推；位置被占了就转个角度再试，转一圈还不行就推远一点
    let spot = null
    for (let ring = 0; ring < 3 && !spot; ring += 1) {
      for (let step = 0; step < 12; step += 1) {
        const a = (step % 2 ? -1 : 1) * Math.ceil(step / 2) * (Math.PI / 9)
        const dx = vx * Math.cos(a) - vy * Math.sin(a)
        const dy = vx * Math.sin(a) + vy * Math.cos(a)
        const r = BORROW_GAP + ring * BORROW_MIN
        const x = Math.round(ax + dx * r)
        const y = Math.round(ay + dy * r)
        if (taken.every((t) => Math.hypot(t.x - x, t.y - y) >= BORROW_MIN)
            && pts.every((n) => Math.hypot((n.x || 0) - x, (n.y || 0) - y) >= BORROW_MIN)) {
          spot = { x, y }
          break
        }
      }
    }
    const at = spot || { x: Math.round(ax + vx * BORROW_GAP), y: Math.round(ay + vy * BORROW_GAP) }
    taken.push(at)
    out[id] = { ...at, group: null, state: 'borrowed' }
  }
  return out
})

const borrowedIds = computed(() => new Set(Object.keys(borrowedNodes.value)))

/** 只看某个节点的邻居时，画布上留哪些节点（它自己 + 一跳邻居）。 */
const neighborSet = computed(() => {
  if (!neighbor.value || !indexDoc.value) return null
  const keep = new Set([neighbor.value])
  for (const e of indexDoc.value.edges) {
    if (e.source === neighbor.value) keep.add(e.target)
    else if (e.target === neighbor.value) keep.add(e.source)
  }
  return keep
})

const neighborName = computed(() => (neighbor.value
  ? indexDoc.value?.nodes.find((n) => n.id === neighbor.value)?.name || neighbor.value : ''))

function toggleNeighbor(id) {
  neighbor.value = neighbor.value === id ? null : id
  render()
  // 进邻居模式要飞到这一小撮节点上；用它们自己的框，不能用 fitStable（那是全图的框）
  const box = neighbor.value && boxOf([...neighborSet.value])
  if (!box) return
  const el = graph.value.container
  const z = Math.min(1.2, (el.clientWidth || 1200) / box.w, (el.clientHeight || 800) / box.h)
  flyTo({ cx: box.x + box.w / 2, cy: box.y + box.h / 2, zoom: z })
}

/** 一组节点在 layout 里占的框（带一点留白）。 */
/**
 * 存下来的视口还值得恢复吗。
 *
 * 视口是跟着操作实时保存的，所以很容易存成"缩到底"或者"平移跑飞了"的状态
 * （X6 的缩放下限是 0.05，那时一个节点在屏幕上只有几个像素）——下次打开就是
 * 一小坨或者一片空白，看上去像画布坏了。这种情况直接改用适应窗口。
 */
function storedViewportUsable() {
  const box = contentBBox(layoutDoc.value)
  if (!box) return true
  const vp = layoutDoc.value.viewport || {}
  const z = vp.zoom || 0.8
  const el = graph.value.container
  const w = el.clientWidth || 1200
  const h = el.clientHeight || 800
  // 比"整张图刚好铺满窗口"还小一半以上 → 一个节点只剩几个像素，什么都看不清
  const fitZoom = Math.min((w - 100) / box.width, (h - 100) / box.height)
  if (z < fitZoom * 0.5) return false
  // 视口中心离内容框还有一屏以上 → 打开是一片空白
  const cx = vp.cx ?? 0
  const cy = vp.cy ?? 0
  return cx > box.x - w / z && cx < box.x + box.width + w / z
    && cy > box.y - h / z && cy < box.y + box.height + h / z
}

const staleDays = (n) => (n.placedAt ? Math.floor((Date.now() - Date.parse(n.placedAt)) / 86400000) : 0)



async function switchMode(next) {
  // 切到全局图时，如果正选着项目，顺手把它的点高亮出来——
  // 回答"我学的这些东西，在整张图里是什么位置"。这就是项目视角与全局视角之间的桥，
  // 不另设按钮（两个控件都叫「全局图」只会让人问"为什么有两个"）。
  const focusProject = next === 'structure' && !!currentProject.value && mode.value !== 'structure'
  if (mode.value === next) return
  stopPlay()
  stopTour()
  await patcher.value.flush()           // 离开画布前先把手上的改动落盘
  const wasLayout = layoutName()
  mode.value = next
  try { localStorage.setItem('knowrary-mode', next) } catch { /* 无痕模式 */ }
  markHistoryContainer(next)
  // 左侧工具窗口是分模式的：切过去之后原来开着的那个可能不适用了。
  // 判据直接问活动栏那张表，别在这儿另写一套——谱系视图整条栏都收起来了，
  // 面板要是还挂着，关它的按钮已经不存在。
  if (!panelOk(panel.value, next, currentProject.value)) panel.value = ''
  // **换了一份 layout 就得重新拉，并且把撤销栈清掉。**
  // 撤销栈里存的是"某一份 layout 的前后两个快照"，跨画布撤销会把补丁打到错的文件上——
  // 这是多份 layout 带来的真风险，不是体验问题。
  if (layoutName() !== wasLayout) {
    history.reset()
    histVer.value++
    await reloadLayout()
  }
  if (next === 'chat') {
    loadChatHistory()
    render({ view: 'stored' })          // 右侧那块图照常画
  } else if (next === 'history') {
    renderHistory({ view: 'fit' })
  } else if (next === 'lineage') {
    renderLineage({ view: 'fit' })
  } else {
    expanded.value = new Set()
    render({ view: next === 'project' ? 'fit' : 'stored' })
    setBanner('')
  }
  // 换模式会改画布宽度（对话模式左边被占走大半），X6 要重新量一次。
  // **不能让这里的异常把后面的高亮吞掉**：渲染期一个报错就再也走不到那一步了。
  try {
    await nextTick()
    window.dispatchEvent(new Event('resize'))
  } catch (err) {
    reportCrash(err)
  }
  if (focusProject && projectIds.value.size) {
    highlightPath(graph.value, projectIds.value, new Set())
    setBanner(`已高亮「${plansDoc.value?.projects?.[currentProject.value]?.name || currentProject.value}」的 `
      + `${projectIds.value.size} 个点（Esc 取消）`)
  }
}

function reportProblems(index, layout, refit = false) {
  const parts = []
  if (refit) parts.push('上次关掉时画面缩得太小（或平移出了图外），已自动适应窗口')
  if (index.errors.length) parts.push(`索引有 ${index.errors.length} 个错误（knowrary check 看详情）`)
  if (layout.orphans.length) parts.push(`${layout.orphans.length} 条孤立布局记录（红色虚线节点，不会自动删除）`)
  if (layout.generated) parts.push('已按 field / 目录生成初始布局，拖动即保存')
  const stale = Object.entries(layout.layout.nodes).filter(([, n]) => n.state === 'draft' && staleDays(n) >= 7)
  if (stale.length) parts.push(`${stale.length} 个草稿放了一周以上（「欠账」里可以逐个定稿）`)
  problems.value = parts
  setBanner(parts.join('；'), index.errors.length ? 'error' : '')
}

/** 状态栏那个"n 项待处理"：把加载时报过的问题再摆一遍。 */
function showProblems() {
  if (!problems.value.length) return
  problems.value.forEach((p) => pushToast(p, 'info'))
}

// 与本地镜像（= 服务端最新状态）比对，值没变就不发；mount() 建父子关系触发的事件天然被过滤掉
// 写盘总闸：预览布局时画布是"草稿"；历史视图是浏览视图，坐标本来就不持久化。
// **项目画布同样可写**（四期之后它是一份真 layout）——只认 structure 的话，
// 在项目画布上拖节点、右键、连边全部静默失效，看着像"这块画布是只读的"。
// **对话模式右边那块图也可写**：它用的就是项目那份 layout（见 layoutName()），
// 漏掉 chat 的话，在对话里点节点「定稿」会静默丢补丁——提示还照弹"已定稿"，
// 盘上却没变。同一个坑踩第二次了，判据跟着 layoutName() 走，别再各写一份。
const writable = () => !preview.value
  && (mode.value === 'structure' || mode.value === 'project' || mode.value === 'chat')

function queueIfChanged(kind, id, patch) {
  if (!writable()) return
  const store = kind === 'group' ? layoutDoc.value.groups : layoutDoc.value.nodes
  const cur = store[id]
  if (cur && Object.entries(patch).every(([k, v]) => (typeof v === 'number' ? Math.round(cur[k]) === v : (cur[k] ?? null) === v))) {
    return
  }
  if (!dirtyBefore) dirtyBefore = clone(layoutDoc.value)   // 这批改动的起点，撤销要回到这里
  if (cur) Object.assign(cur, patch)
  if (kind === 'group') patcher.value.queueGroup(id, patch)
  else patcher.value.queueNode(id, patch)
}

// 任何一次事件回调抛异常，都会打断 X6 的内部清理，让整张图不再响应鼠标（拖拽卡死）。
// 所以自己的回调一律包一层，异常只记录、不外抛。
function safe(fn) {
  return (...args) => {
    try {
      return fn(...args)
    } catch (err) {
      reportCrash(err)
    }
  }
}

let recoveringAt = 0

/** 画布出错时自愈：重建 X6 实例并按服务端状态重画，避免"卡死只能刷新"。 */
function rebuildGraph(reason = '') {
  const old = graph.value
  try {
    old?.dispose()
  } catch {
    /* 已经坏掉的实例，忽略 */
  }
  const fresh = createGraph(canvasEl.value)
  graph.value = fresh
  setSnap(fresh, snap.value)
  bindEvents(fresh)
  if (window.__kg) window.__kg.graph = fresh
  render({ view: 'stored' })
  if (reason) setBanner(`画布出错，已自动恢复交互：${reason}`, 'error')
}

function reportCrash(err) {
  const msg = String(err?.message || err?.reason || err || '未知错误')
  const now = Date.now()
  if (now - recoveringAt < 3000) return   // 防止恢复过程本身再出错导致死循环
  recoveringAt = now
  rebuildGraph(msg)
}

function bindEvents(g) {
  // 首次真实交互后才允许落盘（合成事件与真人操作都会触发 mousedown / wheel）
  const markReady = () => { ready.value = true }
  g.container.addEventListener('mousedown', markReady, { capture: true })
  g.container.addEventListener('wheel', markReady, { capture: true, passive: true })
  // 导览进行中用户自己拖 / 缩画布，说明他想停下来自己看看：
  // 立刻松开方向盘（否则下一帧推镜头又把他拽走），并暂停自动走，但不退出导览。
  const yieldWheel = () => {
    if (!tour.on) return
    cancelFly()
    if (tour.auto) { tour.auto = false; scheduleTour() }
  }
  g.container.addEventListener('mousedown', yieldWheel, { capture: true })
  g.container.addEventListener('wheel', yieldWheel, { capture: true, passive: true })

  g.on('node:moved', safe(({ node }) => {
    if (!ready.value) return
    if (node.id.startsWith('note:') || node.id.startsWith('ref:')) {
      const pos = snapped(node.position())
      node.position(pos.x, pos.y)
      moveDecoration(node.id, pos.x, pos.y)
      return
    }
    if (node.shape === 'kg-cluster') return moveCluster(node)
    const moved = movedPositions(node)
    // 松手后把落点吸到网格上。只挪被拖的那个 cell，X6 会带着它的子元素一起走；
    // movedPositions 是在挪之前采的，所以同样的位移量要补到整批坐标上，落盘的才是真实位置。
    const head = moved[0]
    if (snap.value && head) {
      const { dx, dy } = snapDelta(head.x, head.y)
      if (dx || dy) {
        node.position(head.x + dx, head.y + dy)
        for (const p of moved) { p.x += dx; p.y += dy }
      }
    }
    for (const p of moved) queueIfChanged(p.kind, p.id, { x: p.x, y: p.y })
  }))
  g.on('node:change:parent', safe(({ node, current }) => {
    if (!ready.value) return
    if (node.shape === 'kg-node') {
      const pos = node.position()
      queueIfChanged('node', node.id, { group: current || null, x: Math.round(pos.x), y: Math.round(pos.y) })
    } else if (node.shape === 'kg-group') {
      const pos = node.position()
      queueIfChanged('group', node.id, { parent: current || null, x: Math.round(pos.x), y: Math.round(pos.y) })
    }
  }))
  g.on('node:selected', safe(({ node }) => {
    selected.value = describe(node.id)
    inspectorHidden.value = false
    suggestions.value = null
    focus(node.id)
    loadDetail(node.id)
  }))
  g.on('node:unselected', safe(() => { selected.value = null; detail.value = null; focus(null) }))
  g.on('blank:click', safe(() => {
    selected.value = null
    detail.value = null
    activeGroup.value = null
    groupBarAt.value = null
    focus(null)
    g.getEdges().forEach((e) => e.removeTools())   // 顺手摘掉拐点手柄
  }))
  // 悬停即高亮：不点也能看清一个节点牵着哪些线
  g.on('node:mouseenter', safe(({ node }) => { if (node.shape === 'kg-node') focus(node.id) }))
  g.on('node:mouseleave', safe(() => { focus(selected.value?.id || null) }))
  // 点簇卡片 → 放大进这个域（只展开它）；再点「返回全景」或按 Esc 缩回去
  /** 点在哪个分组的标题条里（图坐标）。嵌套时取最深的那个——点的是里层。 */
  const depthOf = (gid) => {
    let n = 0
    let cur = layoutDoc.value?.groups?.[gid]?.parent
    while (cur && n < 12) { n += 1; cur = layoutDoc.value?.groups?.[cur]?.parent }
    return n
  }
  const groupHeadAt = (x, y) => {
    let hit = null
    for (const [gid, box] of Object.entries(layoutDoc.value?.groups || {})) {
      if (collapsedIds.value.has(gid)) continue
      if (x < box.x || x > box.x + box.w || y < box.y || y > box.y + GROUP_HEAD) continue
      if (!hit || depthOf(gid) > depthOf(hit)) hit = gid
    }
    return hit
  }

  g.on('node:click', safe(({ node }) => {
    if (node.shape === 'kg-cluster') enterGroup(node.id)
    else if (node.shape === 'kg-group') setActiveGroup(node.id)
    if (node.shape === 'kg-ref') gotoNode(node.getData()?.target)
    // 项目画布上的幽灵占位：点一下就去建它——那正是它摆在那儿的意义
    if (node.shape === 'kg-node' && node.getData()?.ghost) {
      const pt = projectPoint(node.id)
      buildPoint({ id: node.id, name: pt?.name || node.id, why: pt?.why || '',
                   project: currentProject.value, field: projectField() })
      return
    }
    // 对话模式下点图上的节点 → 把它带进下一轮上下文，省掉"我想问 XX"这句打字
    if (mode.value === 'chat' && node.shape === 'kg-node' && !node.getData()?.orphan) {
      chatFocus.value = describe(node.id)
    }
  }))
  g.on('node:dblclick', safe(({ node }) => {
    if (node.shape === 'kg-note') editNote(node.id.slice(5))
    else if (node.shape === 'kg-group') exitGroup()   // 双击域的空白处退回全景
  }))
  g.on('node:resized', safe(({ node }) => {
    if (!ready.value) return
    const { width, height } = node.size()
    const pos = node.position()
    if (node.shape === 'kg-image') {
      saveList('img', (item) => (item.id === node.id.slice(4)
        ? { ...item, x: Math.round(pos.x), y: Math.round(pos.y), w: Math.round(width), h: Math.round(height) }
        : item))
      return
    }
    if (node.shape === 'kg-group') resizeGroup(node)
  }))
  // 点聚合边展开这对分组之间的明细，再点收起；点普通边则挂上拐点手柄
  g.on('edge:click', safe(({ edge, x, y }) => {
    // 绕行路由让线贴着分组框的上沿走，而边的点击热区有十来像素宽——
    // 于是**点域标题条会点到线上**，工具条再也弹不出来（e2e 抓到的：点中的是 `丁->乙#依赖`）。
    // 标题条是那个域唯一的把手，必须赢：点在标题条范围内就当点了这个域。
    const head = groupHeadAt(x, y)
    if (head) { setActiveGroup(head); return }
    const data = edge.getData() || {}
    if (data.kind === 'agg') {
      const next = new Set(expanded.value)
      next.has(data.pair) ? next.delete(data.pair) : next.add(data.pair)
      expanded.value = next
      render()
      return
    }
    editVertices(edge)
  }))
  g.on('edge:dblclick', safe(({ edge }) => {
    if ((edge.getData() || {}).kind !== 'edge') return
    edge.setVertices([])
    edge.removeTools()
    patcher.value.queueEdge(edge.id, null)       // 删掉这条记录，边回到默认走线
    const edges = { ...(layoutDoc.value.edges || {}) }
    delete edges[edge.id]
    layoutDoc.value = { ...layoutDoc.value, edges }
    setBanner('已清掉这条边的手工拐点')
  }))
  g.on('edge:change:vertices', safe(({ edge }) => {
    if (!ready.value || !writable() || (edge.getData() || {}).kind !== 'edge') return
    const vertices = edge.getVertices().map((v) => ({ x: Math.round(v.x), y: Math.round(v.y) }))
    const style = { vertices, router: layoutDoc.value.edges?.[edge.id]?.router || null }
    layoutDoc.value = { ...layoutDoc.value, edges: { ...(layoutDoc.value.edges || {}), [edge.id]: style } }
    patcher.value.queueEdge(edge.id, style)
  }))
  g.on('scale', safe(onZoom))
  g.on('translate', safe(() => { saveViewport(); syncView() }))
  // 右键菜单：浏览器自带的菜单让位，画布自己弹
  g.container.addEventListener('contextmenu', (ev) => ev.preventDefault())
  g.on('node:contextmenu', safe(({ node, e }) => {
    // 借来的点不归这块画布管：右键菜单里全是写操作（移入分组 / 从画布去掉 / 定稿），
    // 对一个"只是路过"的点执行它们，改的是别处的账。要动它就去全局图。
    if (node.getData()?.borrowed) {
      setBanner(`「${node.id}」是借来的外部邻居，不属于这个项目——要改它请去全局图`)
      return
    }
    const kind = { 'kg-node': 'node', 'kg-group': 'group', 'kg-cluster': 'cluster' }[node.shape]
      || (['kg-note', 'kg-image', 'kg-ref'].includes(node.shape) ? 'deco' : null)
    if (kind) openMenu(kind, node.id, e)
  }))
  g.on('edge:contextmenu', safe(({ edge, e }) => {
    if ((edge.getData() || {}).kind === 'edge') openMenu('edge', edge.id, e)
  }))
  g.on('blank:contextmenu', safe(({ e }) => openMenu('blank', null, e)))
}

/**
 * 拖动簇卡片 = 拖动它代表的那个分组。
 *
 * 簇里的节点和子分组没有建 cell（那正是折叠的意义），X6 不会替我们移动它们，
 * 所以这里按位移量把它们在 layout 里整体平移——否则展开之后节点还留在原地。
 */
function moveCluster(node) {
  const gid = node.id
  const box = layoutDoc.value.groups[gid]
  if (!box) return
  const [moved] = movedPositions(node)
  const dx = moved.x - Math.round(box.x)
  const dy = moved.y - Math.round(box.y)
  if (!dx && !dy) return
  queueIfChanged('group', gid, { x: moved.x, y: moved.y })
  for (const [id, sub] of Object.entries(layoutDoc.value.groups)) {
    if (id !== gid && groupAncestors(layoutDoc.value.groups, id).includes(gid)) {
      queueIfChanged('group', id, { x: Math.round(sub.x + dx), y: Math.round(sub.y + dy) })
    }
  }
  for (const [id, n] of Object.entries(layoutDoc.value.nodes)) {
    const chain = n.group ? [n.group, ...groupAncestors(layoutDoc.value.groups, n.group)] : []
    if (chain.includes(gid)) queueIfChanged('node', id, { x: Math.round(n.x + dx), y: Math.round(n.y + dy) })
  }
}

/** 给边挂上拐点手柄：拖圆点造拐点，双击边清空。别的边先摘掉手柄，画面才不乱。 */
function editVertices(edge) {
  if (!writable()) return
  const g = graph.value
  g.getEdges().forEach((e) => e.id !== edge.id && e.removeTools())
  edge.addTools([{ name: 'vertices', args: { attrs: { r: 5, fill: '#fff', stroke: '#3b6fe0', strokeWidth: 2 } } }])
  setBanner('拖动边上的圆点调拐点，双击这条边清掉拐点')
}

// 某个节点的边在画布上的 cell id：组内边是本身，跨组边是它所属的那一束聚合边
function relatedEdgeIds(nodeId) {
  const layout = layoutDoc.value
  const fams = visibleFamilies()
  const groupOf = (nid) => layout.nodes[nid]?.group || null
  const ids = new Set()
  for (const e of indexDoc.value.edges) {
    if (e.source !== nodeId && e.target !== nodeId) continue
    if (!fams.has(e.family)) continue
    if (!layout.nodes[e.source] || !layout.nodes[e.target]) continue
    const a = groupOf(e.source)
    const b = groupOf(e.target)
    const pair = a && b ? `${a}->${b}` : null
    ids.add(!aggregate.value || !pair || a === b || expanded.value.has(pair) ? e.id : `agg:${pair}`)
  }
  return ids
}

function focus(nodeId) {
  if (pathHit.value) return              // 路径点亮时不让单节点高亮把它冲掉
  highlightEdges(graph.value, nodeId ? relatedEdgeIds(nodeId) : null, { flow: true })
}

// ---- 右键菜单：画布上每类元素一套动作 ----

/** 给菜单 builder 的一份状态快照。builder 是纯函数（canvas/menus.js），
 *  它不认识 ref——这里把要用到的那几样摊平递进去。 */
function menuCtx() {
  return { index: indexDoc.value, layout: layoutDoc.value, dueIds: dueIds.value,
           pathFrom: pathFrom.value, pathHit: !!pathHit.value, neighbor: neighbor.value,
           focusGroup: focusGroup.value, showMap: showMap.value, nodeName, selectedIds: selectedNodeIds() }
}

/** 画布上当前框选 / 多选的知识点 id（Selection 插件只让 kg-node 进选区）。 */
function selectedNodeIds() {
  const cells = graph.value?.getSelectedCells?.() || []
  return cells.filter((c) => c.shape === 'kg-node').map((c) => c.id)
}

function openMenu(kind, id, ev) {
  // 历史视图是只读浏览视图；项目画布和全局图一样可操作。
  // （这里和 `writable()` 是同一个判据，必须一起改——只改一处的话
  //   会出现"拖得动却右键不出菜单"这种半瘫状态。）
  if (!writable()) return
  ctxTarget = { kind, id, at: graph.value.clientToLocal(ev.clientX, ev.clientY) }
  ctx.value = { x: ev.clientX, y: ev.clientY, ...buildMenu(kind, id, menuCtx()) }
}

const MENU_ACTIONS = {
  relate: (id) => { relating.value = describe(id) },
  'build-ghost': (id) => {
    const pt = projectPoint(id)
    buildPoint({ id, name: pt?.name || id, why: pt?.why || '',
                 project: currentProject.value, field: projectField() })
  },
  'drop-ghost': (id) => {
    // 只从这块画布上拿掉，**不动清单**：清单是"要学什么"，画布是"怎么摆"，两回事
    patcher.value.queueNode(id, null)
    layoutDoc.value = { ...layoutDoc.value,
                        nodes: Object.fromEntries(
                          Object.entries(layoutDoc.value.nodes).filter(([k]) => k !== id)) }
    render()
    setBanner(`已从画布上去掉「${id}」——清单里还留着它`)
  },
  ref: (id) => { selected.value = describe(id); addRef() },
  finalize: (id) => finalize(id),
  draft: (id) => { queueIfChanged('node', id, { state: 'draft' }); render(); setBanner(`「${id}」已标记为草稿`) },
  review: (id) => markReviewed(id),
  detail: (id) => gotoNode(id),
  neighbor: (id) => toggleNeighbor(id),
  'path-from': (id) => startPath(id),
  'path-to': (id) => endPath(id),
  'path-clear': () => clearPathHighlight(),
  obsidian: (id) => openInObsidian(id),
  copy: (id) => copyWikiLink(id),
  unplace: (id) => unplace(id),

  'open-doc': (gid) => gotoNode(layoutDoc.value.groups[gid]?.doc),
  'new-doc': (gid, at) => openDocDialog(gid, at),
  'unbind-doc': (gid) => bindDoc(gid, null),
  'as-doc': (id) => bindDoc(layoutDoc.value.nodes[id]?.group, id),
  collapse: (gid) => setPinned(gid, 'collapsed'),
  enter: (gid) => enterGroup(gid),
  'pin-expanded': (gid) => setPinned(gid, layoutDoc.value.groups[gid]?.pinned === 'expanded' ? null : 'expanded'),
  'pin-auto': (gid) => setPinned(gid, null),
  rename: (gid) => renameGroup(gid),
  dissolve: (gid) => dissolveGroup(gid),
  summarize: (gid, at) => summarizeGroup(gid, at),
  'summarize-selected': (_id, at) => summarizeSelected(selectedNodeIds(), at),
  ...Object.fromEntries(Object.keys(GROUP_LAYOUTS).map(
    (kind) => [`inner-${kind}`, (gid) => runGroupLayout(gid, kind)])),
  'exit-focus': () => exitGroup(),

  'edge-delete': (id) => deleteEdge(id),
  'edge-clear': (id) => clearEdgeVertices(id),
  'edge-source': (id) => gotoNode(indexDoc.value?.edges.find((e) => e.id === id)?.source),
  'edge-target': (id) => gotoNode(indexDoc.value?.edges.find((e) => e.id === id)?.target),

  'deco-edit': (cellId) => editNote(cellId.slice(cellId.indexOf(':') + 1)),
  'deco-delete': (cellId) => deleteDecoration(cellId),

  'new-group': (_id, at) => newGroup(at),
  'new-subgroup': (gid, at) => newGroup(at, gid),
  'new-node': (id, at) => openNodeDialog(at, layoutDoc.value?.groups?.[id] ? id : innermostGroupAt(at.x, at.y)),
  note: (_id, at) => addNote(at),
  image: () => { panel.value = 'assets' },
  fit: () => fit(),
  map: () => toggleMap(),
  inbox: () => openPanel('inbox'),
}

function onMenuPick(action) {
  const target = ctxTarget
  ctx.value = null
  if (!target) return
  MENU_ACTIONS[action]?.(target.id, target.at)
}

// ---- 菜单背后的动作 ----

/** 折叠状态：pinned 是唯一的"用户意志"，null 表示交回给缩放自动判定。 */
function setPinned(gid, value) {
  if (!layoutDoc.value.groups[gid]) return
  if (focusGroup.value === gid && value === 'collapsed') exitGroup()
  queueIfChanged('group', gid, { pinned: value })
  render()
  setBanner(value === 'collapsed' ? '已折叠；再次展开用右键菜单或放大'
    : value === 'expanded' ? '已钉住展开：缩小也不会收成簇卡片'
      : '已恢复自动折叠：缩小到看不清字时自动收起', 'success')
}

/**
 * 拖分组框的把手改大小。
 *
 * 从上边 / 左边拖时，X6 改的不只是尺寸：它把整个框平移了，而 Node.translate 会
 * 递归带上所有子元素。也就是说框里的知识点也跟着挪了位置——画面上看不出来
 * （它们和框的相对位置没变），但 layout.json 里还是老坐标，不落盘的话刷新一次全跳回去。
 * 所以这里连子元素的新坐标一起写。
 */
function resizeGroup(node) {
  if (!writable()) return
  const { width, height } = node.size()
  const pos = node.position()
  const box = { x: Math.round(pos.x), y: Math.round(pos.y),
                w: Math.round(width), h: Math.round(height) }
  queueIfChanged('group', node.id, box)
  for (const p of movedPositions(node).slice(1)) queueIfChanged(p.kind, p.id, { x: p.x, y: p.y })
  // 不走 render()：画布上的框已经是新尺寸了，重绘只会闪一下、顺手把刚拉出来的把手也拆掉。
  // syncView 只更新小地图和贴在框上的工具条位置，正是这次改动影响到的两样东西。
  syncView()
  warnIfOverflow(node.id, box)
}

/** 框缩到装不下里面的东西时说一声——节点露在自己的域外面，读图时会误以为它不属于这个域。 */
function warnIfOverflow(gid, box) {
  const out = Object.entries(layoutDoc.value.nodes).filter(([, n]) => n.group === gid)
    .filter(([, n]) => n.x < box.x || n.y < box.y
      || n.x + (n.w || 160) > box.x + box.w || n.y + (n.h || 60) > box.y + box.h).length
  if (out) setBanner(`有 ${out} 个知识点露在「${layoutDoc.value.groups[gid]?.name || gid}」框外了`, 'error')
}

/**
 * 只重排一个域内部，域的位置和别的域一概不动。
 *
 * 和"换布局"那条路径不同，这里不走预览态：影响面只有一个框，撤销一步就回去了，
 * 再套一层"预览 / 确认"反而碍事。
 */
function runGroupLayout(gid, kind) {
  if (!writable()) return
  const result = layoutGroup(indexDoc.value, layoutDoc.value, gid, kind)
  if (!result) {
    setBanner(kind === 'mindmap' ? '这个域里没有结构族的边，搭不出层级——试试力导向或网格'
      : '这个域里的知识点不够两个，没什么可排的', 'error')
    return
  }
  for (const [id, pos] of Object.entries(result.nodes)) queueIfChanged('node', id, pos)
  queueIfChanged('group', gid, result.box)
  render()
  setBanner(`「${layoutDoc.value.groups[gid]?.name || gid}」已按${GROUP_LAYOUTS[kind].label}重排`
    + `（${membersOf(layoutDoc.value, gid).length} 个知识点，⌘Z 撤销）`, 'success')
}

function renameGroup(gid) {
  const cur = layoutDoc.value.groups[gid]
  if (!cur) return
  const name = window.prompt('这个域叫什么', cur.name || '')
  if (name === null || !name.trim()) return
  queueIfChanged('group', gid, { name: name.trim() })
  render()
}

/**
 * 解散一个框：框没了，里面的东西一个不动——节点、子簇、便签都留在原坐标，只是不再归它。
 *
 * 为项目画布去父框而生（2026-09-18）：老项目画布还带着"一份清单一个框"的 g-list-N，
 * 用这一下拆掉。全局图上也能用，等于"只删框不删内容"——删框从来不该连带删知识点的位置。
 * 里面的东西改归外一层：有父框就归父框，没有就自由；子簇的 parent 同理。
 */
function dissolveGroup(gid) {
  const box = layoutDoc.value.groups[gid]
  if (!box || !writable()) return
  if (!dirtyBefore) dirtyBefore = clone(layoutDoc.value)
  const outer = box.parent || null
  const groups = { ...layoutDoc.value.groups }
  delete groups[gid]
  for (const [cid, g] of Object.entries(groups)) {
    if (g.parent !== gid) continue
    groups[cid] = { ...g, parent: outer }
    patcher.value.queueGroup(cid, { parent: outer })
  }
  const nodes = { ...layoutDoc.value.nodes }
  let kept = 0
  for (const [nid, n] of Object.entries(nodes)) {
    if (n.group !== gid) continue
    nodes[nid] = { ...n, group: outer }
    patcher.value.queueNode(nid, { group: outer })
    kept++
  }
  layoutDoc.value = { ...layoutDoc.value, groups, nodes }
  for (const kind of Object.keys(LIST_KEY)) {
    saveList(kind, (item) => (item.group === gid ? { ...item, group: outer } : item))
  }
  patcher.value.queueGroup(gid, null)
  if (focusGroup.value === gid) exitGroup()
  if (activeGroup.value === gid) { activeGroup.value = null; groupBarAt.value = null }
  render()
  const where = outer ? `，改归「${groups[outer]?.name}」` : ''
  setBanner(`已解散「${box.name}」：${kept} 个知识点留在原位${where}`, 'success')
}

/**
 * 新建簇：落点就是左上角，给一个能装下两行卡片的初始大小，拖进去的节点自动归它。
 * 传 parent 就是在某个域里开子域——层次化的"看得见"那一半（另一半是结构族关系边）。
 */
function newGroup(at, parent = null) {
  const name = window.prompt(parent ? `在「${layoutDoc.value.groups[parent]?.name}」里新建子簇` : '新簇的名称', '')
  if (!name || !name.trim()) return
  const gid = `g-${Date.now().toString(36)}`
  const host = parent ? layoutDoc.value.groups[parent] : null
  // 子簇要落在父框里面，否则 X6 的父子关系和 layout 的 parent 对不上
  const x = host ? Math.max(Math.round(at.x), Math.round(host.x) + 16) : Math.round(at.x)
  const y = host ? Math.max(Math.round(at.y), Math.round(host.y) + 40) : Math.round(at.y)
  const w = host ? Math.min(640, Math.round(host.x + host.w) - x - 16) : 640
  const h = host ? Math.min(320, Math.round(host.y + host.h) - y - 16) : 320
  const box = { name: name.trim(), x, y, w: Math.max(w, 200), h: Math.max(h, 120),
                parent, collapsed: false, pinned: null, color: null }
  layoutDoc.value = { ...layoutDoc.value, groups: { ...layoutDoc.value.groups, [gid]: box } }
  patcher.value.queueGroup(gid, box)
  render()
  setBanner(parent ? `已在「${layoutDoc.value.groups[parent]?.name}」里建出「${box.name}」子簇`
    : `已建「${box.name}」：把知识点拖进框里就归它`, 'success')
}

function openInObsidian(id) {
  fetchNode(id).then((d) => { window.location.href = d.obsidian_uri })
    .catch((err) => setBanner(`打不开：${err.message}`, 'error'))
}

async function copyWikiLink(id) {
  const meta = indexDoc.value?.nodes.find((n) => n.id === id)
  const text = `[[${meta?.name || id}]]`
  try {
    await navigator.clipboard.writeText(text)
    setBanner(`已复制 ${text}`, 'success')
  } catch {
    setBanner(`复制失败，手动抄一下：${text}`, 'error')
  }
}

/** 移出画布：只删 layout 记录，md 原封不动，节点回到 Inbox。 */
function unplace(id) {
  if (!layoutDoc.value.nodes[id]) return
  if (!dirtyBefore) dirtyBefore = clone(layoutDoc.value)
  const nodes = { ...layoutDoc.value.nodes }
  delete nodes[id]
  layoutDoc.value = { ...layoutDoc.value, nodes }
  patcher.value.queueNode(id, null)
  if (selected.value?.id === id) { selected.value = null; detail.value = null }
  if (neighbor.value === id) neighbor.value = null
  render()
  refreshInbox()
  setBanner(`「${id}」已移出画布，md 没动，在 Inbox 里可以再放回来`, 'success')
}

function deleteDecoration(cellId) {
  const [kind, id] = cellId.split(':')
  saveList(kind, (item) => (item.id === id ? null : item))
  render()
}

function clearEdgeVertices(id) {
  const edge = graph.value.getCellById(id)
  if (edge) { edge.setVertices([]); edge.removeTools() }
  patcher.value.queueEdge(id, null)
  const edges = { ...(layoutDoc.value.edges || {}) }
  delete edges[id]
  layoutDoc.value = { ...layoutDoc.value, edges }
}

// ---- 域的总览文档：簇终于有了"知识"那一半 ----

/**
 * 给一个域绑总览文档。
 *
 * 文档本身是一个再普通不过的知识点（md 在 fields/ 或 nodes/ 下），只是在 layout 里记一笔
 * "这个域的总览是它"。所以它天然能写正文、连关系、在 Obsidian 打开——不用为簇再造一套。
 */
function bindDoc(gid, nodeId) {
  if (!gid || !layoutDoc.value.groups[gid]) return
  queueIfChanged('group', gid, { doc: nodeId })
  render()
  const name = layoutDoc.value.groups[gid].name
  setBanner(nodeId ? `「${nodeId}」成了「${name}」的总览文档` : `「${name}」的总览文档已解绑（md 没动）`,
            'success')
}

// ---- 概括节点（第六步）：框里的点 / 框选的点 → 一个上位节点 + 「包含」边 ----

/** 把一个框里的点概括成一个节点：模型起草，预填进新建对话框，建完绑成这个框的总览。 */
async function summarizeGroup(gid, at) {
  const g = layoutDoc.value?.groups?.[gid]
  if (!g) return
  const ids = Object.entries(layoutDoc.value.nodes).filter(([, n]) => n.group === gid).map(([id]) => id)
  await openSummarizeDialog(ids, { name: g.name.replace(/（\d+）$/, ''), asDoc: gid, group: gid, groupName: g.name,
                                   at: at || { x: g.x + 40, y: g.y + 60 } })
}

/** 框选的几个点概括成一个节点：不属于任何框时可勾「顺手建个框把它们圈起来」。 */
async function summarizeSelected(ids, at) {
  if (ids.length < 2) { setBanner('先按住 Shift 框选至少两个点', 'error'); return }
  const groups = new Set(ids.map((id) => layoutDoc.value.nodes[id]?.group || null))
  const common = groups.size === 1 ? [...groups][0] : null
  await openSummarizeDialog(ids, { name: '', group: common, groupName: common ? layoutDoc.value.groups[common]?.name : '',
                                   at, wrapOption: true, wrap: !common })
}

/**
 * 让模型起草，再开新建对话框。起草失败（没配 LLM、超时）也照样开——只是正文留空自己写：
 * 概括是人的判断，模型只是先垫一稿，垫不出来不该挡着人建节点。
 */
async function openSummarizeDialog(ids, extra) {
  const base = { field: majority(extra.group, (n) => n?.field) || majority(null, (n) => n?.field) || '',
                 dir: majority(extra.group, (n) => dirOf(n)) || nodeDirs.value[0] || 'nodes',
                 contains: ids, ...extra }
  setBanner(`正在让模型概括 ${ids.length} 个点…（一次模型调用，几秒到几十秒）`, 'info')
  status.value = 'saving'
  try {
    const draft = await postSummarize({ node_ids: ids, name: extra.name || null })
    creating.value = { ...base, name: draft.name || extra.name, desc: draft.desc, body: draft.body,
                       layer: draft.layer || '', year: draft.year || null, contains: draft.children, aiDraft: true }
    setBanner('草稿已预填，带「AI 建议」标记的都是模型写的，改成你自己的判断再创建', 'success')
  } catch (err) {
    creating.value = { ...base, aiDraft: false }
    setBanner(`模型没起出草稿（${err.body?.detail?.message || err.body?.detail || err.message}），先自己写吧`, 'error')
  } finally {
    status.value = 'saved'
  }
}

/** 按几个点的包围盒建一个框并把它们归进去；返回 gid。 */
function wrapNodesInGroup(ids, name) {
  const boxes = ids.map((id) => layoutDoc.value.nodes[id]).filter(Boolean)
  if (!boxes.length) return null
  const x0 = Math.min(...boxes.map((b) => b.x)) - 24
  const y0 = Math.min(...boxes.map((b) => b.y)) - 44
  const x1 = Math.max(...boxes.map((b) => b.x + (b.w || NODE_W))) + 24
  const y1 = Math.max(...boxes.map((b) => b.y + (b.h || NODE_H))) + 24 + NODE_H + 36   // 给概括节点留一行
  const parent = boxes[0].group || null
  const gid = `g-${Date.now().toString(36)}`
  const box = { name, x: Math.round(x0), y: Math.round(y0), w: Math.round(x1 - x0), h: Math.round(y1 - y0),
                parent, collapsed: false, pinned: null, color: null }
  if (!dirtyBefore) dirtyBefore = clone(layoutDoc.value)
  const nodes = { ...layoutDoc.value.nodes }
  for (const id of ids) {
    if (!nodes[id]) continue
    nodes[id] = { ...nodes[id], group: gid }
    patcher.value.queueNode(id, { group: gid })
  }
  layoutDoc.value = { ...layoutDoc.value, groups: { ...layoutDoc.value.groups, [gid]: box }, nodes }
  patcher.value.queueGroup(gid, box)
  return gid
}

/** 框里给概括节点落脚的位置：框底部那一行的左边。 */
function groupSpot(gid) {
  const g = layoutDoc.value.groups[gid]
  return { x: g.x + 24 + 80, y: g.y + g.h - NODE_H - 24 + 26 }
}

/** 新建总览文档：名字默认跟域同名，默认落在 fields/（规范 2：领域总览住这儿）。 */
function openDocDialog(gid, at) {
  const g = layoutDoc.value?.groups?.[gid]
  if (!g) return
  creating.value = {
    at: at || { x: g.x + 40, y: g.y + 60 }, group: gid, groupName: g.name, asDoc: gid,
    name: g.name.replace(/（\d+）$/, ''),
    field: majority(gid, (n) => n?.field) || '',
    dir: nodeDirs.value.includes('fields') ? 'fields' : (majority(gid, (n) => dirOf(n)) || 'nodes'),
  }
}

// ---- 新建知识点：画布右键 → 写一个新 md → 立刻放上画布 ----

/** nodes/ 下已有的目录，新节点默认跟着"同一个域里其他节点"走。 */
const dirOf = (n) => (n?.path ? n.path.split('/').slice(0, -1).join('/') : null)

const nodeDirs = computed(() => {
  const dirs = new Set(['nodes'])
  for (const n of indexDoc.value?.nodes || []) {
    const d = dirOf(n)
    if (d) dirs.add(d)
  }
  return [...dirs].filter(Boolean).sort()
})

const fieldNames = computed(() =>
  [...new Set((indexDoc.value?.nodes || []).map((n) => n.field).filter(Boolean))].sort())

const takenIds = computed(() => new Set((indexDoc.value?.nodes || []).map((n) => n.id)))

/** id → 展示名：测验里的考点 chip、以及任何只拿得到 id 的地方。 */
const nodeNames = computed(() =>
  Object.fromEntries((indexDoc.value?.nodes || []).map((n) => [n.id, n.name || n.id])))

/** 这个域里的节点大多存在哪个目录 / 属于哪个领域——新建时拿它当默认值。 */
function majority(gid, pick) {
  const tally = new Map()
  const byId = new Map((indexDoc.value?.nodes || []).map((n) => [n.id, n]))
  for (const [nid, n] of Object.entries(layoutDoc.value?.nodes || {})) {
    if (gid && n.group !== gid) continue
    const v = pick(byId.get(nid))
    if (v) tally.set(v, (tally.get(v) || 0) + 1)
  }
  return [...tally.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || null
}

function openNodeDialog(at, gid) {
  creating.value = {
    at, group: gid || null,
    groupName: gid ? layoutDoc.value?.groups?.[gid]?.name : '',
    field: majority(gid, (n) => n?.field) || majority(null, (n) => n?.field) || '',
    dir: majority(gid, (n) => dirOf(n)) || nodeDirs.value[nodeDirs.value.length - 1] || 'nodes',
  }
}

async function createNode(form) {
  const spot = creating.value
  creating.value = null
  status.value = 'saving'
  try {
    const contains = form.contains || []
    const res = await writeChanges([{
      type: 'create_node', source: form.id, path: `${form.dir}/${form.id}.md`,
      fields: { name: form.name, field: form.field, desc: form.desc,
                ...(form.year ? { year: form.year } : {}),
                ...(form.layer ? { layer: form.layer } : {}),
                learned: new Date().toISOString().slice(0, 10) },
      ...(form.body ? { body: form.body } : {}),
    // 概括节点：同一批里给每个子节点连一条「包含」边——写回通道支持给刚建的节点挂边，
    // 不用等索引刷新再发第二次请求（中间那一刻图上多一个孤岛）
    }, ...contains.map((child) => ({ type: 'add_edge', source: form.id, relation: '包含', target: child }))])
    // 勾了「顺手建个框」：先按子节点的包围盒建框、把它们归进去，再让新点落在框里
    const wrapGid = form.wrap && contains.length ? wrapNodesInGroup(contains, form.name) : null
    const spotForPlace = wrapGid ? { group: wrapGid, at: groupSpot(wrapGid) } : spot
    await placeNew([form.id], spotForPlace)
    await reloadIndex()
    if (spot?.asDoc) bindDoc(spot.asDoc, form.id)
    else if (wrapGid) bindDoc(wrapGid, form.id)
    status.value = 'saved'
    setBanner(`已新建 ${res.files[0]?.path || form.id}`, 'success')
    gotoNode(form.id)
    if (form.thenRelate) relating.value = describe(form.id)
    fetchSuggestions(form.id)
  } catch (err) {
    status.value = 'error'
    setBanner(`新建失败：${err.body?.detail?.message || err.body?.detail || err.message}`, 'error')
  }
}

/** 新节点落到右键的那个点上；不在任何域里就交给服务端按领域找位置。
 *  **一律落 draft（金色虚线），不直接 final**：位置是机器按邻居投票猜的，得由人确认
 *  （设计文档 4.1「程序只写 draft、只在分组内、不动 final」）。 */
/**
 * 项目画布上的落位不走 `/api/place`：那个接口只写**全局图**、也只往分组框里塞，
 * 而项目画布 2026-09-18 起没有框。以前项目模式下新建一个点会拿项目 revision 去打全局图
 * （必 409、被吞掉），再把全局 layout 读回来顶掉项目画布——点建出来了，画布却换了一张。
 *
 * 这里直接把坐标写进项目 layout（和拖拽同一条补丁通道）：落在鼠标点，没有就落视口中央；
 * 松手处在人自己建的框里就归那个框。原来是幽灵占位的，原地转成草稿，进正常的 draft → final 流程。
 * 顺手把点归进项目清单——项目画布只画清单里的点，不归就看不见。
 */
/**
 * 把清单里还没上画布的点停到右下角当"待学区"：没建的画幽灵、建了的落草稿。
 * 初始布局本来就把幽灵铺在右下角（core.build_project_layout），这是它的增量版——
 * 清单后来加的点不会自己长出幽灵，这一下补上。不自动补：「从画布上去掉」是人的决定，
 * 每次刷新又长回来等于不让人去掉。
 */
function parkMissing() {
  const ids = missingPoints.value
  if (!ids.length) return
  const built = new Set(indexDoc.value.nodes.filter((n) => !n.virtual).map((n) => n.id))
  const n = parkNodes(ids, (id) => (built.has(id) ? 'draft' : 'ghost'))
  if (n) setBanner(`已把 ${n} 个点停到右下角：没建的是幽灵，建了的是草稿`, 'success')
}

/**
 * 把一批点停到画布右下角，状态由 `stateOf(id)` 决定（ghost / draft）。已经在画布上的跳过。
 * 初始布局、「放到右下角」、导入落位三处共用：右下角就是这块画布的"待学 / 待归位"区。
 */
function parkNodes(ids, stateOf) {
  const fresh = ids.filter((id) => !layoutDoc.value.nodes[id])
  if (!fresh.length || !writable()) return 0
  if (!dirtyBefore) dirtyBefore = clone(layoutDoc.value)
  const box = contentBBox(layoutDoc.value)
  const x0 = box ? Math.round(box.x + box.width) + 120 : 80
  const y0 = box ? Math.round(box.y + box.height) - NODE_H : 80
  const cols = Math.max(1, Math.min(4, Math.ceil(Math.sqrt(fresh.length))))
  const today = new Date().toISOString().slice(0, 10)
  const nodes = { ...layoutDoc.value.nodes }
  fresh.forEach((id, i) => {
    const state = stateOf(id)
    const entry = { x: x0 + (i % cols) * (NODE_W + 44), y: y0 + Math.floor(i / cols) * (NODE_H + 36),
                    w: NODE_W, h: NODE_H, group: null, state,
                    placedAt: state === 'ghost' ? null : today, anchor: null }
    nodes[id] = entry
    patcher.value.queueNode(id, entry)
  })
  layoutDoc.value = { ...layoutDoc.value, nodes }
  render()
  return fresh.length
}

/**
 * 导入写入之后的落位——项目下：认领的点原地从幽灵转草稿，其余新点停右下角，并全部补进清单；
 * 全局：走放置接口按邻居投票找位置，放不下的进 Inbox。补充过的老节点只刷索引，位置不动。
 */
async function onImported({ result, born, claimed, enriched }) {
  try {
    await load()
    if (layoutName()) {
      const rest = born.filter((id) => !claimed.includes(id))
      if (claimed.length) await placeLocal(claimed, null)
      parkNodes(rest, () => 'draft')
      if (currentProject.value && born.length) await addToList({ project: currentProject.value, list: 0, points: born })
    } else if (born.length) {
      await placeNew(born, null)
    }
    const where = layoutName() ? `${claimed.length} 个落在幽灵原位、${born.length - claimed.length} 个停到右下角`
                               : '新点按建议放上全局图，放不下的在 Inbox'
    setBanner(`导入完成：写入 ${result.files.length} 个文件，${where}`
              + (enriched.length ? `，补充了 ${enriched.join('、')}` : '')
              + (result.pending.length ? `，${result.pending.length} 条边待审` : ''), 'success')
    if (born.length === 1) gotoNode(born[0])
  } catch (err) {
    setBanner(`导入已写入，但落位失败：${err.message}`, 'error')
  }
}

async function placeLocal(ids, at) {
  const spot = at ? { x: Math.round(at.x), y: Math.round(at.y) } : viewportCenter()
  const gid = innermostGroupAt(spot.x, spot.y)
  const today = new Date().toISOString().slice(0, 10)
  const nodes = { ...layoutDoc.value.nodes }
  ids.forEach((id, i) => {
    const cur = nodes[id]
    if (cur && cur.state !== 'ghost') return
    const box = cur ? { ...cur, state: 'draft', placedAt: today }
                    : { x: spot.x, y: spot.y + i * (NODE_H + 36), w: NODE_W, h: NODE_H,
                        group: gid, state: 'draft', placedAt: today, anchor: null }
    nodes[id] = box
    patcher.value.queueNode(id, cur ? { state: 'draft', placedAt: today } : box)
  })
  layoutDoc.value = { ...layoutDoc.value, nodes }
  if (currentProject.value) await addToList({ project: currentProject.value, list: 0, points: ids })
  render()
}

async function placeNew(ids, spot) {
  const list = Array.isArray(ids) ? ids : [ids]
  if (!list.length) return
  if (layoutName()) return placeLocal(list, spot?.at)
  await patcher.value.flush()
  const body = { base_revision: revision.value, ids: list, state: 'draft' }
  if (spot?.group) {
    body.group = spot.group
    body.at = { x: Math.round(spot.at.x - 80), y: Math.round(spot.at.y - 26) }
  }
  try {
    await postPlace(body)
  } catch { /* 放不下也无所谓：md 已经写出来了，它会出现在 Inbox 里 */ }
  const fresh = await fetchLayout()
  layoutDoc.value = fresh.layout
  revision.value = fresh.layout.revision
}

// ---- AI 建议 ----

/**
 * 欠账清单里点「让 AI 补一轮」：**一次调用**问完一批缺 year 的节点。
 *
 * 不做成"逐个节点在检查器里问"：实盘 41 个点缺 year，那样是 41 次调用；
 * 而判断"这个概念哪年出现"用不着候选节点和关系类型表，一次问完几毛钱。
 */
async function openYears() {
  yearsOpen.value = true
  yearProposal.value = null
  yearsBusy.value = true
  try {
    yearProposal.value = await postYearsPropose({})
  } catch (err) {
    yearsOpen.value = false
    setBanner(`补 year 失败：${err.body?.detail || err.message}`, 'error')
  } finally {
    yearsBusy.value = false
    refreshUsage()
  }
}

/** 勾好的那些写回 md 的 frontmatter。走 /api/changes 这唯一入口，备份和 diff 一样不少。 */
async function applyYears(rows) {
  yearsBusy.value = true
  try {
    const res = await writeChanges(rows.map((r) => ({ type: 'update_frontmatter', source: r.id,
                                                      fields: { year: r.year } })))
    yearsOpen.value = false
    await load()
    refreshDigest()
    setBanner(`已给 ${rows.length} 个节点补上 year（${res.files.length} 个文件），`
              + `原文备份在 ${res.backup}。历史视图里它们现在有位置了`, 'success')
  } catch (err) {
    setBanner(`写回失败：${err.body?.detail?.message || err.body?.detail || err.message}`, 'error')
  } finally {
    yearsBusy.value = false
  }
}

/** 欠账清单里点「问 AI 连什么」：先定位过去（检查器跟着选中它），再要建议。
 *  **不另造一套建议 UI**——检查器里那一套已经能逐条采纳，再做一份只会两边不一致。 */
async function suggestFromDigest(id) {
  await gotoNode(id)
  if (selected.value?.id === id) fetchSuggestions(id)
}

async function fetchSuggestions(nodeId) {
  if (!nodeId) return
  suggesting.value = true
  suggestions.value = null
  try {
    const result = await postSuggest({ node_id: nodeId, index_revision: indexRevision.value })
    // 只有当前选中节点还是那个才赋值（用户可能已经切走了）
    if (selected.value?.id === nodeId) suggestions.value = result
  } catch (err) {
    setBanner(`AI 建议失败：${err.body?.detail || err.message}`, 'error')
  } finally {
    suggesting.value = false
    refreshUsage()
  }
}

function dismissSuggestion(index) {
  if (!suggestions.value?.edges) return
  const next = { ...suggestions.value, edges: suggestions.value.edges.filter((_, i) => i !== index) }
  suggestions.value = next
}

// ---- 建立 / 删除关系：直接写回 md（服务端写前自动备份） ----

const familyOfType = (type) => (indexDoc.value?.families || []).find((f) => f.types.includes(type))?.name || null

const placedIds = computed(() => new Set(Object.keys(layoutDoc.value?.nodes || {})))

/** 建立关系对话框里给每个候选标上"已经连过哪些"，避免重复连（服务端也会拒）。 */
const linkedOf = computed(() => {
  const out = {}
  const id = relating.value?.id
  if (!id || !indexDoc.value) return out
  for (const e of indexDoc.value.edges) {
    const other = e.source === id ? e.target : e.target === id ? e.source : null
    if (!other) continue
    if (!out[other]) out[other] = []
    out[other].push(e.type)
  }
  return out
})

/** 今日清单里的孤点点「连边」。清单里带着现成建议就预填，没有就只定位到它、
 *  让人自己在对话框里搜目标——**孤点本身就是那条任务**，有没有建议都该能动手。 */
async function linkFromToday(item) {
  await gotoNode(item.id)
  if (item.link?.target) relatePreset.value = { relation: item.link.relation, target: item.link.target }
  relating.value = describe(item.id)
}

/**
 * 欠账清单里点「连边」：把建议的类型和目标填进关系对话框，人确认了才写。
 *
 * **不直接写回**——建议算出来的 `包含` / `对比` 是按名字猜的（「堆内存」含着「内存」
 * ⇒ 多半是它的一种），猜错了写进 md 就是一条骗人的边，而边一旦写进去，后面的
 * 复习、出题、最短解释链全都会照着它跑。
 */
function linkFromDigest({ source, target, relation }) {
  relatePreset.value = { relation, target }
  relating.value = describe(source)
}

async function createRelation({ relation, target, swap }) {
  const src = relating.value
  if (!src) return
  const from = swap ? target : src.id
  const to = swap ? src.id : target
  relating.value = null
  status.value = 'saving'
  try {
    const res = await writeChanges([{ type: 'add_edge', source: from, relation, target: to }])
    await ensurePlaced(to)
    await ensurePlaced(from)
    const family = familyOfType(relation)
    if (family && !visible[family]) visible[family] = true   // 刚连的线必须看得见
    await reloadIndex()
    status.value = 'saved'
    setBanner(`已写入「${from} ${relation} → ${to}」，原文备份在 ${res.backup}`, 'success')
    flyToPair(from, to)
  } catch (err) {
    status.value = 'error'
    setBanner(`建立关系失败：${err.body?.detail?.message || err.body?.detail || err.message}`, 'error')
  }
}

/** 保存正文：只换 frontmatter 与 `## 关系` 之间那一段，其余逐字保留（服务端也是这么切的）。 */
async function saveBody(text) {
  const id = selected.value?.id
  if (!id) return
  status.value = 'saving'
  try {
    const res = await writeChanges([{ type: 'update_body', source: id, body: text }])
    await reloadIndex()
    status.value = 'saved'
    setBanner(`已保存「${id}」的正文，原文备份在 ${res.backup}`, 'success')
  } catch (err) {
    status.value = 'error'
    setBanner(`保存正文失败：${err.body?.detail?.message || err.body?.detail || err.message}`, 'error')
  }
}

/** 工具条上的「写内容」：先定位到总览文档，再让检查器把编辑框打开。 */
function writeDoc(docId) {
  if (!docId) return
  gotoNode(docId)
  writeNonce.value++
}

async function deleteEdge(edgeId) {
  const e = indexDoc.value?.edges.find((x) => x.id === edgeId)
  if (!e) return
  status.value = 'saving'
  try {
    await writeChanges([{ type: 'remove_edge', source: e.source, relation: e.type, target: e.target }])
    await reloadIndex()
    status.value = 'saved'
    setBanner(`已删除「${e.source} ${e.type} → ${e.target}」`, 'success')
  } catch (err) {
    status.value = 'error'
    setBanner(`删除失败：${err.body?.detail?.message || err.body?.detail || err.message}`, 'error')
  }
}

/** 目标还在 Inbox 里就先放上画布，不然刚建的关系没有线可看。 */
async function ensurePlaced(id) {
  if (layoutDoc.value.nodes[id]) return
  if (layoutName()) return placeLocal([id], null)
  await patcher.value.flush()
  try {
    await postPlace({ base_revision: revision.value, ids: [id] })
    const fresh = await fetchLayout()
    layoutDoc.value = fresh.layout
    revision.value = fresh.layout.revision
  } catch { /* 放不下不影响 md 里那条关系 */ }
}

/** md 变了之后只重读索引重画，不动视口（写回一条关系不该把画面弹回去）。 */
async function reloadIndex() {
  const index = await fetchIndex()
  indexDoc.value = index
  indexRevision.value = index.revision
  Object.assign(stats, { nodes: index.stats.nodes, edges: index.stats.edges, stubs: index.stats.stubs })
  render()
  refreshInbox()
  // 进度是服务端按 index 现算的（建没建、是不是只有壳），md 一变它就旧了。
  // 不在这里跟着刷的话，点完「建」清单上那个点还写着「未建」，非得手动刷新页面
  // ——派生数据不落盘，代价就是每次源数据变都得让它重算一遍。
  if (plansDoc.value) refreshPlans()
  if (selected.value) await loadDetail(selected.value.id)
}

/** 落点取整：开了吸附就贴到网格，没开就照旧四舍五入到整数像素。 */
function snapped({ x, y }) {
  if (!snap.value) return { x: Math.round(x), y: Math.round(y) }
  const { dx, dy } = snapDelta(x, y)
  return { x: Math.round(x + dx), y: Math.round(y + dy) }
}

function toggleSnap() {
  snap.value = !snap.value
  localStorage.setItem('knowrary-snap', snap.value ? '1' : '0')
  setSnap(graph.value, snap.value)
  setBanner(snap.value ? '对齐已开：拖动时出参考线，松手贴到 8px 网格'
    : '对齐已关：位置完全按手放的地方存', 'success')
}

function toggleAvoid() {
  avoidNodes.value = !avoidNodes.value
  localStorage.setItem('knowrary-avoid', avoidNodes.value ? '1' : '0')
  render({ view: 'keep' })
  setBanner(avoidNodes.value ? '连线会绕开卡片了（直角走线；手工拐过的边仍然听你的）'
    : '连线恢复直连', 'success')
}

/** 借不借外部邻居。**只活在这一次会话里**，刷新就回到关——理由见 borrowOn 的注释。 */
function toggleBorrow() {
  borrowOn.value = !borrowOn.value
  render()
}

function toggleMap() {
  showMap.value = !showMap.value
  localStorage.setItem('knowrary-map', showMap.value ? '1' : '0')
  if (showMap.value) syncView()      // 关着的时候视口框没跟着算，开回来先对一次
}

// ---- 阶段 3：节点详情 + Markdown 写回（所有写回都走 ChangeSet，确认前不碰文件）----

async function loadDetail(id) {
  detail.value = null
  try {
    detail.value = await fetchNode(id)
  } catch (err) {
    if (err.status !== 404) setBanner(`读取节点失败：${err.body?.detail || err.message}`, 'error')
  }
}

const relationTypes = computed(() =>
  (indexDoc.value?.families || []).map((f) => ({ family: f.name, types: f.types, meta: f.meta || {} })))

const allNodeIds = computed(() => (indexDoc.value?.nodes || []).map((n) => n.id))

function queueChange(change) {
  pending.value = [...pending.value, change]
  changePreview.value = null
  inspectorHidden.value = false
}

function addEdgeDraft(draft) {
  if (!detail.value || !draft.relation || !draft.target) return
  queueChange({
    type: 'add_edge', source: detail.value.id, relation: draft.relation, target: draft.target,
    ...(draft.year ? { year: Number(draft.year) } : {}),
    ...(draft.note ? { note: draft.note } : {}),
  })
}

function removeEdge(edge) {
  queueChange({ type: 'remove_edge', source: detail.value.id, relation: edge.type, target: edge.target })
}

function retypeEdge(edge, relation) {
  if (!relation || relation === edge.type) return
  queueChange({ type: 'update_edge', source: detail.value.id, target: edge.target,
                from_relation: edge.type, relation })
}

function editDesc() {
  const value = window.prompt('新的一句话摘要（desc）', detail.value?.meta?.desc || '')
  if (value === null) return
  queueChange({ type: 'update_frontmatter', source: detail.value.id, fields: { desc: value } })
}

/** 欠账清单里点「挪过去」：把 field 和所在域对不上的那个点挪回去，那条道不存在就现开一条。
 *
 * **只挪人点的这一个**（带 ids），不顺手扫全图：批量凭空长出几条道，会把手排的画布搅乱。
 * "程序不动已定稿的东西"管的是背着人的批量行为，不是"人点了这一个"。
 */
async function fixMisplaced(item) {
  await patcher.value.flush()
  try {
    const res = await postRegroup({ base_revision: revision.value, ids: [item.id], create_lane: true })
    const fresh = await fetchLayout(layoutName())
    layoutDoc.value = fresh.layout
    revision.value = fresh.layout.revision
    render()
    refreshDigest()
    if (res.placed.length) {
      const lane = res.grown_groups?.length ? `（顺带新开了「${item.layer}」这条道）` : ''
      setBanner(`已把「${item.id}」挪进 ${item.field}${item.layer ? `/${item.layer}` : ''}${lane}`, 'success')
      gotoNode(item.id)
    } else {
      setBanner(res.skipped[0]?.reason || '没挪动', 'error')
    }
  } catch (err) {
    setBanner(`挪动失败：${err.body?.detail?.message || err.body?.detail || err.message}`, 'error')
  }
}

/** 按层归位：把落在领域大框里的草稿挪进对应泳道。只动草稿（4.1 程序不动定稿的东西）。 */
async function regroupDrafts() {
  await patcher.value.flush()
  try {
    const res = await postRegroup({ base_revision: revision.value })
    const fresh = await fetchLayout()
    layoutDoc.value = fresh.layout
    revision.value = fresh.layout.revision
    render()
    refreshDigest()
    const stuck = res.skipped.length
    setBanner(res.placed.length
      ? `归位 ${res.placed.length} 个${stuck ? `，${stuck} 个那一层放不下` : ''}`
      : (stuck ? `都没挪动：${res.skipped[0].reason}` : '没有需要归位的：草稿都在自己那一层了'),
      res.placed.length ? 'success' : 'info')
  } catch (err) {
    setBanner(`归位失败：${err.body?.detail?.message || err.body?.detail || err.message}`, 'error')
  }
}

/** 改抽象层：和改摘要一样走变更卡，人看过 diff 再写盘（4.4 只提议不越权）。 */
function setLayer({ id, layer }) {
  if (!id) return
  queueChange({ type: 'update_frontmatter', source: id, fields: { layer: layer || '' } })
}

/** 采纳 AI 建议的年份。同样走变更卡——机器给的年份更要经人过目，
 *  它错了不会报错，只会把这个点在历史视图上摆到错误的位置。 */
function setYear({ id, year }) {
  if (!id || !year) return
  queueChange({ type: 'update_frontmatter', source: id, fields: { year: Number(year) } })
}

async function previewChanges() {
  if (!pending.value.length) return
  try {
    changePreview.value = await writeChanges(pending.value, { dryRun: true })
    setBanner('')
  } catch (err) {
    changePreview.value = null
    setBanner(`变更被拒绝：${err.body?.detail?.message || err.body?.detail || err.message}`, 'error')
  }
}

/**
 * 走 /api/changes 的唯一出口：**撞上「索引已更新」自己刷一次再试**。
 *
 * base_revision 是整张索引的粗粒度闸：你在 Obsidian 里随手改一个字，索引就重建、
 * revision 就变，于是这一页上后面**每一次**写回都 409——变更卡、新建、改摘要全部
 * 卡死，只能刷新页面。（真实使用里就是这么炸的：手改了一个文件，之后所有卡片都失败。）
 *
 * 自动重试是安全的：真正的保护是**每个文件的 digest**（core.plan 会拿索引里的指纹
 * 比对磁盘原文，被人改过就抛 WriteConflict），刷新索引后写的是**改过之后**的那一版，
 * 不会覆盖掉人手写的东西。digest 对不上时仍然 409，但 detail 是一句话而不是带
 * current_revision 的对象——那种不重试，原样报给人看。
 */
async function writeChanges(changes, { dryRun = false } = {}) {
  const body = { base_revision: indexRevision.value, dry_run: dryRun, changes }
  try {
    return await postChanges(body)
  } catch (err) {
    const current = err.status === 409 ? err.body?.detail?.current_revision : null
    if (!current) throw err
    const fresh = await fetchIndex()
    indexDoc.value = fresh
    indexRevision.value = fresh.revision
    return postChanges({ ...body, base_revision: fresh.revision })
  }
}

async function applyChanges() {
  try {
    const res = await writeChanges(pending.value)
    pending.value = []
    changePreview.value = null
    const id = detail.value?.id
    await load()
    if (id) await loadDetail(id)
    setBanner(`已写回 ${res.files.length} 个文件，原文备份在 ${res.backup}`, 'success')
  } catch (err) {
    setBanner(`写回失败：${err.body?.detail?.message || err.body?.detail || err.message}`, 'error')
  }
}

function dropChange(i) {
  pending.value = pending.value.filter((_, idx) => idx !== i)
  changePreview.value = null
}

/** 搜索命中 / 3D 跳回来：展开它所在的域、居中并选中。 */
function gotoNode(id) {
  const place = layoutDoc.value?.nodes?.[id]
  if (!place) {
    setBanner(`「${id}」还没放到画布上（在 Inbox 里）`, 'error')
    return
  }
  const g = graph.value
  if (place.group && collapsedIds.value.has(place.group)) {
    if (!panorama) panorama = { zoom: g.zoom(), translate: g.translate() }
    focusGroup.value = place.group
  }
  render()
  applyingViewport.value = true
  g.zoomTo(Math.max(g.zoom(), 0.8))
  g.centerPoint(place.x + 90, place.y + 30)
  applyingViewport.value = false
  zoom.value = g.zoom()
  const cell = g.getCellById(id)
  if (cell) {
    g.cleanSelection?.()
    g.select?.(cell)
  }
  selected.value = describe(id)
  inspectorHidden.value = false
  loadDetail(id)
  focus(id)
  search.value = ''
}

const searchHits = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return []
  return (indexDoc.value?.nodes || [])
    .filter((n) => !n.virtual && `${n.id} ${n.name || ''} ${n.desc || ''}`.toLowerCase().includes(q))
    .slice(0, 8)
})

// ---- 便签与引用卡：只存在 layout.json 里，和 md 无关 ----

const newId = (prefix) => `${prefix}${Date.now().toString(36)}`

function viewportCenter() {
  const g = graph.value
  const p = g.graphToLocal(g.container.clientWidth / 2, g.container.clientHeight / 2)
  return { x: Math.round(p.x - 90), y: Math.round(p.y - 30) }
}

function addNote(at) {
  const text = window.prompt('便签内容', '')
  if (!text) return
  const spot = at ? { x: Math.round(at.x), y: Math.round(at.y) } : viewportCenter()
  const notes = [...(layoutDoc.value.notes || []), { id: newId('nt'), text, ...spot, w: 190, h: 74 }]
  layoutDoc.value = { ...layoutDoc.value, notes }
  patcher.value.queueList('notes', notes)
  render()
}

// —— 阶段 4：Inbox 放置 / 欠账清单 / 复习 ——

async function refreshInbox() {
  try {
    const data = await fetchInbox()
    inboxItems.value = data.items
    inboxCount.value = data.items.length
  } catch { /* Inbox 拉不到不影响画布本身 */ }
}

async function refreshDue() {
  // 关掉「到期标记」时连拉都不拉：画布金点和活动栏角标读的是同一个 dueIds，
  // 清空它一处就够，不用在两个组件里各写一遍判断
  if (!reviewMarks.value) { dueList.value = []; dueIds.value = new Set(); return }
  try {
    const data = await fetchDue()
    dueList.value = data.due
    dueIds.value = new Set(data.due.map((d) => d.id))
  } catch { dueList.value = []; dueIds.value = new Set() }
}

async function refreshDigest() {
  digest.value = null
  try {
    digest.value = await fetchDigest()
  } catch (err) {
    setBanner(`欠账清单加载失败：${err.message}`, 'error')
  }
}

/** 放置是服务端算位置 + 服务端写盘，所以本地要整份重读，不能只改内存镜像。 */
async function place(body, label) {
  if (placing.value) return
  placing.value = true
  try {
    await patcher.value.flush()                  // 先把手上的拖拽落盘，revision 才对得上
    const res = await postPlace({ ...body, base_revision: revision.value })
    await load()
    const skipped = res.skipped.length ? `，${res.skipped.length} 个没放下（${res.skipped[0].reason}）` : ''
    const grown = res.grown_groups.length ? `，${res.grown_groups.length} 个分组框往下长了一行` : ''
    const made = res.created_groups?.length ? `，新开了「${res.created_groups.map((g) => layoutDoc.value.groups[g]?.name || g).join('、')}」域框` : ''
    setBanner(`${label}：放上 ${res.placed.length} 个草稿${made}${grown}${skipped}`,
              res.placed.length ? 'success' : 'error')
    if (res.placed.length === 1) gotoNode(res.placed[0].id)
  } catch (err) {
    const detailMsg = err.body?.detail?.message || err.body?.detail || err.message
    setBanner(`放置失败：${detailMsg}`, 'error')
    if (err.status === 409) await load()
  } finally {
    placing.value = false
  }
}

const placeOne = (item) => place({ ids: [item.id] }, `放置「${item.name}」`)
/** 画布上还没有这个领域的框：服务端在最下面开一个同名顶层框再放进去（加法，不动已有内容）。 */
const placeWithNewGroup = (item) => place({ ids: [item.id], create_field_group: true },
                                          `建「${item.field}」域框并放入「${item.name}」`)

/** Inbox 里的零边节点：不在画布上，gotoNode 跳不过去；直接把它选中、开检查器、要一轮建议。 */
function suggestForInbox(item) {
  selected.value = describe(item.id)
  inspectorHidden.value = false
  fetchSuggestions(item.id)
}
const placeAll = () => place({ ids: inboxItems.value.map((i) => i.id) }, '全部按建议放置')

/** 从 Inbox 拖到画布：落点由鼠标决定，落在哪个分组框里就归哪个组。 */
async function onCanvasDrop(ev) {
  const id = ev.dataTransfer?.getData('text/knowrary-node')
  if (!id) return
  ev.preventDefault()
  const g = graph.value
  const p = g.clientToLocal(ev.clientX, ev.clientY)
  if (layoutName()) {                          // 项目画布没有父框：松手在哪儿就落哪儿
    await placeLocal([id], { x: p.x - 90, y: p.y - 30 })
    setBanner(`已把「${id}」放到项目画布上，并归进这个项目的清单`, 'success')
    return
  }
  const gid = innermostGroupAt(p.x, p.y)
  if (!gid) {
    setBanner('松手的位置不在任何分组框里——拖到某个分组框内，或用条目上的按钮放置', 'error')
    return
  }
  place({ ids: [id], group: gid, at: { x: Math.round(p.x - 90), y: Math.round(p.y - 30) } }, `放置「${id}」`)
}

/** 命中点所在的最内层分组框（嵌套时取最小的那个）。 */
function innermostGroupAt(x, y) {
  const groups = layoutDoc.value?.groups || {}
  let best = null
  for (const [gid, box] of Object.entries(groups)) {
    if (x < box.x || y < box.y || x > box.x + box.w || y > box.y + box.h) continue
    if (!best || box.w * box.h < best.area) best = { gid, area: box.w * box.h }
  }
  return best?.gid || null
}

async function markReviewed(id, grade = '记得') {
  try {
    const res = await postReview(id, grade)
    afterReview([res.id])
    const tail = grade === '忘了' ? `，明天（${res.next_due}）再考一次` : `，下次 ${res.next_due} 再来`
    setBanner(`「${id}」记为「${grade}」${tail}`, grade === '忘了' ? 'info' : 'success')
  } catch (err) {
    setBanner(`记录复习失败：${err.message}`, 'error')
  }
}

/** 复习状态变了之后的统一收尾：到期角标、面板、画布都跟着刷新。 */
function afterReview(ids) {
  const next = new Set(dueIds.value)
  ids.forEach((id) => next.delete(id))
  dueIds.value = next
  dueList.value = dueList.value.filter((d) => !ids.includes(d.id))
  render()
  if (panel.value === 'digest') refreshDigest()
  if (panel.value === 'study') { refreshToday(); refreshDue() }
}

// —— 重命名：改 id 并把引用一起迁走 ——

/** 传 null 表示"名字改了，上一次的影响面作废"。 */
async function previewRename(next) {
  if (!next) { renameImpact.value = null; return }
  renameBusy.value = true
  try {
    const res = await postRename({ old_id: renaming.value.id, new_id: next,
                                   base_revision: indexRevision.value, dry_run: true })
    renameImpact.value = res.impact
  } catch (err) {
    setBanner(`改名不成：${err.body?.detail?.error || err.body?.detail || err.message}`, 'error')
  } finally {
    renameBusy.value = false
  }
}

async function applyRename(next) {
  renameBusy.value = true
  const old = renaming.value.id
  try {
    await postRename({ old_id: old, new_id: next, base_revision: indexRevision.value, dry_run: false })
    renaming.value = null
    renameImpact.value = null
    await load()                       // id 变了，索引和布局都得整份重读
    gotoNode(next)
    setBanner(`「${old}」已改名为「${next}」，引用都迁过去了`, 'success')
  } catch (err) {
    setBanner(`改名失败：${err.body?.detail?.error || err.body?.detail || err.message}`, 'error')
  } finally {
    renameBusy.value = false
  }
}

function openRename(id) {
  renameImpact.value = null
  renaming.value = { id, name: describe(id)?.name || id }
}

// —— 合并重复节点 ——

function openMerge(pair) {
  mergeImpact.value = null
  merging.value = pair
}

/** 对调保留/丢弃。方向一换，上一次算的影响面就作废。 */
function swapMerge() {
  merging.value = { keep: merging.value.drop, drop: merging.value.keep }
  mergeImpact.value = null
}

async function previewMerge() {
  mergeBusy.value = true
  try {
    const res = await postMerge({ ...idsOf(merging.value), base_revision: indexRevision.value,
                                  dry_run: true })
    mergeImpact.value = res.impact
  } catch (err) {
    setBanner(`合并不成：${err.body?.detail?.error || err.body?.detail || err.message}`, 'error')
  } finally {
    mergeBusy.value = false
  }
}

async function applyMerge() {
  mergeBusy.value = true
  const { keep, drop } = merging.value
  try {
    await postMerge({ keep_id: keep, drop_id: drop, base_revision: indexRevision.value,
                      dry_run: false })
    merging.value = null
    mergeImpact.value = null
    await load()                      // 少了一个节点、边也变了，整份重读
    refreshDigest()
    gotoNode(keep)
    setBanner(`已把「${drop}」并进「${keep}」，引用都迁过去了`, 'success')
  } catch (err) {
    setBanner(`合并失败：${err.body?.detail?.error || err.body?.detail || err.message}`, 'error')
  } finally {
    mergeBusy.value = false
  }
}

const idsOf = (p) => ({ keep_id: p.keep, drop_id: p.drop })

// —— 模型用量：所有 LLM 调用都记账，状态栏常驻读数 ——

async function refreshUsage() {
  try {
    usage.value = await fetchUsage()
  } catch { /* 账本拉不到不影响任何正事 */ }
}

/** 调完模型就刷一次账本——"实时关注"的实时就靠这个。 */
const llmBusy = computed(() => quizBusy.value || planProposing.value || suggesting.value)

/** 换当前项目：今日清单、出题范围、对话留档都跟着换。 */
/** 换当前项目。**项目画布是另一份文件，必须重新拉**——
 *  只 render 一下的话画的还是上一个项目的 layout（看起来就是"空白/少了一半点"），
 *  而且接下来的拖拽会拿着旧 revision 去写新文件，必 409。 */
async function switchProject(id) {
  if (id === currentProject.value) return          // 空串是合法值：「全局」那一条线
  await patcher.value?.flush()              // 上一个项目的改动先落盘，别带到下一份去
  const wasLayout = layoutName()
  currentProject.value = id
  try { localStorage.setItem('knowrary-project', id) } catch { /* 无痕模式 */ }
  if (panel.value === 'study') refreshToday()
  if (panel.value === 'calendar') refreshCalendar()
  // 换项目也可能换作用域（对话模式下「全局」那套和项目那套不是一批工具），
  // 开着的面板要是在新作用域里没有入口，跟切视图一样关掉
  if (!panelOk(panel.value, mode.value, id)) panel.value = ''
  // **画布跟着换。** 判据是"这一屏该画哪份 layout"，不是"现在是不是项目图模式"——
  // 对话模式右边那块图用的也是项目画布，只按模式判会漏掉它：
  // 在 A 项目下聊天、切到 B，背后还摆着 A 的图。
  if (layoutName() !== wasLayout) {
    history.reset()                         // 撤销栈是跟着某一份 layout 的，换了就作废
    histVer.value++
    await reloadLayout()
    if (mode.value !== 'history') render({ view: mode.value === 'chat' ? 'stored' : 'fit' })
  }
  if (!id && mode.value === 'project') {
    // 「全局」没有项目画布。留在一个空模式里只会让人困惑，直接退到全局图。
    await switchMode('structure')
    setBanner('「全局」不绑项目，没有项目画布——已经切到全局图')
  }
  if (mode.value === 'chat') {
    // 对话按项目分线：换项目等于换一条线。上一段没丢——它一直在 .knowrary/chat/<项目>/ 里。
    // 走 newChatSession 而不是手写"清空 + 换 id"：梳理游标是跟着会话走的，
    // 漏掉它的话换过项目之后按钮的灰/亮状态还停在上一段。
    newChatSession()
    loadChatHistory()
  }
}

/** 一天弹一次。没东西可做的日子也弹——"今天没有到期的"本身就是有用的信息。 */
function maybeBrief() {
  const day = todayList.value?.generated_at
  if (!day || briefOn.value) return
  if (!reviewOn.value || settings.value.review_brief === false) return
  try {
    if (localStorage.getItem(BRIEF_KEY) === day) return
    localStorage.setItem(BRIEF_KEY, day)
  } catch { /* 无痕模式读不到 localStorage：那就每次都弹，总比不弹好 */ }
  briefOn.value = true
}

/** 简报上点一条：直接进到能动手的那一步，不用再自己找面板。 */
function briefStart(item) {
  briefOn.value = false
  if (item.kind === 'unbuilt') buildPoint(item)
  else if (item.kind === 'shell') writeBody(item)
  else gotoNode(item.id)
}


/** 卡上改过摘要 / 正文之后「重算 diff」：dry_run 一次，卡片上的 diff 换成改后的。
 *  和真写入走同一个 preview，所以看到什么就落什么。 */
async function previewChatCard({ card, i, j }) {
  chatBusy.value = true
  try {
    const res = await writeChanges(card.changes, { dryRun: true })
    Object.assign(chatLog.value[i].cards[j], { files: res.files, stale: false })
    setBanner('')
  } catch (err) {
    setBanner(`改法过不了校验：${err.body?.detail?.message || err.body?.detail || err.message}`, 'error')
  } finally {
    chatBusy.value = false
  }
}

/** 变更卡上的「写入」：走的仍然是 /api/changes 这唯一入口，和详情面板一模一样。 */
async function applyChatCard({ card, i, j }) {
  chatBusy.value = true
  try {
    const res = await writeChanges(card.changes)
    // 卡上改过的话 diff 是旧的：换成真写下去的那份，留档里看到的就是落盘的样子
    Object.assign(chatLog.value[i].cards[j], { applied: true, editing: false, stale: false, files: res.files })
    await load()
    // 学完之后图谱自动长出来（重构方案 §4）：新建的点自动上画布，落 draft 等人定稿。
    // 不这么做的话它只会掉进 Inbox，还得自己去点「放进去」。
    const born = card.changes.filter((c) => c.type === 'create_node').map((c) => c.source)
    if (born.length) await placeNew(born, null)
    // 顺手补进项目清单：**一次点击两件事一起落**。节点建出来了、清单却没列它的话，
    // 项目进度不认它，今日清单也不会再提它。
    if (card.into?.points?.length) await addToList(card.into)
    // 采纳并且真落了文件 —— 这才算"整理过了"，游标推到这条回复为止。
    // 放在这里而不是梳理那一轮结束时：梳理完没点写入的，下次还得重梳。
    await advanceTidied(i)
    setBanner(`已写回 ${res.files.length} 个文件${born.length ? `，${born.length} 个新点已落到画布上（草稿）` : ''}，`
              + `原文备份在 ${res.backup}`, 'success')
  } catch (err) {
    setBanner(`写回失败：${err.body?.detail?.message || err.body?.detail || err.message}`, 'error')
  } finally {
    chatBusy.value = false
  }
}

// —— 阶段 10：今日清单（教练调度，不调 LLM）——

async function refreshToday() {
  try {
    todayList.value = await fetchToday(currentProject.value || null)
    maybeBrief()
  } catch (err) {
    setBanner(`今日清单加载失败：${err.message}`, 'error')
  }
}

/** 清单里「只有壳」的一条 → 跳过去并把正文编辑框展开。 */
function writeBody(item) {
  gotoNode(item.id)
  writeNonce.value += 1
}

/** 清单里 Inbox 的一条 → 交给服务端按邻居投票找位置放上画布，放完重排清单。 */
async function placeFromToday(item) {
  await placeOne(item)
  refreshToday()
}

// —— 阶段 10：学习计划（只写 plans.json）——

async function refreshPlans() {
  try {
    const data = await fetchProjects()
    plansDoc.value = data.doc
    // 记住上次看的项目：这是个界面偏好，localStorage 就够了（不需要第二份存储）
    // 开局那次已经从 localStorage 读过了，这里只负责校验：
    // 存的项目要是没了（被删 / 换了 vault），退回全局；从没存过就落到第一个项目。
    const known = data.doc.projects || {}
    if (currentProject.value && !known[currentProject.value]) {
      currentProject.value = ''
      await reloadLayout()                        // 画布跟着退回全局图
      render({ view: 'stored' })
    } else if (!projectPicked && !currentProject.value) {
      let saved = null
      try { saved = localStorage.getItem('knowrary-project') } catch { /* 无痕模式 */ }
      if (saved === null) {
        currentProject.value = Object.keys(known)[0] || ''
        // **选完项目必须把画布也换过去。** 走到这一支说明本机没存过项目（首次用 / 无痕 /
        // 换了台机器），`load()` 早就按"没有项目"拉了全局图；这里刚把项目选上，
        // layoutName() 跟着变成项目图，内存里那份却还是全局的。
        // 不补这一下，后面 switchMode 的 `layoutName() !== wasLayout` 判据永远相等，
        // 再也纠正不回来——表现是对话 / 项目图上摆着全局图，在上面定稿写不进去。
        if (currentProject.value) {
          await reloadLayout()
          render({ view: 'stored' })
        }
      }
    }
    projectPicked = true
    plansProgress.value = data.progress
    plansSchedules.value = data.schedules || {}
  } catch (err) {
    setBanner(`项目加载失败：${err.message}`, 'error')
  }
}

/** 整份提交。base_revision 对不上就重新拉取——本地改动还在面板里，不会丢。 */
async function savePlans(projects) {
  plansBusy.value = true
  try {
    const res = await putProjects({ base_revision: plansDoc.value?.revision ?? 0, projects })
    plansDoc.value = { ...plansDoc.value, revision: res.revision, projects }
    plansProgress.value = res.progress
    plansSchedules.value = res.schedules || {}
    setBanner('项目已保存', 'success')
  } catch (err) {
    if (err.status === 409) {
      setBanner('项目在别处被改过了，已重新拉取，请再存一次', 'error')
      refreshPlans()
    } else {
      setBanner(`保存项目失败：${err.body?.detail || err.message}`, 'error')
    }
  } finally {
    plansBusy.value = false
  }
}

/** 目标 → 要点。传 null 表示收起提议区。只提议，不写任何文件。 */
async function proposePlan(req) {
  if (!req) { planProposal.value = null; return }
  if (!req.goal?.trim() || planProposing.value) return
  planProposing.value = true
  try {
    const res = await postPlanPropose({ goal: req.goal.trim(), plan_name: req.plan_name || '',
                                        kind: req.kind || '学习', coach: req.coach || '',
                                        target_date: req.target_date || null,
                                        weekly_hours: req.weekly_hours || 7,
                                        mode: req.mode || '标准' })
    if (!res.stages.length) {
      setBanner('这次没拆出要点来——把目标写具体一点再试', 'error')
      return
    }
    planProposal.value = res
  } catch (err) {
    setBanner(`拆解失败：${err.body?.detail || err.message}`, 'error')
  } finally {
    planProposing.value = false
    refreshUsage()
  }
}

/** 某个领域的知识点通常落在哪个目录；这个领域一个节点都还没有就给它一个新目录。 */
function dirForField(field) {
  if (!field) return ''
  const tally = new Map()
  for (const n of indexDoc.value?.nodes || []) {
    if (n.field !== field) continue
    const d = dirOf(n)
    if (d) tally.set(d, (tally.get(d) || 0) + 1)
  }
  const top = [...tally.entries()].sort((a, b) => b[1] - a[1])[0]?.[0]
  return top || `nodes/${field}`
}

/**
 * 计划里「未建」的点 → 开新建对话框，领域和目录都由**这份计划**决定。
 *
 * 不能走 openNodeDialog 的默认值：那套是按"画布上正指着哪个域"算的，
 * 从计划面板点进来时根本没有 activeGroup，就退化成全库多数派——
 * 于是「神经网络与反向传播」被塞进 nodes/02-计算机硬件/。
 * 计划自己知道它属于哪个领域，就该由它说了算；它也说不出时宁可留空让人选，不硬塞。
 */
function buildPoint(point) {
  const field = point.field || plansDoc.value?.projects?.[point.project]?.field || ''
  const box = layoutDoc.value?.viewport
  // 抽象层和年份是**拆计划那一次调用顺手给的建议**，存在清单点上（PlanPoint.layer / year）。
  // 三条路径（计划面板 / 画布幽灵 / 今日清单）送进来的对象形状不一样——只有计划面板给的是
  // 清单里那个点本身，另外两条只挑了 name / why。所以在这儿统一回查一次，
  // 而不是让三处各记得补一遍（漏一处的表现就是"同一个点从这边进有预填、从那边进没有"）。
  const pt = projectPoint(point.id, point.project)
  openNodeDialog({ x: (box?.cx ?? 0) - 80, y: (box?.cy ?? 0) - 30 }, null)
  creating.value = { ...creating.value, name: point.id, field, dir: dirForField(field),
                     groupName: '', planWhy: point.why || '',
                     layer: point.layer || pt?.layer || '',
                     year: point.year || pt?.year || null }
}

// —— 阶段 9：测验（出题走 LLM，自评三档回写复习调度；全程不碰 md）——

// 检查器、今日清单那些地方点「考一下」时不带档位：按当前项目的默认档来，
// 而不是一律用最难的——「只想了解的历史」被按精通考一遍，人就再也不想点这个按钮了
const projectLevel = computed(() => plansDoc.value?.projects?.[currentProject.value]?.level || null)

/** 没交卷的那份题：出题花过钱，关掉对话框、刷新页面都不该让它蒸发。 */
async function refreshOpenQuiz() {
  if (!reviewOn.value) { openQuiz.value = null; return }   // 关着就别再提"你还有一份没交卷"
  try { openQuiz.value = (await fetchOpenQuiz()).quiz || null } catch { openQuiz.value = null }
}

function resumeQuiz() {
  if (!openQuiz.value) return
  quiz.value = { questions: openQuiz.value.questions,
                 index_revision: openQuiz.value.index_revision ?? null, warnings: [] }
  quizDiag.value = null
  openQuiz.value = null
}

async function discardQuiz() {
  try { await dropOpenQuiz() } catch { /* 删不掉也不挡事，下次进来还在而已 */ }
  openQuiz.value = null
}

async function startQuiz(arg) {
  const ids = Array.isArray(arg) ? arg : arg?.ids || []
  const style = Array.isArray(arg) ? '复习' : arg?.style || '复习'
  if (!ids.length || quizBusy.value) return
  quizBusy.value = true
  try {
    const res = await postQuiz({ node_ids: ids, count: Math.min(5, Math.max(3, ids.length)),
                                 style, level: (Array.isArray(arg) ? null : arg?.level) || projectLevel.value,
                                 coach: Array.isArray(arg) ? '' : arg?.coach || '' })
    ;(res.warnings || []).forEach((w) => pushToast(w, 'info'))
    if (!res.questions.length) {
      setBanner('这轮没出出题来——模型没给有效题目，换几个节点再试', 'error')
      return
    }
    quiz.value = res
    quizDiag.value = null
    openQuiz.value = null      // 这份现在就在手上，不用再提示「还有没答完的」
  } catch (err) {
    setBanner(`出题失败：${err.message}`, 'error')
  } finally {
    quizBusy.value = false
  }
}

/** 整轮比对：只读，不写盘。拿不到诊断也不挡交卷——按自评记就是了。 */
async function diagnoseQuiz(rows) {
  quizBusy.value = true
  try {
    const res = await postQuizDiagnose({
      // 判分要和出题用同一档：出题按「了解」问的，批改却按「精通」判，会白白多复习一轮
      answers: rows.map((r) => ({ question: r.question, grade: r.grade, my_answer: r.my_answer })),
      level: quiz.value?.level || projectLevel.value || null,
    })
    ;(res.warnings || []).forEach((w) => pushToast(w, 'info'))
    quizDiag.value = res
  } catch (err) {
    setBanner(`比对失败，按你的自评记：${err.message}`, 'error')
  } finally {
    quizBusy.value = false
    refreshUsage()
  }
}

async function submitQuiz(answers) {
  quizBusy.value = true
  try {
    const res = await postQuizGrade({ answers, index_revision: quiz.value?.index_revision ?? null })
    afterReview(res.reviewed.map((r) => r.id))
    const wrong = res.wrong.length
    setBanner(wrong ? `交卷：${answers.length} 题错 ${wrong} 题，错的已排进明天`
                    : `交卷：${answers.length} 题全对，间隔往后拉了`, wrong ? 'info' : 'success')
    quiz.value = null
    quizDiag.value = null
    openQuiz.value = null
  } catch (err) {
    setBanner(`交卷失败：${err.message}`, 'error')
  } finally {
    quizBusy.value = false
  }
}

/** 定稿：草稿位置确认下来，金色虚线框变成正常卡片。只改 layout，不碰 md。
 *
 * **不可写时要说出来。** queueIfChanged 在总闸关着时是静默 return 的，
 * 底下照样弹「已定稿」的话，人看到的是成功、盘上却没动——这个假成功比不生效更难查。
 */
function finalize(id) {
  if (layoutDoc.value.nodes[id]?.state !== 'draft') return
  if (!writable()) {
    setBanner(preview.value ? '正在预览布局，先落盘或取消预览再定稿' : '当前视图不写盘，定稿没生效', 'error')
    return
  }
  queueIfChanged('node', id, { state: 'final' })
  selected.value = describe(id)
  render()
  setBanner(`「${id}」已定稿`, 'success')
}

function addRef() {
  const target = selected.value?.id
  if (!target) return
  const refs = [...(layoutDoc.value.refs || []), { id: newId('r'), target, ...viewportCenter(), w: 170, h: 46 }]
  layoutDoc.value = { ...layoutDoc.value, refs }
  patcher.value.queueList('refs', refs)
  render()
}

/** 便签 / 引用卡被拖动或编辑后，整表写回（它们是带 id 的小集合）。 */
const LIST_KEY = { note: 'notes', ref: 'refs', img: 'images' }

function saveList(kind, updater) {
  if (!writable()) return
  const key = LIST_KEY[kind]
  const items = (layoutDoc.value[key] || []).map(updater).filter(Boolean)
  layoutDoc.value = { ...layoutDoc.value, [key]: items }
  patcher.value.queueList(key, items)
}

function moveDecoration(cellId, x, y) {
  const [kind, id] = cellId.split(':')
  saveList(kind, (item) => (item.id === id ? { ...item, x, y } : item))
}

/** 贴一张图：落在当前视口中心，大小先给 320×200，之后可以拖角拉伸。 */
function addImage(file) {
  const images = [...(layoutDoc.value.images || []),
                  { id: newId('im'), file, ...viewportCenter(), w: 320, h: 200 }]
  layoutDoc.value = { ...layoutDoc.value, images }
  patcher.value.queueList('images', images)
  render()
  setBanner(`已贴上 ${file}（拖角可以拉伸，只改 layout）`, 'success')
}

function editNote(id) {
  const note = (layoutDoc.value.notes || []).find((n) => n.id === id)
  const text = window.prompt('便签内容（清空即删除）', note?.text || '')
  if (text === null) return
  saveList('note', (item) => (item.id !== id ? item : text.trim() ? { ...item, text } : null))
  render()
}

function describe(id) {
  const index = indexDoc.value
  const node = index.nodes.find((n) => n.id === id)
  if (!node) return { id, orphan: true }
  const byId = new Map(index.edges.map((e) => [e.id, e]))
  const pick = (ids) => ids.map((eid) => byId.get(eid)).filter(Boolean)
  return { ...node, out: pick(node.out), in: pick(node.in), placed: layoutDoc.value.nodes[id] || null }
}

// —— 布局菜单：算法只生成"初始种子"，先预览、确认后才落盘 ——
const LAYOUTS = {
  mindmap: { label: '多中心脑图', run: () => mindmapLayout(indexDoc.value, layoutDoc.value), structure: true },
  community: { label: '社区发现重排（建议）', structure: false,
               run: () => communityLayout(indexDoc.value, layoutDoc.value) },
}

function runLayout(kind) {
  const spec = LAYOUTS[kind]
  if (!spec) return
  const serverBefore = layoutDoc.value
  const result = spec.run()
  preview.value = { serverLayout: layoutDoc.value, result, kind }
  layoutDoc.value = {
    ...layoutDoc.value,
    groups: { ...result.groups },
    nodes: Object.fromEntries(Object.entries(result.nodes).map(
      ([id, pos]) => [id, { ...(layoutDoc.value.nodes[id] || { w: 160, h: 60, state: 'final' }), ...pos }])),
  }
  if (spec.structure) visible['结构'] = true     // 脑图 / 径向的主干就是结构边
  expanded.value = new Set()
  const extra = kind === 'mindmap' ? `${result.stats.trees} 棵脑图 + ${result.stats.strays} 个未归入层级的节点`
    : kind === 'community'
      ? `louvain 发现 ${result.communities.length} 个社区，${compareWithGroups(result.communities, serverBefore).length} 个节点建议换组`
      : `${Object.keys(result.groups).length} 个分组重排`
  setBanner(`预览「${spec.label}」：${extra}`)
  render({ view: 'fit' })
}

async function applyPreview() {
  const { result, serverLayout } = preview.value
  status.value = 'saving'
  try {
    const saved = await patchLayout(toPatch(result, serverLayout, revision.value))
    preview.value = null
    await load()
    if (history.record(clone(serverLayout), clone(layoutDoc.value), '换布局')) histVer.value++
    status.value = 'saved'
    setBanner(saved.backup ? `已应用新布局，旧布局备份在 ${saved.backup}` : '已应用新布局', 'success')
  } catch (err) {
    status.value = 'error'
    setBanner(`应用失败：${err.body?.detail || err.message}`, 'error')
  }
}

function cancelPreview() {
  layoutDoc.value = preview.value.serverLayout
  preview.value = null
  setBanner('')
  render({ view: 'fit' })
}

/** 点开一个域：只展开它并放大到铺满视口（视口不落盘，纯浏览状态）。 */
function enterGroup(gid) {
  const box = layoutDoc.value.groups[gid]
  if (!box) return
  const g = graph.value
  if (!focusGroup.value) panorama = { zoom: g.zoom(), translate: g.translate() }
  focusGroup.value = gid
  render()
  setActiveGroup(gid)
  applyingViewport.value = true
  g.zoomToRect({ x: box.x - 60, y: box.y - 60, width: box.w + 120, height: box.h + 120 }, { maxScale: 1.4 })
  applyingViewport.value = false
  zoom.value = g.zoom()
}

/** 退回全景：还原进入前的视口。 */
function exitGroup() {
  if (!focusGroup.value) return
  const g = graph.value
  focusGroup.value = null
  activeGroup.value = null
  groupBarAt.value = null
  render()
  applyingViewport.value = true
  if (panorama) {
    g.zoomTo(panorama.zoom)
    g.translate(panorama.translate.tx, panorama.translate.ty)
  } else {
    g.zoomToFit({ padding: 60, maxScale: 1 })
  }
  applyingViewport.value = false
  panorama = null
  zoom.value = g.zoom()
}

async function restore(target, what) {
  const patch = diffPatch(layoutDoc.value, target)
  if (isEmptyPatch(patch)) return
  status.value = 'saving'
  try {
    await patchLayout({ base_revision: revision.value, ...patch })
    await load()
    status.value = 'saved'
    setBanner(what)
  } catch (err) {
    status.value = 'error'
    setBanner(`${what}失败：${err.body?.detail || err.message}`, 'error')
  }
}

async function undo() {
  if (preview.value || !canUndo.value) return
  const target = history.takeUndo()
  histVer.value++
  if (target) await restore(target, '已撤销上一步布局改动')
}

async function redo() {
  if (preview.value || !canRedo.value) return
  const target = history.takeRedo()
  histVer.value++
  if (target) await restore(target, '已重做')
}

/** Esc 的收起顺序：弹窗 → 左侧工具窗口 → 右侧检查器 → 退出聚焦。 */
function onEscape() {
  if (ctx.value) { ctx.value = null; return }
  if (tour.on) { stopTour(); setBanner(''); return }
  if (pathHit.value || pathFrom.value) { clearPathHighlight(); return }
  if (relating.value) { relating.value = null; return }
  if (creating.value) { creating.value = null; return }
  if (showHelp.value) { showHelp.value = false; return }
  if (neighbor.value) { toggleNeighbor(neighbor.value); return }
  if (panel.value) { panel.value = ''; return }
  if (inspectorOpen.value) { inspectorHidden.value = true; return }
  // 聚焦时 Esc 是"退回全景"（工具条跟着一起收）；没聚焦才轮到单独收工具条
  if (focusGroup.value) { exitGroup(); return }
  activeGroup.value = null
  groupBarAt.value = null
}

function onKeydown(e) {
  const el = e.target
  const tag = (el?.tagName || '').toLowerCase()
  const typing = tag === 'input' || tag === 'textarea' || tag === 'select' || el?.isContentEditable
  if (e.key === 'Escape' && !typing) { onEscape(); return }

  if (e.metaKey || e.ctrlKey) {
    const key = e.key.toLowerCase()
    if (key === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo() }
    else if (key === 'y') { e.preventDefault(); redo() }
    else if (key === 'l' && selected.value) { e.preventDefault(); relating.value = selected.value }
    return
  }
  if (typing || e.altKey) return

  // 单键快捷键：只在焦点不在输入框时生效
  if (e.key === '/') { e.preventDefault(); headerEl.value?.focus() }
  else if (e.key === '?') { e.preventDefault(); showHelp.value = !showHelp.value }
  else if (e.key === '1') switchMode('chat')
  else if (e.key === '2') switchMode('project')
  else if (e.key === '3') switchMode('structure')
  else if (e.key === '4') switchMode('history')
  else if (e.key === '5') switchMode('lineage')
  else if (e.key.toLowerCase() === 'f') fit()
  else if (e.key.toLowerCase() === 'i' && mode.value === 'structure') openPanel('inbox')
  else if (e.key.toLowerCase() === 'd') openPanel('digest')
}

function applyThemeNow() {
  setTheme(theme.value)
  document.documentElement.dataset.theme = theme.value
  localStorage.setItem('knowrary-theme', theme.value)
  // 不用 X6 的 drawBackground/drawGrid 就地换肤：实测它会把已渲染的 cell 从 DOM 里抹掉
  // （模型里还在、画布空白）。主题切换很少见，直接重建画布最稳。
  if (graph.value) rebuildGraph()
}

/** 拉一次设置。失败就保持默认（全开）——设置读不到不该让整个库变成"什么都关着"。 */
async function loadSettings() {
  try { settings.value = await fetchSettings() } catch { /* 保持默认 */ }
}

/** 改设置：先落盘再按新值刷新受影响的东西。
 *
 * **不做乐观更新**：这几个开关会改变教练的系统提示词（服务端拼），
 * 界面先变、盘上没落的话，你以为关了、教练还在催，比慢半拍难受得多。
 */
async function saveSettings(patch) {
  try {
    settings.value = await putSettings(patch)
  } catch (err) {
    setBanner(`设置没存上：${err.message}`, 'error')
    return
  }
  await Promise.all([refreshDue(), refreshToday(), refreshOpenQuiz()])
  render()                                   // 到期金点要跟着一起消失 / 回来
  // 提示要说**这一次改了什么**：三个开关共用一句"复习已关掉"的话，
  // 关掉「教练会考我」时会看到"整套关了"，而今日分栏其实还在——提示自己就把人误导了
  setBanner(banner_of(patch), 'success')
}

function banner_of(patch) {
  if ('review_enabled' in patch) {
    return reviewOn.value ? '复习与出题已打开'
                          : '复习与出题整套已关掉：「今日」分栏里的到期与错题也收起了'
  }
  if ('review_in_chat' in patch) {
    return settings.value.review_in_chat ? '教练会在对话里考你了'
                                         : '教练不再考你、不再催欠账；「今日」分栏里照常能复习'
  }
  return '设置已保存'
}

function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
  applyThemeNow()
}

function fit() {
  ready.value = true   // 点按钮属于用户操作，之后的视口值该落盘
  fitStable()
}

/**
 * 贴合到全部内容。
 *
 * 按 layout 里所有元素的框来贴，而不是按画布上当前画出来的东西：缩放会改变 LOD 折叠、
 * 折叠又会改变画布上的内容大小，跟着当前内容贴会来回打架（实测会停在放大后的局部）。
 * layout 的框跟折叠无关，一步到位。
 */
function fitStable() {
  const g = graph.value
  if (mode.value === 'history') {
    g.zoomToFit({ padding: 40, maxScale: 1, minScale: 0.35 })
    zoom.value = g.zoom()
    return
  }
  const box = contentBBox(layoutDoc.value)
  if (!box) {
    g.zoomToFit({ padding: 60, maxScale: 1 })
    zoom.value = g.zoom()
    return
  }
  // 自己算缩放而不是用 zoomToFit / zoomToRect：这两个都跟着画布上"当前画出来的东西"走，
  // 而 LOD 折叠会让那个东西忽大忽小（簇卡片还会按 1/zoom 放大），贴合结果不可预测。
  g.resize()      // 刚打开时工具条 / 抽屉还没排完，容器量出来会偏矮，先重新量一次
  const el = g.container
  const w = Math.max((el.clientWidth || 1200) - 100, 200)
  const h = Math.max((el.clientHeight || 800) - 100, 200)
  const z = Math.max(0.05, Math.min(1, Math.min(w / box.width, h / box.height)))
  g.zoomTo(z)
  g.centerPoint(box.x + box.width / 2, box.y + box.height / 2)
  zoom.value = g.zoom()
}

async function reload() {
  await patcher.value.flush()
  await load()
}


watch(visible, () => {
  try {
    localStorage.setItem(FAMILY_KEY, JSON.stringify({ ...visible }))
  } catch { /* 隐私模式存不了就只在本次会话生效 */ }
}, { deep: true })

// 抽屉开合会改变画布可用宽度，X6 的 autoResize 靠 ResizeObserver，这里再补一次
watch([panel, inspectorOpen], () => {
  requestAnimationFrame(() => graph.value?.resize())
})

onMounted(async () => {
  setTheme(theme.value)
  document.documentElement.dataset.theme = theme.value
  const g = createGraph(canvasEl.value)
  graph.value = g
  setSnap(g, snap.value)
  patcher.value = createPatcher({
    getLayoutName: layoutName,
    getRevision: () => revision.value,
    setRevision: (r) => { revision.value = r },
    onStatus: (s, payload) => {
      status.value = s
      if (s === 'saved' && dirtyBefore) {
        if (history.record(dirtyBefore, clone(layoutDoc.value), '拖动')) histVer.value++
        dirtyBefore = null
      }
      if (s === 'error') setBanner(`保存失败：${payload?.body?.detail || payload?.message || '未知错误'}`, 'error')
      if (s === 'saved' && lastKind === 'error') setBanner('')
    },
    onConflict: (fresh) => {
      layoutDoc.value = fresh.layout
      setBanner('layout 被其他窗口改过，已合并到最新 revision 后重试')
    },
  })
  bindEvents(g)
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('error', onGlobalError)
  window.addEventListener('unhandledrejection', onGlobalError)
  // 本地个人工具：暴露一个调试句柄，排查渲染问题时能在控制台直接看模型
  window.__kg = {
    graph: g,
    get layout() { return layoutDoc.value },
    get index() { return indexDoc.value },
    // 排查"为什么没高亮 / 为什么画布是空的"这类问题时，没有这几个就只能靠猜
    get mode() { return mode.value },
    get project() { return currentProject.value },
    get projectIds() { return [...projectIds.value] },
    get progress() { return plansProgress.value },
    get indexRevision() { return indexRevision.value },
    write: writeChanges,          // 排查"写回为什么失败"时，能在控制台直接打一发
    // 历史视图：布局是一次算定的，游标停在哪由 upto 决定——排查"点该亮没亮"只看这两个
    get histPlan() { return histPlan.value },
    get histActive() { return histActiveIds() },
    // 往对话里塞一条假回复：图文渲染（Markdown / mermaid）不调模型也能验。
    // 第二个参数塞变更卡（形状同 SSE 的 card 事件）：卡上改摘要 / 正文 → 重算 → 写入这条链路
    // 除了模型那一步全是真的（/api/changes + 落盘），所以也能不调模型就验。
    fakeReply(text, cards = []) {
      chatLog.value = [...chatLog.value,
                       { role: 'assistant', content: text, trace: [],
                         cards: cards.map((c) => ({ ...c, applied: false })) }]
    },
    // 往最后那条回复上接一段，形状和流式增量一模一样。**「图不该被重画」只能这么验**：
    // 生产构建里 `__vueParentComponent` 是不挂的，从 DOM 摸不到这条消息。
    growReply(text) {
      const last = chatLog.value[chatLog.value.length - 1]
      if (last) last.content += text
    },
  }
  try {
    await load()
    // 开场就把这两份拉回来：双态要项目进度，晨间简报要今日清单。
    // 都是本地接口、都不调 LLM，不 await 是为了不挡首屏。
    // 设置要先回来：今日清单、到期角标、简报都按它决定要不要拉
    await loadSettings()
    refreshPlans()
    refreshToday()
    refreshOpenQuiz()
    // 从 3D 总览跳回来时带着 ?focus=<id>：定位到那个节点，然后把参数抹掉
    const wanted = new URLSearchParams(window.location.search).get('focus')
    if (wanted) {
      ready.value = true
      gotoNode(wanted)
      window.history.replaceState({}, '', window.location.pathname)
    }
  } catch (err) {
    setBanner(`加载失败：${err.message}。确认本地服务已启动（server/dev.sh）`, 'error')
  }
})

function onGlobalError(e) {
  reportCrash(e?.error || e?.reason || e?.message)
}

onBeforeUnmount(() => {
  stopPlay()
  toastTimers.forEach((t) => clearTimeout(t))
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('error', onGlobalError)
  window.removeEventListener('unhandledrejection', onGlobalError)
  patcher.value?.flush()
})
</script>

<template>
  <div class="app">
    <AppHeader ref="headerEl" :mode="mode" :hits="searchHits" :status="status" :status-text="statusText"
               :theme="theme" :busy="placing"
               :projects="plansDoc?.projects || {}" :project="currentProject"
               @switch-mode="switchMode" @switch-project="switchProject" @settings="settingsOn = true"
               @search="search = $event" @goto="gotoNode"
               @toggle-theme="toggleTheme" @reload="reload" @rebuild="rebuildGraph('手动重建')"
               @help="showHelp = true" />

    <div class="workbench">
      <SettingsDialog v-if="settingsOn" :settings="settings" :snap="snap" :avoid-nodes="avoidNodes"
                      :auto-lod="autoLod" :aggregate="aggregate" :show-map="showMap" :theme="theme"
                      @close="settingsOn = false" @set="saveSettings"
                      @toggle-snap="toggleSnap" @toggle-avoid="toggleAvoid" @toggle-map="toggleMap"
                      @toggle-lod="autoLod = !autoLod; render()"
                      @toggle-aggregate="aggregate = !aggregate; expanded = new Set(); render()"
                      @toggle-theme="toggleTheme" />

      <MorningBrief v-if="briefOn" :today="todayList" @close="briefOn = false"
                    @start="briefStart" @quiz="briefOn = false; startQuiz($event)" />

      <ActivityBar :active="panel" :mode="mode" :project="currentProject"
                   :inbox="inboxCount" :due="dueIds.size" :theme="theme"
                   @select="openPanel" @toggle-theme="toggleTheme" />

      <InboxTray v-if="panel === 'inbox'" class="inbox" :items="inboxItems" :busy="placing"
                 @place="placeOne" @place-all="placeAll" @place-new-group="placeWithNewGroup"
                 @suggest="suggestForInbox" @close="panel = ''" />
      <ImportPanel v-else-if="panel === 'import'" class="study" :fields="fieldNames" :project="currentProject"
                   :project-name="plansDoc?.projects?.[currentProject]?.name || ''" :project-field="projectField()"
                   :revision="indexDoc?.revision || 0" :busy="status === 'saving'"
                   @applied="onImported" @goto="gotoNode" @close="panel = ''" />
      <ProjectsPanel v-else-if="panel === 'plans'" class="study" :doc="plansDoc" :progress="plansProgress"
                     :schedules="plansSchedules" :fields="fieldNames" :project="currentProject"
                     :busy="plansBusy" :proposal="planProposal" :proposing="planProposing"
                     @save="savePlans" @goto="gotoNode" @build="buildPoint" @propose="proposePlan"
                     @quiz="startQuiz" @switch="switchProject" @refresh="refreshPlans"
                     @close="panel = ''" />
      <StudyPanel v-else-if="panel === 'study'" class="study" :today="todayList" :busy="quizBusy"
                  :open-quiz="openQuiz" @resume-quiz="resumeQuiz" @drop-quiz="discardQuiz"
                  @goto="gotoNode" @quiz="startQuiz($event.id ? [$event.id] : $event)"
                  @build="buildPoint" @write="writeBody" @place="placeFromToday"
                  @link="linkFromToday"
                  @plans="panel = 'plans'; refreshPlans()" @global="switchProject('')"
                  @refresh="refreshToday" @close="panel = ''" />
      <StatsPanel v-else-if="panel === 'stats'" class="study" :index="indexDoc" :chains="lineageChains"
                  @goto="gotoNode" @close="panel = ''" />
      <CalendarPanel v-else-if="panel === 'calendar'" class="study" :data="calendar"
                     @goto="gotoNode" @refresh="refreshCalendar" @close="panel = ''" />
      <DigestPanel v-else-if="panel === 'digest'" :busy="status === 'saving'" @regroup="regroupDrafts" class="digest" :digest="digest"
                   @goto="gotoNode" @refresh="refreshDigest" @merge="openMerge" @link="linkFromDigest"
                   @suggest="suggestFromDigest" @years="openYears" @misplace="fixMisplaced"
                   @close="panel = ''" />
      <ImagePicker v-else-if="panel === 'assets'" class="picker" @pick="addImage" @add-note="addNote"
                   @error="setBanner($event, 'error')" @close="panel = ''" />
      <TimelinePanel v-else-if="panel === 'timeline'" class="timeline" :options="timelineChoices"
                     :selected="timelines" :layered="layeredHint"
                     :families="hist" :trunk="hist.trunk" :chain="histChain"
                     @toggle="toggleTimeline" @toggle-family="toggleHistFamily"
                     @toggle-trunk="hist.trunk = !hist.trunk; renderHistory({ view: 'fit' })"
                     @select-all="timelines = []; renderHistory({ view: 'fit' })" @close="panel = ''" />

      <div class="stage stage-split" :class="{ solo: mode === 'chat' && !graphPane }">
        <!-- 对话模式：左边全屏对话，右边留给画布（可收起）。
             画布**不用 v-if 销毁**——重建 X6 既慢又会丢掉视口和选中态，只是让出宽度。 -->
        <ChatView v-if="mode === 'chat'" :messages="chatLog" :busy="chatBusy"
                  :sessions="chatSessions" :session="chatSession" :focus="chatFocus"
                  :stance="chatStance" @stance="setStance"
                  :graph-open="graphPane" :tidied="chatTidied" :fresh="chatFresh"
                  @send="sendChat" @stop="stopChat" @apply="applyChatCard" @preview="previewChatCard"
                  @apply-project="applyProjectCard" @apply-points="applyPointsCard"
                  @apply-list-edit="applyListEditCard" @goto="gotoNode"
                  @new-session="newChatSession" @pick-session="pickChatSession" @rename-session="renameSession"
                  @drop-focus="chatFocus = null" @toggle-graph="toggleGraphPane"
                  @close="switchMode(currentProject ? 'project' : 'structure')" />

        <div v-show="mode !== 'chat' || graphPane" ref="canvasEl" class="canvas"
             @dragover.prevent @drop="onCanvasDrop" />

        <!-- 项目画布是工作台，全局图才是成品图：成熟了再并进主图 -->
        <div v-if="mode === 'project' && currentProject" class="sync-bar">
          <span class="dim">项目画布 · 只有这个项目的点，还没建的画成幽灵（点一下就去建）</span>
          <button v-if="missingPoints.length" class="btn tiny" title="清单里有、画布上没有的点，停到右下角当待学区"
                  @click="parkMissing">
            <Icon name="inbox" :size="13" />{{ missingPoints.length }} 个点还没上画布，放到右下角
          </button>
          <button class="btn primary tiny" :disabled="syncing" title="把已建成、还没上全局图的点放过去（落草稿；坐标不搬）"
                  @click="syncToGlobal">
            <Icon name="arrowRight" :size="13" />{{ syncing ? '同步中…' : '同步到全局' }}
          </button>
        </div>

        <CanvasTools v-if="mode === 'structure' || mode === 'project'" :visible="visible" :shown-families="shownFamilies"
                     :collapsed="collapsedIds.size" :aggregate="aggregate" :auto-lod="autoLod" :snap="snap" :avoid-nodes="avoidNodes"
                     :borrow="borrowOn" :project="mode === 'project' && !!currentProject"
                     :layouts="mode === 'project' ? {} : LAYOUTS" :can-undo="canUndo" :can-redo="canRedo" :locked="!!preview"
                     @toggle-family="visible[$event] = !visible[$event]; render()"
                     @toggle-aggregate="aggregate = !aggregate; expanded = new Set(); render()"
                     @toggle-lod="autoLod = !autoLod; render()"
                     @toggle-snap="toggleSnap" @toggle-avoid="toggleAvoid" @toggle-borrow="toggleBorrow"
                     @pick-layout="runLayout" @add-note="addNote" @add-image="panel = 'assets'"
                     @undo="undo" @redo="redo" />

        <ZoomBar :zoom="zoom" :map="showMap && mode !== 'history'" @zoom-in="stepZoom(1.25)"
                 @zoom-out="stepZoom(0.8)" @reset="resetZoom" @fit="fit" @toggle-map="toggleMap" />

        <HistoryPlayer v-if="mode === 'history'" :playing="isPlaying" :upto="hist.upto" :range="yearRange"
                       :speed="speed" @speed="setSpeed"
                       :compact="hist.compact" :validity="hist.validity"
                       :active="histActiveCount" :total="histPlan?.placed.size || 0"
                       @toggle-play="togglePlay" @set-upto="setUpto"
                       :touring="tour.on"
                       @toggle-compact="hist.compact = !hist.compact; renderHistory({ view: 'fit' })"
                       @toggle-validity="hist.validity = !hist.validity; paintTime()"
                       @tour="tour.on ? stopTour() : startTour()" />
        <TourPanel v-if="mode === 'history' && tour.on" :chain="tourChain" :index="tour.i"
                   :stop="tourStop" :via="tourVia" :playing="tour.auto"
                   @go="tourGo" @prev="tourGo(tour.i - 1)" @next="tourGo(tour.i + 1)"
                   @toggle="toggleTourAuto" @close="stopTour(); setBanner('')" />

        <!-- 换布局是"未落盘的草稿态"，用一条醒目的浮条把去留摆在画布正上方 -->
        <div v-if="preview" class="float banner-bar">
          <Icon name="grid" :size="15" />
          <span>预览中的布局还没写盘</span>
          <button class="btn primary" @click="applyPreview"><Icon name="check" :size="14" />应用</button>
          <button class="btn" @click="cancelPreview">取消</button>
        </div>
        <div v-else-if="neighbor" class="float context">
          <div class="crumbs">
            <Icon name="eye" :size="13" />
            <span class="cur">只看「{{ neighborName }}」的邻居</span>
          </div>
          <button class="icon-btn" title="退出（Esc）" @click="toggleNeighbor(neighbor)">
            <Icon name="x" :size="15" />
          </button>
        </div>
        <div v-else-if="focusName" class="float context">
          <div class="crumbs">
            <span class="link" @click="exitGroup">全景</span>
            <Icon name="chevronRight" :size="12" class="sep-icon" />
            <span class="cur">{{ focusName }}</span>
          </div>
          <button class="icon-btn" title="返回全景（Esc）" @click="exitGroup"><Icon name="x" :size="15" /></button>
        </div>

        <GroupBar v-if="groupBarAt && activeGroupBox && mode === 'structure'"
                  :name="activeGroupBox.name" :at="groupBarAt" :doc="activeGroupBox.doc || null"
                  :count="activeGroupCount" :folded="activeFolded"
                  @open-doc="gotoNode(activeGroupBox.doc)" @write="writeDoc(activeGroupBox.doc)"
                  @relate="relating = describe(activeGroupBox.doc)"
                  @new-doc="openDocDialog(activeGroup, null)"
                  @new-node="openNodeDialog({ x: activeGroupBox.x + 40, y: activeGroupBox.y + 64 }, activeGroup)"
                  @fold="setPinned(activeGroup, 'collapsed')"
                  @unfold="setPinned(activeGroup, 'expanded')"
                  @close="activeGroup = null; groupBarAt = null" />

        <MiniMap v-if="showMap && mode === 'structure' && layoutDoc" :layout="layoutDoc" :view="viewBox"
                 :focus="focusGroup" :collapsed="collapsedIds" @jump="jumpTo" @close="toggleMap" />

        <ToastHost :items="toasts" @dismiss="dismissToast" />
      </div>

      <Inspector v-if="inspectorOpen" :selected="selected" :detail="detail" :relation-types="relationTypes"
                 :all-node-ids="allNodeIds" :pending="pending" :change-preview="changePreview"
                 :is-due="!!selected && dueIds.has(selected.id)"
                 :suggestions="suggestions" :suggesting="suggesting"
                 @close="inspectorHidden = true" @goto="gotoNode" @edit-desc="editDesc" @set-layer="setLayer" @set-year="setYear" @add-ref="addRef"
                 @review="markReviewed($event.id, $event.grade)" @quiz="startQuiz"
                 @rename="openRename"
                 @finalize="finalize" @retype-edge="retypeEdge"
                 @remove-edge="removeEdge" @add-edge="addEdgeDraft" @drop-change="dropChange"
                 @preview-changes="previewChanges" @apply-changes="applyChanges"
                 @clear-changes="pending = []; changePreview = null"
                 @suggest="fetchSuggestions(selected?.id)" @dismiss-suggestion="dismissSuggestion"
                 :write-nonce="writeNonce" @save-body="saveBody" />
    </div>

    <StatusBar :mode="mode" :stats="stats" :edges-shown="edgesShown" :agg-shown="aggShown"
               :hist-plan="histPlan" :range="yearRange" :index-revision="indexRevision" :revision="revision"
               :focus-name="focusName" :problems="problems" :usage="usage" :llm-busy="llmBusy"
               @exit-focus="exitGroup" @show-problems="showProblems" @help="showHelp = true"
               @show-usage="showUsage = true; refreshUsage()" />

    <ContextMenu v-if="ctx" v-bind="ctx" @pick="onMenuPick" @close="ctx = null" />

    <NodeDialog v-if="creating" :fields="fieldNames" :dirs="nodeDirs" :defaults="creating"
                :taken="takenIds" @create="createNode" @close="creating = null" />

    <RelationDialog v-if="relating" :source="relating" :families="relationTypes"
                    :nodes="indexDoc?.nodes || []" :placed="placedIds" :linked="linkedOf"
                    :preset="relatePreset"
                    @create="createRelation" @close="relating = null" />

    <QuizDialog v-if="quiz" :questions="quiz.questions" :names="nodeNames" :diagnosis="quizDiag"
                :busy="quizBusy" @diagnose="diagnoseQuiz" @submit="submitQuiz"
                @goto="gotoNode($event); quiz = null" @close="quiz = null; quizDiag = null" />

    <YearDialog v-if="yearsOpen" :proposal="yearProposal" :busy="yearsBusy"
                @apply="applyYears" @goto="gotoNode" @close="yearsOpen = false" />

    <MergeDialog v-if="merging" :pair="merging" :impact="mergeImpact" :busy="mergeBusy"
                 @preview="previewMerge" @apply="applyMerge" @swap="swapMerge"
                 @goto="gotoNode($event); merging = null" @close="merging = null; mergeImpact = null" />

    <RenameDialog v-if="renaming" :source="renaming" :taken="takenIds" :impact="renameImpact"
                  :busy="renameBusy" @preview="previewRename" @apply="applyRename"
                  @close="renaming = null; renameImpact = null" />

    <UsageDialog v-if="showUsage && usage" :usage="usage" @refresh="refreshUsage"
                 @close="showUsage = false" />

    <HelpDialog v-if="showHelp" :mode="mode" @close="showHelp = false" />
  </div>
</template>
