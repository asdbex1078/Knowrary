<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import {
  fetchDigest, fetchDue, fetchHealth, fetchIndex, fetchInbox, fetchLayout, fetchNode,
  patchLayout, postChanges, postPlace, postReview,
} from './api'
import AppHeader from './components/AppHeader.vue'
import ActivityBar from './components/ActivityBar.vue'
import CanvasTools from './components/CanvasTools.vue'
import ZoomBar from './components/ZoomBar.vue'
import HistoryPlayer from './components/HistoryPlayer.vue'
import StatusBar from './components/StatusBar.vue'
import Inspector from './components/Inspector.vue'
import HelpDialog from './components/HelpDialog.vue'
import ContextMenu from './components/ContextMenu.vue'
import RelationDialog from './components/RelationDialog.vue'
import NodeDialog from './components/NodeDialog.vue'
import GroupBar from './components/GroupBar.vue'
import MiniMap from './components/MiniMap.vue'
import ToastHost from './ui/ToastHost.vue'
import Icon from './ui/Icon.vue'
import InboxTray from './panels/InboxTray.vue'
import DigestPanel from './panels/DigestPanel.vue'
import ImagePicker from './panels/ImagePicker.vue'
import TimelinePanel from './panels/TimelinePanel.vue'
import { clone, createHistory, diffPatch, isEmptyPatch } from './canvas/history'
import { createPatcher } from './canvas/patcher'
import { ancestors as groupAncestors, computeCollapsed } from './canvas/lod'
import {
  LABEL_ZOOM, applyEdgeLabels, applyViewport, buildCells, buildHistoryCells, contentBBox, createGraph,
  currentViewport, highlightEdges, mount, movedPositions,
} from './canvas/render'
import { timelineOptions } from './canvas/timeline'
import { communityLayout, compareWithGroups } from './canvas/communities'
import { mindmapLayout, toPatch } from './canvas/layouts'
import { FAMILIES, setTheme } from './canvas/shapes'

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
const has3d = ref(false)          // 服务端有 web3d 构建产物时才显示 3D 入口
const inboxCount = ref(0)
const inboxItems = shallowRef([])        // GET /api/inbox：索引里有、画布上还没有的节点
const digest = shallowRef(null)          // GET /api/digest：欠账清单
const dueIds = shallowRef(new Set())     // 今天该复习的节点，画布上点一个金色小圆点
const placing = ref(false)
// 历史视图（阶段 6）：X 轴锁在年份上，坐标不持久化，进来一次算一次
const mode = ref('structure')
const hist = reactive({ compact: false, validity: false, upto: null, 演化: true, 依赖: false, 对照: false })
const histPlan = shallowRef(null)
const timelines = ref([])          // 选中的 layout 分组 id（空 = 全部）
let playing = null
const isPlaying = ref(false)
const autoLod = ref(true)                // 缩小自动折叠成簇卡片（设计文档 3.7）
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
const labelsOn = ref(false)
const zoom = ref(1)                      // 给右下角缩放条读数用，随 scale 事件更新
// 只有用户真的操作过画布才允许落盘：既避免"打开页面就涨 revision"，
// 也不依赖 requestAnimationFrame（后台标签页 / 无头浏览器里 rAF 不触发）
const ready = ref(false)
let applyingViewport = false   // 程序化设置视口期间不落盘，否则切族/展开都白涨一个 revision

// —— 右键菜单 / 建立关系 / 小地图 / 只看邻居 ——
const ctx = ref(null)          // 菜单浮层的 props：{ x, y, title, subtitle, items }
let ctxTarget = null           // 菜单指着谁：{ kind, id, at }，不进 props（会漏成 DOM 属性）
const relating = shallowRef(null)        // 建立关系对话框的源节点
const creating = shallowRef(null)        // 新建知识点对话框：{ at, group }
const activeGroup = ref(null)            // 工具条正指着哪个域
const groupBarAt = ref(null)             // 工具条的屏幕坐标，跟着缩放平移重算
const writeNonce = ref(0)                // ++ 一次 = 让检查器展开正文编辑框
const neighbor = ref(null)               // 只看这个节点和它的直接邻居
const showMap = ref(localStorage.getItem('knowrary-map') !== '0')
const viewBox = ref({ cx: 0, cy: 0, w: 0, h: 0 })   // 当前视口（图坐标），小地图用

// —— 界面状态：左侧工具窗口、右侧检查器、浮层提示、帮助 ——
const panel = ref('')                    // '' | inbox | digest | assets | timeline
const inspectorHidden = ref(false)
const showHelp = ref(false)
const problems = ref([])                 // 加载时发现的待处理项，挂在状态栏上

const inspectorOpen = computed(() =>
  !inspectorHidden.value && (!!selected.value || pending.value.length > 0))

function openPanel(id) {
  panel.value = panel.value === id ? '' : id
  if (panel.value === 'digest' && !digest.value) refreshDigest()
  if (panel.value === 'inbox') refreshInbox()
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
  const [index, layout] = await Promise.all([fetchIndex(), fetchLayout()])
  fetchHealth().then((h) => { has3d.value = !!h.web3d }).catch(() => {})
  indexDoc.value = index
  layoutDoc.value = layout.layout
  revision.value = layout.layout.revision
  indexRevision.value = index.revision
  Object.assign(stats, { nodes: index.stats.nodes, edges: index.stats.edges, stubs: index.stats.stubs })
  inboxCount.value = index.nodes.filter((n) => !n.virtual && !layout.layout.nodes[n.id]).length
  refreshInbox()
  refreshDue()
  ready.value = false
  const refit = !storedViewportUsable()
  render({ view: refit ? 'fit' : 'stored' })
  if (refit) {
    applyingViewport = true      // 自动贴合不算用户操作，别把视口写回去
    fitStable()
    applyingViewport = false
  }
  reportProblems(index, layout, refit)
}

// view: 'stored' 用 layout 里存的视口（首次加载）/ 'fit' 适应内容（换布局后）/ 'keep' 保持当前（切族、展开聚合束）
function render({ view = 'keep' } = {}) {
  if (mode.value === 'history') return renderHistory({ view })
  const g = graph.value
  collapsedIds.value = computeCollapsed(layoutDoc.value, g.zoom(),
    { auto: autoLod.value, focus: focusGroup.value })
  const cells = buildCells(indexDoc.value, layoutDoc.value, {
    families: visibleFamilies(), showLabels: labelsOn.value,
    aggregate: aggregate.value, expanded: expanded.value, collapsed: collapsedIds.value, zoom: g.zoom(),
    due: dueIds.value, only: neighborSet.value,
  })
  edgesShown.value = cells.edges.filter((e) => e.data.kind === 'edge').length
  aggShown.value = cells.edges.length - edgesShown.value
  const keep = view === 'keep' ? { zoom: g.zoom(), translate: g.translate() } : null
  applyingViewport = true
  mount(g, cells)
  if (keep) {
    g.zoomTo(keep.zoom)
    g.translate(keep.translate.tx, keep.translate.ty)
  } else if (view === 'fit') {
    g.zoomToFit({ padding: 60, maxScale: 1 })
  } else {
    applyViewport(g, layoutDoc.value.viewport)
  }
  applyingViewport = false
  zoom.value = g.zoom()
  syncView()
}

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
function boxOf(ids, pad = 120) {
  const boxes = ids.map((id) => layoutDoc.value?.nodes?.[id]).filter(Boolean)
  if (!boxes.length) return null
  const x0 = Math.min(...boxes.map((b) => b.x)) - pad
  const y0 = Math.min(...boxes.map((b) => b.y)) - pad
  const x1 = Math.max(...boxes.map((b) => b.x + (b.w || 160))) + pad
  const y1 = Math.max(...boxes.map((b) => b.y + (b.h || 60))) + pad
  return { x: x0, y: y0, w: Math.max(x1 - x0, 1), h: Math.max(y1 - y0, 1) }
}

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

// —— 阶段 6：历史视图 ——

const histFamilies = () => new Set(['演化', '依赖', '对照'].filter((f) => hist[f]))
const yearRange = computed(() => {
  const years = (indexDoc.value?.nodes || []).filter((n) => typeof n.year === 'number').map((n) => n.year)
  return years.length ? [Math.min(...years), Math.max(...years)] : [0, 0]
})
const timelineChoices = computed(() => timelineOptions(layoutDoc.value))

function renderHistory({ view = 'fit' } = {}) {
  const g = graph.value
  const cells = buildHistoryCells(indexDoc.value, layoutDoc.value, {
    timelines: timelines.value, families: histFamilies(), compact: hist.compact, upto: hist.upto,
    validity: hist.validity,
  })
  histPlan.value = cells.plan
  applyingViewport = true
  // 先定视口再建 cell：时间轴的宽高比极端，先按算好的框定缩放，mount 出来就是完整一屏
  if (view !== 'keep') {
    g.zoomToRect({ x: -80, y: 0, width: cells.plan.width + 160, height: cells.plan.height + 60 },
                 { maxScale: 1, minScale: 0.35 })
  }
  mount(g, cells)
  applyingViewport = false
  zoom.value = g.zoom()
  const d = cells.plan.diagnostics
  const parts = [`${cells.plan.placed.size} 个有 year 的节点 · ${cells.edges.length} 条边`]
  if (d.noYear) parts.push(`${d.noYear} 个节点没有 year，不进历史图`)
  if (d.missingYear.length) parts.push(`${d.missingYear.length} 条演化边缺年份（${d.missingYear[0]} …）`)
  setBanner(parts.join('；'), d.missingYear.length ? 'error' : '')
}

async function switchMode(next) {
  if (mode.value === next) return
  stopPlay()
  await patcher.value.flush()           // 离开结构视图前先把手上的改动落盘
  mode.value = next
  markHistoryContainer(next)
  // 左侧工具窗口是分模式的：切过去之后原来开着的那个可能不适用了
  if ((next === 'history' && panel.value !== 'digest') || (next === 'structure' && panel.value === 'timeline')) {
    panel.value = ''
  }
  if (next === 'structure') {
    expanded.value = new Set()
    render({ view: 'stored' })
    setBanner('')
  } else {
    renderHistory({ view: 'fit' })
  }
}

/** 历史视图给容器加个类名，节点的渐显动画只在这个模式下生效。 */
function markHistoryContainer(next) {
  graph.value?.container?.classList?.toggle('kg-history', next === 'history')
}

function setUpto(value) {
  hist.upto = value === '' || value === null ? null : Number(value)
  renderHistory({ view: 'keep' })
}

function togglePlay() {
  if (playing) return stopPlay()
  const [min, max] = yearRange.value
  if (hist.upto === null || hist.upto >= max) hist.upto = min
  isPlaying.value = true
  playing = setInterval(() => {
    if (hist.upto === null || hist.upto >= max) return stopPlay()
    hist.upto += 1
    renderHistory({ view: 'keep' })
  }, 220)
}

function stopPlay() {
  if (playing) clearInterval(playing)
  playing = null
  isPlaying.value = false
}

function toggleTimeline(id) {
  timelines.value = timelines.value.includes(id)
    ? timelines.value.filter((x) => x !== id)
    : [...timelines.value, id]
  renderHistory({ view: 'fit' })
}

function toggleHistFamily(f) {
  hist[f] = !hist[f]
  renderHistory({ view: 'keep' })
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
// 写盘总闸：预览布局时画布是"草稿"；历史视图是浏览视图，坐标本来就不持久化
const writable = () => !preview.value && mode.value === 'structure'

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
let lastBucket = 0

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

  g.on('node:moved', safe(({ node }) => {
    if (!ready.value) return
    if (node.id.startsWith('note:') || node.id.startsWith('ref:')) {
      const pos = node.position()
      moveDecoration(node.id, Math.round(pos.x), Math.round(pos.y))
      return
    }
    if (node.shape === 'kg-cluster') return moveCluster(node)
    for (const p of movedPositions(node)) queueIfChanged(p.kind, p.id, { x: p.x, y: p.y })
  }))
  g.on('node:change:parent', safe(({ node, current }) => {
    if (!ready.value || node.shape !== 'kg-node') return
    const pos = node.position()
    queueIfChanged('node', node.id, { group: current || null, x: Math.round(pos.x), y: Math.round(pos.y) })
  }))
  g.on('node:selected', safe(({ node }) => {
    selected.value = describe(node.id)
    inspectorHidden.value = false
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
  g.on('node:click', safe(({ node }) => {
    if (node.shape === 'kg-cluster') enterGroup(node.id)
    else if (node.shape === 'kg-group') setActiveGroup(node.id)
    if (node.shape === 'kg-ref') gotoNode(node.getData()?.target)
  }))
  g.on('node:dblclick', safe(({ node }) => {
    if (node.shape === 'kg-note') editNote(node.id.slice(5))
    else if (node.shape === 'kg-group') exitGroup()   // 双击域的空白处退回全景
  }))
  g.on('node:resized', safe(({ node }) => {
    if (!ready.value || node.shape !== 'kg-image') return
    const { width, height } = node.size()
    const pos = node.position()
    saveList('img', (item) => (item.id === node.id.slice(4)
      ? { ...item, x: Math.round(pos.x), y: Math.round(pos.y), w: Math.round(width), h: Math.round(height) }
      : item))
  }))
  // 点聚合边展开这对分组之间的明细，再点收起；点普通边则挂上拐点手柄
  g.on('edge:click', safe(({ edge }) => {
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

function saveViewport() {
  if (!ready.value || applyingViewport || !writable()) return
  patcher.value.queueViewport(currentViewport(graph.value))
}

// ---- 域工具条：浮在当前这个域的上沿 ----

const activeGroupBox = computed(() => (activeGroup.value
  ? layoutDoc.value?.groups?.[activeGroup.value] || null : null))

const activeGroupCount = computed(() => (activeGroup.value
  ? Object.values(layoutDoc.value?.nodes || {}).filter((n) => n.group === activeGroup.value).length : 0))

const activeFolded = computed(() => !!activeGroup.value && collapsedIds.value.has(activeGroup.value))

function setActiveGroup(gid) {
  activeGroup.value = layoutDoc.value?.groups?.[gid] ? gid : null
  placeGroupBar()
}

/**
 * 把工具条摆到域的上沿。
 *
 * 按"域与可视区的交集"算而不是只看左上角：放大之后域往往比屏幕还大，左上角早就在
 * 视口外，但你明明正看着它——那时也得给工具条。整块都挪出屏幕了才收起来。
 */
function placeGroupBar() {
  const g = graph.value
  const box = activeGroupBox.value
  if (!g || !box) { groupBarAt.value = null; return }
  const cell = g.getCellById(activeGroup.value)
  const at = cell ? cell.position() : box          // 折叠时簇卡片的位置才是它现在的样子
  const size = cell ? cell.size() : { width: box.w, height: box.h }
  const tl = g.localToClient(at.x, at.y)
  const br = g.localToClient(at.x + size.width, at.y + size.height)
  const view = g.container.getBoundingClientRect()
  const x0 = Math.max(tl.x, view.left + 8)
  const x1 = Math.min(br.x, view.right - 8)
  const y0 = Math.max(tl.y, view.top + 52)         // 至少给工具条自己留出一条的高度
  const y1 = Math.min(br.y, view.bottom - 8)
  groupBarAt.value = x1 > x0 && y1 > y0 ? { x: x0, y: y0 - 44 } : null
}

/** 当前视口换算成图坐标，小地图靠它画那个白框。 */
function syncView() {
  const g = graph.value
  if (!g) return
  const z = g.zoom() || 1
  const el = g.container
  const c = currentViewport(g)
  viewBox.value = { cx: c.cx, cy: c.cy, w: (el.clientWidth || 1200) / z, h: (el.clientHeight || 800) / z }
  placeGroupBar()
}

function onZoom() {
  const g = graph.value
  zoom.value = g.zoom()
  saveViewport()
  syncView()
  // 历史视图没有 LOD 折叠：缩放就只是看大看小，走结构视图那套会每滚一格就整图重建，
  // 还会把结构视图的折叠集合改掉（切回去时折叠状态就错了）
  if (mode.value === 'history') return
  const next = computeCollapsed(layoutDoc.value, g.zoom(),
    { auto: autoLod.value, focus: focusGroup.value })
  const changed = next.size !== collapsedIds.value.size
    || [...next].some((id) => !collapsedIds.value.has(id))
  // 折叠集合变了要重画；有簇卡片时缩放跨档（卡片尺寸分档跟随缩放）也要重画
  const bucket = Math.round(Math.min(3, Math.max(1, 1 / Math.max(g.zoom(), 0.05))) * 2)
  if (changed || (next.size && bucket !== lastBucket)) {
    lastBucket = bucket
    render()
    return
  }
  const shouldShow = g.zoom() > LABEL_ZOOM
  if (shouldShow !== labelsOn.value) {
    labelsOn.value = shouldShow
    applyEdgeLabels(g, indexDoc.value, shouldShow)
  }
}

/** 右下角缩放条：按固定倍率缩放，落点夹在 X6 的上下限内。 */
function stepZoom(factor) {
  const g = graph.value
  if (!g) return
  ready.value = true
  g.zoomTo(Math.min(3, Math.max(0.05, g.zoom() * factor)))
}

function resetZoom() {
  ready.value = true
  graph.value?.zoomTo(1)
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
  highlightEdges(graph.value, nodeId ? relatedEdgeIds(nodeId) : null)
}

// ---- 右键菜单：画布上每类元素一套动作 ----

function openMenu(kind, id, ev) {
  if (mode.value !== 'structure') return        // 历史视图是只读浏览视图
  ctxTarget = { kind, id, at: graph.value.clientToLocal(ev.clientX, ev.clientY) }
  const build = { node: nodeMenu, group: groupMenu, cluster: groupMenu, edge: edgeMenu,
                  deco: decoMenu, blank: blankMenu }[kind]
  ctx.value = { x: ev.clientX, y: ev.clientY, ...build(id, kind === 'cluster') }
}

function nodeMenu(id) {
  const meta = indexDoc.value?.nodes.find((n) => n.id === id)
  const place = layoutDoc.value?.nodes?.[id]
  const items = [{ id: 'relate', label: '建立关系…', icon: 'link', hint: '⌘L' },
                 { id: 'ref', label: '放引用卡', icon: 'bookmark' }]
  if (place?.state === 'draft') items.push({ id: 'finalize', label: '定稿', icon: 'check' })
  else if (place) items.push({ id: 'draft', label: '标记为草稿（待关联）', icon: 'pencil' })
  if (dueIds.value.has(id)) items.push({ id: 'review', label: '复习过了', icon: 'rotate' })
  items.push(
    { sep: true },
    { id: 'detail', label: '查看详情', icon: 'file' },
    { id: 'neighbor', label: neighbor.value === id ? '退出只看邻居' : '只看它的邻居',
      icon: 'eye', on: neighbor.value === id },
    { id: 'obsidian', label: '在 Obsidian 打开', icon: 'external' },
    { id: 'copy', label: `复制 [[${meta?.name || id}]]`, icon: 'copy' },
    ...(place?.group ? [{ id: 'as-doc', label: `设为「${layoutDoc.value.groups[place.group]?.name}」的总览`,
                          icon: 'bookmark', on: layoutDoc.value.groups[place.group]?.doc === id }] : []),
    { sep: true },
    { id: 'unplace', label: '移出画布（不删 md）', icon: 'trash', danger: true },
  )
  const deg = meta?.degree || 0
  return { title: meta?.name || id, subtitle: `${meta?.field || '未归类'} · ${deg} 条关系`, items }
}

function groupMenu(gid, folded) {
  const g = layoutDoc.value?.groups?.[gid]
  const count = Object.values(layoutDoc.value?.nodes || {}).filter((n) => n.group === gid).length
  const items = folded
    ? [{ id: 'enter', label: '展开这个域（放大进去）', icon: 'unfold' }]
    : [{ id: 'collapse', label: '折叠成簇卡片', icon: 'fold' },
       { id: 'enter', label: '放大到这个域', icon: 'target' }]
  const doc = g?.doc || null
  items.push(
    { sep: true },
    doc ? { id: 'open-doc', label: `打开总览「${doc}」`, icon: 'file' }
        : { id: 'new-doc', label: '给这个域加总览文档…', icon: 'file', hint: '写 md' },
    ...(doc ? [{ id: 'unbind-doc', label: '解除总览文档绑定', icon: 'x' }] : []),
    { sep: true },
    { id: 'pin-expanded', label: '一直展开（缩小也不折叠）', icon: 'pin', on: g?.pinned === 'expanded' },
    { id: 'pin-auto', label: '恢复自动折叠', icon: 'rotate', disabled: !g?.pinned },
    { sep: true },
    { id: 'new-node', label: '在这里新建知识点…', icon: 'plus', hint: '写 md' },
    { id: 'new-subgroup', label: '在这里新建子簇', icon: 'grid' },
    { id: 'rename', label: '重命名这个域', icon: 'pencil' },
  )
  if (focusGroup.value) items.push({ id: 'exit-focus', label: '返回全景', icon: 'arrowLeft', hint: 'Esc' })
  const pinned = g?.pinned === 'expanded' ? '已钉住展开' : g?.pinned === 'collapsed' ? '已钉住折叠' : '自动折叠'
  return { title: g?.name || gid,
           subtitle: `${count} 个知识点 · ${doc ? '有总览文档' : '没有总览文档'} · ${pinned}`, items }
}

function edgeMenu(id) {
  const e = indexDoc.value?.edges.find((x) => x.id === id)
  return {
    title: e ? `${e.source} → ${e.target}` : id,
    subtitle: e ? `${e.type}（${e.family}族）` : '',
    items: [
      { id: 'edge-delete', label: '删除这条关系（写回 md）', icon: 'trash', danger: true },
      { id: 'edge-clear', label: '清掉手工拐点', icon: 'rotate', disabled: !layoutDoc.value?.edges?.[id] },
      { sep: true },
      { id: 'edge-source', label: `打开 ${e?.source || ''}`, icon: 'file' },
      { id: 'edge-target', label: `打开 ${e?.target || ''}`, icon: 'file' },
    ],
  }
}

function decoMenu(cellId) {
  const [kind] = cellId.split(':')
  const label = { note: '便签', img: '贴图', ref: '引用卡' }[kind] || '元素'
  return { title: label, subtitle: '只存在 layout 里，不碰 md',
           items: [...(kind === 'note' ? [{ id: 'deco-edit', label: '编辑便签', icon: 'pencil' }] : []),
                   { id: 'deco-delete', label: `删除这张${label}`, icon: 'trash', danger: true }] }
}

function blankMenu() {
  return { title: '画布', subtitle: '右键落点就是新元素的位置', items: [
    { id: 'new-node', label: '新建知识点…', icon: 'plus', hint: '写 md' },
    { id: 'new-group', label: '新建簇（分组框）', icon: 'grid' },
    { id: 'note', label: '贴便签', icon: 'note' },
    { id: 'image', label: '贴图…', icon: 'image' },
    { sep: true },
    { id: 'fit', label: '适应窗口', icon: 'fit', hint: 'F' },
    { id: 'map', label: showMap.value ? '隐藏小地图' : '显示小地图', icon: 'map', on: showMap.value },
    { id: 'inbox', label: '打开 Inbox', icon: 'inbox', hint: 'I' },
  ] }
}

const MENU_ACTIONS = {
  relate: (id) => { relating.value = describe(id) },
  ref: (id) => { selected.value = describe(id); addRef() },
  finalize: (id) => finalize(id),
  draft: (id) => { queueIfChanged('node', id, { state: 'draft' }); render(); setBanner(`「${id}」已标记为草稿`) },
  review: (id) => markReviewed(id),
  detail: (id) => gotoNode(id),
  neighbor: (id) => toggleNeighbor(id),
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

function renameGroup(gid) {
  const cur = layoutDoc.value.groups[gid]
  if (!cur) return
  const name = window.prompt('这个域叫什么', cur.name || '')
  if (name === null || !name.trim()) return
  queueIfChanged('group', gid, { name: name.trim() })
  render()
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
    const res = await postChanges({ base_revision: indexRevision.value, dry_run: false, changes: [{
      type: 'create_node', source: form.id, path: `${form.dir}/${form.id}.md`,
      fields: { name: form.name, field: form.field, desc: form.desc,
                ...(form.year ? { year: form.year } : {}),
                learned: new Date().toISOString().slice(0, 10) },
    }] })
    await placeNew(form.id, spot)
    await reloadIndex()
    if (spot?.asDoc) bindDoc(spot.asDoc, form.id)
    status.value = 'saved'
    setBanner(`已新建 ${res.files[0]?.path || form.id}`, 'success')
    gotoNode(form.id)
    if (form.thenRelate) relating.value = describe(form.id)
  } catch (err) {
    status.value = 'error'
    setBanner(`新建失败：${err.body?.detail?.message || err.body?.detail || err.message}`, 'error')
  }
}

/** 新节点落到右键的那个点上；不在任何域里就交给服务端按领域找位置。 */
async function placeNew(id, spot) {
  await patcher.value.flush()
  const body = { base_revision: revision.value, ids: [id], state: 'draft' }
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

async function createRelation({ relation, target, swap }) {
  const src = relating.value
  if (!src) return
  const from = swap ? target : src.id
  const to = swap ? src.id : target
  relating.value = null
  status.value = 'saving'
  try {
    const res = await postChanges({ base_revision: indexRevision.value, dry_run: false,
                                    changes: [{ type: 'add_edge', source: from, relation, target: to }] })
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
    const res = await postChanges({ base_revision: indexRevision.value, dry_run: false,
                                    changes: [{ type: 'update_body', source: id, body: text }] })
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
    await postChanges({ base_revision: indexRevision.value, dry_run: false,
                        changes: [{ type: 'remove_edge', source: e.source, relation: e.type, target: e.target }] })
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
  if (selected.value) await loadDetail(selected.value.id)
}

// ---- 把目标"拉到眼前"：连完线自动飞过去，两端一起框进视口 ----

function flyToPair(a, b) {
  const box = boxOf([a, b], 140)
  if (!box) return
  const el = graph.value.container
  const z = Math.min(1.2, (el.clientWidth || 1200) / box.w, (el.clientHeight || 800) / box.h)
  flyTo({ cx: box.x + box.w / 2, cy: box.y + box.h / 2, zoom: z })
}

/**
 * 平滑飞过去。用 setTimeout 而不是 requestAnimationFrame：
 * 后台标签页和无头浏览器里 rAF 不触发，动画会卡在半路，视口再也存不回去（阶段 2 踩过）。
 */
function flyTo({ cx, cy, zoom: to }, ms = 420) {
  const g = graph.value
  const from = currentViewport(g)
  const end = Math.max(0.05, Math.min(3, to))
  const t0 = Date.now()
  const step = () => {
    const p = Math.min(1, (Date.now() - t0) / ms)
    const e = 1 - (1 - p) ** 3                    // easeOutCubic
    g.zoomTo(from.zoom + (end - from.zoom) * e)
    g.centerPoint(from.cx + (cx - from.cx) * e, from.cy + (cy - from.cy) * e)
    if (p < 1) { setTimeout(step, 16); return }
    zoom.value = g.zoom()
    syncView()
    saveViewport()
  }
  step()
}

/** 小地图上点一下 / 拖一把：视口中心跟着走。 */
function jumpTo({ x, y }) {
  ready.value = true
  graph.value?.centerPoint(x, y)
  syncView()
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

async function previewChanges() {
  if (!pending.value.length) return
  try {
    changePreview.value = await postChanges({
      base_revision: indexRevision.value, dry_run: true, changes: pending.value,
    })
    setBanner('')
  } catch (err) {
    changePreview.value = null
    setBanner(`变更被拒绝：${err.body?.detail?.message || err.body?.detail || err.message}`, 'error')
  }
}

async function applyChanges() {
  try {
    const res = await postChanges({
      base_revision: indexRevision.value, dry_run: false, changes: pending.value,
    })
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
  applyingViewport = true
  g.zoomTo(Math.max(g.zoom(), 0.8))
  g.centerPoint(place.x + 90, place.y + 30)
  applyingViewport = false
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
  try {
    dueIds.value = new Set((await fetchDue()).due.map((d) => d.id))
  } catch { dueIds.value = new Set() }
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
    setBanner(`${label}：放上 ${res.placed.length} 个草稿${grown}${skipped}`,
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
const placeAll = () => place({ ids: inboxItems.value.map((i) => i.id) }, '全部按建议放置')

/** 从 Inbox 拖到画布：落点由鼠标决定，落在哪个分组框里就归哪个组。 */
function onCanvasDrop(ev) {
  const id = ev.dataTransfer?.getData('text/knowrary-node')
  if (!id) return
  ev.preventDefault()
  const g = graph.value
  const p = g.clientToLocal(ev.clientX, ev.clientY)
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

async function markReviewed(id) {
  try {
    const res = await postReview(id)
    const next = new Set(dueIds.value)
    next.delete(id)
    dueIds.value = next
    render()
    if (panel.value === 'digest') refreshDigest()
    setBanner(`已记录第 ${res.reviews} 次复习，下次 ${res.next_due} 再来`, 'success')
  } catch (err) {
    setBanner(`记录复习失败：${err.message}`, 'error')
  }
}

/** 定稿：草稿位置确认下来，金色虚线框变成正常卡片。只改 layout，不碰 md。 */
function finalize(id) {
  if (layoutDoc.value.nodes[id]?.state !== 'draft') return
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
  applyingViewport = true
  g.zoomToRect({ x: box.x - 60, y: box.y - 60, width: box.w + 120, height: box.h + 120 }, { maxScale: 1.4 })
  applyingViewport = false
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
  applyingViewport = true
  if (panorama) {
    g.zoomTo(panorama.zoom)
    g.translate(panorama.translate.tx, panorama.translate.ty)
  } else {
    g.zoomToFit({ padding: 60, maxScale: 1 })
  }
  applyingViewport = false
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
  else if (e.key === '1') switchMode('structure')
  else if (e.key === '2') switchMode('history')
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

function open3d() {
  window.location.href = './3d/'
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
  patcher.value = createPatcher({
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
  window.__kg = { graph: g, get layout() { return layoutDoc.value }, get index() { return indexDoc.value } }
  try {
    await load()
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
               :theme="theme" :has3d="has3d" :busy="placing"
               @switch-mode="switchMode" @search="search = $event" @goto="gotoNode"
               @toggle-theme="toggleTheme" @reload="reload" @rebuild="rebuildGraph('手动重建')"
               @open-3d="open3d" @help="showHelp = true" />

    <div class="workbench">
      <ActivityBar :active="panel" :mode="mode" :inbox="inboxCount" :due="dueIds.size" :theme="theme"
                   @select="openPanel" @toggle-theme="toggleTheme" />

      <InboxTray v-if="panel === 'inbox'" class="inbox" :items="inboxItems" :busy="placing"
                 @place="placeOne" @place-all="placeAll" @close="panel = ''" />
      <DigestPanel v-else-if="panel === 'digest'" class="digest" :digest="digest"
                   @goto="gotoNode" @refresh="refreshDigest" @review="markReviewed" @close="panel = ''" />
      <ImagePicker v-else-if="panel === 'assets'" class="picker" @pick="addImage" @add-note="addNote"
                   @error="setBanner($event, 'error')" @close="panel = ''" />
      <TimelinePanel v-else-if="panel === 'timeline'" class="timeline" :options="timelineChoices" :selected="timelines"
                     :families="hist" @toggle="toggleTimeline" @toggle-family="toggleHistFamily"
                     @select-all="timelines = []; renderHistory({ view: 'fit' })" @close="panel = ''" />

      <div class="stage">
        <div ref="canvasEl" class="canvas" @dragover.prevent @drop="onCanvasDrop" />

        <CanvasTools v-if="mode === 'structure'" :visible="visible" :shown-families="shownFamilies"
                     :collapsed="collapsedIds.size" :aggregate="aggregate" :auto-lod="autoLod"
                     :layouts="LAYOUTS" :can-undo="canUndo" :can-redo="canRedo" :locked="!!preview"
                     @toggle-family="visible[$event] = !visible[$event]; render()"
                     @toggle-aggregate="aggregate = !aggregate; expanded = new Set(); render()"
                     @toggle-lod="autoLod = !autoLod; render()"
                     @pick-layout="runLayout" @add-note="addNote" @add-image="panel = 'assets'"
                     @undo="undo" @redo="redo" />

        <ZoomBar :zoom="zoom" :map="showMap && mode === 'structure'" @zoom-in="stepZoom(1.25)"
                 @zoom-out="stepZoom(0.8)" @reset="resetZoom" @fit="fit" @toggle-map="toggleMap" />

        <HistoryPlayer v-if="mode === 'history'" :playing="isPlaying" :upto="hist.upto" :range="yearRange"
                       :compact="hist.compact" :validity="hist.validity"
                       @toggle-play="togglePlay" @set-upto="setUpto"
                       @toggle-compact="hist.compact = !hist.compact; renderHistory({ view: 'fit' })"
                       @toggle-validity="hist.validity = !hist.validity; renderHistory({ view: 'keep' })" />

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
                 @close="inspectorHidden = true" @goto="gotoNode" @edit-desc="editDesc" @add-ref="addRef"
                 @review="markReviewed" @finalize="finalize" @retype-edge="retypeEdge"
                 @remove-edge="removeEdge" @add-edge="addEdgeDraft" @drop-change="dropChange"
                 @preview-changes="previewChanges" @apply-changes="applyChanges"
                 @clear-changes="pending = []; changePreview = null"
                 :write-nonce="writeNonce" @save-body="saveBody" />
    </div>

    <StatusBar :mode="mode" :stats="stats" :edges-shown="edgesShown" :agg-shown="aggShown"
               :hist-plan="histPlan" :range="yearRange" :index-revision="indexRevision" :revision="revision"
               :focus-name="focusName" :problems="problems"
               @exit-focus="exitGroup" @show-problems="showProblems" @help="showHelp = true" />

    <ContextMenu v-if="ctx" v-bind="ctx" @pick="onMenuPick" @close="ctx = null" />

    <NodeDialog v-if="creating" :fields="fieldNames" :dirs="nodeDirs" :defaults="creating"
                :taken="takenIds" @create="createNode" @close="creating = null" />

    <RelationDialog v-if="relating" :source="relating" :families="relationTypes"
                    :nodes="indexDoc?.nodes || []" :placed="placedIds" :linked="linkedOf"
                    @create="createRelation" @close="relating = null" />

    <HelpDialog v-if="showHelp" :mode="mode" @close="showHelp = false" />
  </div>
</template>
