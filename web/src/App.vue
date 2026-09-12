<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, shallowRef } from 'vue'
import {
  fetchDigest, fetchDue, fetchHealth, fetchIndex, fetchInbox, fetchLayout, fetchNode,
  patchLayout, postChanges, postPlace, postReview,
} from './api'
import InboxTray from './panels/InboxTray.vue'
import DigestPanel from './panels/DigestPanel.vue'
import ImagePicker from './panels/ImagePicker.vue'
import { clone, createHistory, diffPatch, isEmptyPatch } from './canvas/history'
import { createPatcher } from './canvas/patcher'
import { computeCollapsed } from './canvas/lod'
import {
  LABEL_ZOOM, applyEdgeLabels, applyViewport, buildCells, buildHistoryCells, createGraph, currentViewport,
  highlightEdges, mount, movedPositions, syncRenderArea,
} from './canvas/render'
import { timelineOptions } from './canvas/timeline'
import { communityLayout, compareWithGroups } from './canvas/communities'
import { mindmapLayout, toPatch } from './canvas/layouts'
import { FAMILIES, FAMILY_STYLE, setTheme } from './canvas/shapes'

const canvasEl = ref(null)
const graph = shallowRef(null)
const patcher = shallowRef(null)
const indexDoc = shallowRef(null)
const layoutDoc = shallowRef(null)

const revision = ref(0)
const indexRevision = ref(0)
const status = ref('saved')
const banner = ref('')
const bannerKind = ref('')
const selected = ref(null)
const detail = shallowRef(null)          // GET /api/node/:id 的结果（md 原文 + 出入边）
const showRaw = ref(false)
const pending = ref([])                  // 待提交的 ChangeSet（本地攒着，未确认不碰 md）
const changePreview = shallowRef(null)   // 预览结果（每个文件的 diff）
const draft = reactive({ relation: '', target: '', year: '', note: '' })
const stats = reactive({ nodes: 0, edges: 0, stubs: 0 })
// 结构族默认不画线：嵌套（分组框）已经表达了归属，86 条结构边里有 74 条两端同框，
// 画出来纯属重复噪音（设计文档 3.6）。需要看的时候在工具条勾上。
const visible = reactive(Object.fromEntries(FAMILIES.map((f) => [f, f !== '结构'])))
const edgesShown = ref(0)
const aggShown = ref(0)
const has3d = ref(false)          // 服务端有 web3d 构建产物时才显示 3D 入口
const inboxCount = ref(0)
const inboxItems = shallowRef([])        // GET /api/inbox：索引里有、画布上还没有的节点
const showInbox = ref(false)
const showDigest = ref(false)
const digest = shallowRef(null)          // GET /api/digest：欠账清单
const dueIds = shallowRef(new Set())     // 今天该复习的节点，画布上点一个金色小圆点
const placing = ref(false)
const showPicker = ref(false)   // 贴图面板
// 历史视图（阶段 6）：X 轴锁在年份上，坐标不持久化，进来一次算一次
const mode = ref('structure')
const hist = reactive({ compact: false, validity: false, upto: null, 演化: true, 依赖: false, 对照: false })
const histPlan = shallowRef(null)
const timelines = ref([])          // 选中的 layout 分组 id（空 = 全部）
let playing = null
const autoLod = ref(true)                // 缩小自动折叠成簇卡片（设计文档 3.7）
const focusGroup = ref(null)             // 聚焦的域：点簇卡片进入，只展开它
const search = ref('')                   // 工具条搜索框
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
// 只有用户真的操作过画布才允许落盘：既避免"打开页面就涨 revision"，
// 也不依赖 requestAnimationFrame（后台标签页 / 无头浏览器里 rAF 不触发）
const ready = ref(false)
let applyingViewport = false   // 程序化设置视口期间不落盘，否则切族/展开都白涨一个 revision

const statusText = computed(() => ({
  saved: '已保存', saving: '保存中…', dirty: '待保存', retry: '有冲突，已重试', error: '保存失败',
}[status.value] || status.value))

const visibleFamilies = () => new Set(FAMILIES.filter((f) => visible[f]))

function setBanner(text, kind = '') {
  banner.value = text
  bannerKind.value = kind
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
  render({ view: 'stored' })
  reportProblems(index, layout)
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
    due: dueIds.value,
  })
  edgesShown.value = cells.edges.filter((e) => e.data.kind === 'edge').length
  aggShown.value = cells.edges.length - edgesShown.value
  const keep = view === 'keep' ? { zoom: g.zoom(), translate: g.translate() } : null
  applyingViewport = true
  // 这里保持"先 mount 再定视口"：结构视图指望 virtual 裁掉视口外的元素（几千 cell 的性能大头），
  // 视口外的 cell 不渲染是它该有的样子，平移过去自然会补上。
  // 历史视图不同——它一屏就是全部，所以那边反过来先定视口再建 cell（见 renderHistory）。
  mount(g, cells)
  if (keep) {
    g.zoomTo(keep.zoom)
    g.translate(keep.translate.tx, keep.translate.ty)
  } else if (view === 'fit') {
    g.zoomToFit({ padding: 60, maxScale: 1 })
  } else {
    applyViewport(g, layoutDoc.value.viewport)
  }
  syncRenderArea(g)
  applyingViewport = false
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
  // 先定视口再建 cell：X6 的调度器按"当前可视区"决定一个 cell 建好之后要不要摆位，
  // 先 mount 再改视口的话，落在旧视口之外的 cell 会建出 DOM 却没有 transform——
  // 时间轴又宽又扁，首屏之外的节点几乎全中招，看上去就是"全堆在左上角"。
  if (view !== 'keep') {
    g.zoomToRect({ x: -80, y: 0, width: cells.plan.width + 160, height: cells.plan.height + 60 },
                 { maxScale: 1, minScale: 0.35 })
  }
  syncRenderArea(g)
  mount(g, cells)
  applyingViewport = false
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
  playing = setInterval(() => {
    if (hist.upto === null || hist.upto >= max) return stopPlay()
    hist.upto += 1
    renderHistory({ view: 'keep' })
  }, 220)
}

function stopPlay() {
  if (playing) clearInterval(playing)
  playing = null
}

function toggleTimeline(id) {
  const next = timelines.value.includes(id)
    ? timelines.value.filter((x) => x !== id)
    : [...timelines.value, id]
  timelines.value = next
  renderHistory({ view: 'fit' })
}

function reportProblems(index, layout) {
  const parts = []
  if (index.errors.length) parts.push(`索引有 ${index.errors.length} 个错误（knowrary check 看详情）`)
  if (layout.orphans.length) parts.push(`${layout.orphans.length} 条孤立布局记录（红色虚线节点，不会自动删除）`)
  if (layout.generated) parts.push('已按 field / 目录生成初始布局，拖动即保存')
  const stale = Object.entries(layout.layout.nodes).filter(([, n]) => n.state === 'draft' && staleDays(n) >= 7)
  if (stale.length) parts.push(`${stale.length} 个草稿放了一周以上（「欠账」里可以逐个定稿）`)
  setBanner(parts.join('；'), index.errors.length ? 'error' : '')
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
    for (const p of movedPositions(node)) queueIfChanged(p.kind, p.id, { x: p.x, y: p.y })
  }))
  g.on('node:change:parent', safe(({ node, current }) => {
    if (!ready.value || node.shape !== 'kg-node') return
    const pos = node.position()
    queueIfChanged('node', node.id, { group: current || null, x: Math.round(pos.x), y: Math.round(pos.y) })
  }))
  g.on('node:selected', safe(({ node }) => {
    selected.value = describe(node.id)
    focus(node.id)
    loadDetail(node.id)
  }))
  g.on('node:unselected', safe(() => { selected.value = null; detail.value = null; focus(null) }))
  g.on('blank:click', safe(() => {
    selected.value = null
    detail.value = null
    focus(null)
    g.getEdges().forEach((e) => e.removeTools())   // 顺手摘掉拐点手柄
  }))
  // 悬停即高亮：不点也能看清一个节点牵着哪些线
  g.on('node:mouseenter', safe(({ node }) => { if (node.shape === 'kg-node') focus(node.id) }))
  g.on('node:mouseleave', safe(() => { focus(selected.value?.id || null) }))
  // 点簇卡片 → 放大进这个域（只展开它）；再点「返回全景」或按 Esc 缩回去
  g.on('node:click', safe(({ node }) => {
    if (node.shape === 'kg-cluster') enterGroup(node.id)
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
  g.on('translate', safe(() => saveViewport()))
}

/** 给边挂上拐点手柄：拖圆点造拐点，双击边清空。别的边先摘掉手柄，画面才不乱。 */
function editVertices(edge) {
  if (!writable()) return
  const g = graph.value
  g.getEdges().forEach((e) => e.id !== edge.id && e.removeTools())
  edge.addTools([{ name: 'vertices', args: { attrs: { r: 5, fill: '#fff', stroke: '#2d6cdf', strokeWidth: 2 } } }])
  setBanner('拖动边上的圆点调拐点，双击这条边清掉拐点')
}

function saveViewport() {
  if (!ready.value || applyingViewport || !writable()) return
  patcher.value.queueViewport(currentViewport(graph.value))
}

function onZoom() {
  const g = graph.value
  saveViewport()
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
  (indexDoc.value?.families || []).map((f) => ({ family: f.name, types: f.types })))

const allNodeIds = computed(() => (indexDoc.value?.nodes || []).map((n) => n.id))

function queueChange(change) {
  pending.value = [...pending.value, change]
  changePreview.value = null
}

function addEdgeDraft() {
  if (!detail.value || !draft.relation || !draft.target) return
  queueChange({
    type: 'add_edge', source: detail.value.id, relation: draft.relation, target: draft.target,
    ...(draft.year ? { year: Number(draft.year) } : {}),
    ...(draft.note ? { note: draft.note } : {}),
  })
  draft.target = ''
  draft.year = ''
  draft.note = ''
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
    setBanner(`已写回 ${res.files.length} 个文件，原文备份在 ${res.backup}`)
  } catch (err) {
    setBanner(`写回失败：${err.body?.detail?.message || err.body?.detail || err.message}`, 'error')
  }
}

function dropChange(i) {
  pending.value = pending.value.filter((_, idx) => idx !== i)
  changePreview.value = null
}

function describeChange(c) {
  if (c.type === 'add_edge') return `新增　${c.relation} → ${c.target}`
  if (c.type === 'remove_edge') return `删除　${c.relation} → ${c.target}`
  if (c.type === 'update_edge') return `改类型　${c.from_relation} → ${c.relation}（${c.target}）`
  return `改 frontmatter　${Object.entries(c.fields).map(([k, v]) => `${k} = ${v}`).join('、')}`
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
  const cell = g.getCellById(id)
  if (cell) {
    g.cleanSelection?.()
    g.select?.(cell)
  }
  selected.value = describe(id)
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

function addNote() {
  const text = window.prompt('便签内容', '')
  if (!text) return
  const notes = [...(layoutDoc.value.notes || []), { id: newId('nt'), text, ...viewportCenter(), w: 190, h: 74 }]
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
    setBanner(`${label}：放上 ${res.placed.length} 个草稿${grown}${skipped}`, res.placed.length ? '' : 'error')
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
    setBanner('松手的位置不在任何分组框里——拖到某个分组框内，或用「放进去」按钮', 'error')
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
    if (showDigest.value) refreshDigest()
    setBanner(`已记录第 ${res.reviews} 次复习，下次 ${res.next_due} 再来`)
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
  setBanner(`「${id}」已定稿`)
}

function toggleDigest() {
  showDigest.value = !showDigest.value
  if (showDigest.value) refreshDigest()
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
  showPicker.value = false
  render()
  setBanner(`已贴上 ${file}（拖角可以拉伸，只改 layout）`)
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

/** 下拉里既有"换布局"也有"换视图"：3D 是另一个页面，直接跳过去。 */
function onPick(kind) {
  if (!kind) return
  if (kind === '3d') {
    window.location.href = './3d/'
    return
  }
  runLayout(kind)
}

function runLayout(kind) {
  const spec = LAYOUTS[kind]
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
  setBanner(`预览「${spec.label}」：${extra}。确认后才写盘，旧布局会自动备份。`)
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
    setBanner(saved.backup ? `已应用新布局，旧布局备份在 ${saved.backup}` : '已应用新布局')
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
  applyingViewport = true
  g.zoomToRect({ x: box.x - 60, y: box.y - 60, width: box.w + 120, height: box.h + 120 }, { maxScale: 1.4 })
  applyingViewport = false
  setBanner(`已放大到「${box.name}」，点「返回全景」或按 Esc 退回`)
}

/** 退回全景：还原进入前的视口。 */
function exitGroup() {
  if (!focusGroup.value) return
  const g = graph.value
  focusGroup.value = null
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
  setBanner('')
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

function onKeydown(e) {
  const tag = (e.target?.tagName || '').toLowerCase()
  if (tag === 'input' || tag === 'textarea') return
  if (e.key === 'Escape') {
    exitGroup()
    return
  }
  if (!(e.metaKey || e.ctrlKey)) return
  const key = e.key.toLowerCase()
  if (key === 'escape') return
  if (key === 'z') {
    e.preventDefault()
    e.shiftKey ? redo() : undo()
  } else if (key === 'y') {
    e.preventDefault()
    redo()
  }
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
  graph.value.zoomToFit(mode.value === 'history'
    ? { padding: 40, maxScale: 1, minScale: 0.35 }
    : { padding: 60, maxScale: 1 })
  syncRenderArea(graph.value)
}

async function reload() {
  await patcher.value.flush()
  await load()
}

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
      if (s === 'saved' && bannerKind.value === 'error') setBanner('')
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
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('error', onGlobalError)
  window.removeEventListener('unhandledrejection', onGlobalError)
  patcher.value?.flush()
})
</script>

<template>
  <div class="app">
    <header>
      <span class="brand">Knowrary</span>
      <span class="tabs">
        <button :class="{ primary: mode === 'structure' }" @click="switchMode('structure')">结构视图</button>
        <button :class="{ primary: mode === 'history' }" title="只看有 year 的节点，X 轴是年份"
                @click="switchMode('history')">历史视图</button>
      </span>
      <span v-if="focusGroup && mode === 'structure'" class="muted">聚焦：{{ layoutDoc?.groups?.[focusGroup]?.name }}</span>
      <span v-if="mode === 'structure'" class="muted">节点 {{ stats.nodes }} · 组内边 {{ edgesShown }}<template v-if="aggShown"> · 跨组 {{ aggShown }} 束</template>
        / 共 {{ stats.edges }} · stub {{ stats.stubs }}
        <template v-if="inboxCount"> · Inbox {{ inboxCount }}</template>
      </span>
      <span v-else class="muted">有 year 的节点 {{ histPlan?.placed.size ?? 0 }} · 泳道 {{ histPlan?.lanes.length ?? 0 }}
        · {{ yearRange[0] }}–{{ yearRange[1] }}</span>
      <span v-if="mode === 'structure'" class="families">
        <label v-for="f in FAMILIES" :key="f">
          <input type="checkbox" v-model="visible[f]" @change="render()" />
          <i class="swatch" :style="{ borderTopColor: FAMILY_STYLE[f].stroke,
                                      borderTopStyle: FAMILY_STYLE[f].strokeDasharray ? 'dashed' : 'solid' }" />
          {{ f }}
        </label>
      </span>
      <label v-if="mode === 'structure'" class="families">
        <input type="checkbox" v-model="aggregate" @change="expanded = new Set(); render()" />
        聚合跨组边
      </label>
      <label v-if="mode === 'structure'" class="families" title="缩小时把分组折叠成簇卡片（点卡片展开）">
        <input type="checkbox" v-model="autoLod" @change="render()" />
        自动折叠
        <span v-if="collapsedIds.size" class="muted">（{{ collapsedIds.size }} 簇）</span>
      </label>
      <span class="search">
        <input v-model="search" placeholder="搜索知识点…" @keydown.enter="searchHits[0] && gotoNode(searchHits[0].id)" />
        <ul v-if="searchHits.length" class="hits">
          <li v-for="h in searchHits" :key="h.id" @click="gotoNode(h.id)">
            {{ h.name || h.id }}<span class="muted"> · {{ h.field || '' }}</span>
          </li>
        </ul>
      </span>
      <button v-if="mode === 'structure'" title="在视口中心加一张便签（只存 layout，不进 md）" @click="addNote">＋便签</button>
      <button v-if="mode === 'structure'" title="贴一张 assets/ 里的图（只存 layout，不进 md）"
              @click="showPicker = !showPicker">＋图片</button>
      <button :class="{ primary: inboxCount && !showInbox }" title="写好了还没上画布的节点"
              @click="showInbox = !showInbox; showInbox && refreshInbox()">
        Inbox<template v-if="inboxCount"> {{ inboxCount }}</template>
      </button>
      <button title="草稿 / 待复习 / 跨分组桥 / 重复候选" @click="toggleDigest">
        欠账<template v-if="dueIds.size"> · 待复习 {{ dueIds.size }}</template>
      </button>
      <span class="spacer" />
      <template v-if="preview">
        <button class="primary" @click="applyPreview">应用布局</button>
        <button @click="cancelPreview">取消</button>
      </template>
      <select v-else-if="mode === 'structure'" class="layout-menu" @change="onPick($event.target.value); $event.target.value = ''">
        <option value="">视图 / 布局…</option>
        <option v-for="(spec, kind) in LAYOUTS" :key="kind" :value="kind">{{ spec.label }}</option>
        <option v-if="has3d" value="3d">3D 总览（新页面）</option>
      </select>
      <button v-if="focusGroup && mode === 'structure'" class="primary" @click="exitGroup">← 返回全景</button>
      <template v-if="mode === 'structure'">
        <button :disabled="!canUndo || !!preview" title="撤销（⌘Z / Ctrl+Z）" @click="undo">↶ 撤销</button>
        <button :disabled="!canRedo || !!preview" title="重做（⇧⌘Z / Ctrl+Y）" @click="redo">↷ 重做</button>
      </template>
      <button @click="fit">适应窗口</button>
      <button title="画布没反应时点这里重建（不影响已保存的布局）" @click="rebuildGraph('手动重建')">恢复画布</button>
      <button :title="theme === 'dark' ? '切到浅色' : '切到深色'" @click="toggleTheme">{{ theme === 'dark' ? '☀︎' : '☾' }}</button>
      <button :disabled="!!preview" @click="reload">重新加载</button>
      <span class="rev">index r{{ indexRevision }} · layout r{{ revision }}</span>
      <span class="status" :class="status">{{ statusText }}</span>
    </header>

    <div v-if="mode === 'history'" class="timeline-bar">
      <span class="muted">时间线</span>
      <span class="chips">
        <button :class="{ primary: !timelines.length }" @click="timelines = []; renderHistory({ view: 'fit' })">全部</button>
        <button v-for="opt in timelineChoices" :key="opt.id" :class="{ primary: timelines.includes(opt.id) }"
                :title="`按「${opt.name}」切一条独立时间线（可多选叠加）`" @click="toggleTimeline(opt.id)">
          {{ '· '.repeat(opt.depth) }}{{ opt.name }}
        </button>
      </span>
      <span class="families">
        <label v-for="f in ['演化', '依赖', '对照']" :key="f">
          <input type="checkbox" v-model="hist[f]" @change="renderHistory({ view: 'keep' })" />{{ f }}
        </label>
        <label title="空白超过 20 年的区段压缩成固定宽度">
          <input type="checkbox" v-model="hist.compact" @change="renderHistory({ view: 'fit' })" />紧凑
        </label>
        <label title="按 start_year ≤ 当前年 &lt; end_year 过滤：只看那一年仍然有效的东西（法律 / 标准场景）">
          <input type="checkbox" v-model="hist.validity" @change="renderHistory({ view: 'keep' })" />有效期
        </label>
      </span>
      <span class="slider">
        <button :title="playing ? '暂停' : '按年回放'" @click="togglePlay">{{ playing ? '❙❙' : '▶' }}</button>
        <input type="range" :min="yearRange[0]" :max="yearRange[1]" :value="hist.upto ?? yearRange[1]"
               @input="setUpto($event.target.value)" />
        <span class="muted">{{ hist.upto === null ? '全部年份' : `≤ ${hist.upto}` }}</span>
        <button v-if="hist.upto !== null" class="mini" title="放开年份限制" @click="setUpto(null)">✕</button>
      </span>
    </div>

    <div v-if="banner" class="banner" :class="bannerKind">{{ banner }}</div>

    <main>
      <ImagePicker v-if="showPicker && mode === 'structure'" @pick="addImage" @close="showPicker = false"
                   @error="setBanner($event, 'error')" />
      <InboxTray v-if="showInbox && mode === 'structure'" :items="inboxItems" :busy="placing"
                 @place="placeOne" @place-all="placeAll" @close="showInbox = false" />
      <div ref="canvasEl" class="canvas" @dragover.prevent @drop="onCanvasDrop" />
      <aside v-if="selected">
        <h3>{{ selected.name || selected.id }}</h3>
        <div class="desc">{{ selected.desc || '（无摘要）' }}</div>
        <dl>
          <dt>id</dt><dd>{{ selected.id }}</dd>
          <template v-if="selected.field"><dt>领域</dt><dd>{{ selected.field }}</dd></template>
          <template v-if="selected.type"><dt>类型</dt><dd>{{ selected.type }}</dd></template>
          <template v-if="selected.year"><dt>年份</dt><dd>{{ selected.year }}</dd></template>
          <template v-if="selected.weight"><dt>权重</dt><dd>{{ (selected.weight * 100).toFixed(0) }}%（pageRank）</dd></template>
          <template v-if="detail"><dt>文件</dt><dd>{{ detail.path }}</dd></template>
        </dl>

        <div class="row" v-if="detail">
          <a class="btn" :href="detail.obsidian_uri">在 Obsidian 打开</a>
          <button @click="editDesc">改摘要</button>
          <button @click="showRaw = !showRaw">{{ showRaw ? '收起原文' : '看 md 原文' }}</button>
          <button title="在当前视口放一张指向它的引用卡" @click="addRef">放引用卡</button>
          <button v-if="dueIds.has(selected.id)" class="primary" title="记一次复习（只写 review-log.json）"
                  @click="markReviewed(selected.id)">✓ 复习过了</button>
          <button v-if="selected.placed?.state === 'draft'" class="primary"
                  title="位置确认下来，不再是草稿（只改 layout）" @click="finalize(selected.id)">定稿</button>
        </div>
        <pre v-if="showRaw && detail" class="raw">{{ detail.raw }}</pre>

        <template v-if="detail">
          <strong>出边 {{ detail.out.length }}<span class="muted">（可改，写回本文件）</span></strong>
          <ul class="edges">
            <li v-for="e in detail.out" :key="e.id">
              <select :value="e.type" @change="retypeEdge(e, $event.target.value)">
                <optgroup v-for="g in relationTypes" :key="g.family" :label="g.family">
                  <option v-for="t in g.types" :key="t" :value="t">{{ t }}</option>
                </optgroup>
              </select>
              → {{ e.target }}<span class="fam" v-if="e.year">（{{ e.year }}）</span>
              <button class="mini" title="删除这条关系" @click="removeEdge(e)">✕</button>
            </li>
          </ul>

          <strong>新增关系</strong>
          <div class="add-edge">
            <select v-model="draft.relation">
              <option value="">类型…</option>
              <optgroup v-for="g in relationTypes" :key="g.family" :label="g.family">
                <option v-for="t in g.types" :key="t" :value="t">{{ t }}</option>
              </optgroup>
            </select>
            <input v-model="draft.target" list="kg-nodes" placeholder="目标节点 id" />
            <datalist id="kg-nodes"><option v-for="id in allNodeIds" :key="id" :value="id" /></datalist>
            <input v-model="draft.year" class="year" placeholder="年份" />
            <input v-model="draft.note" placeholder="说明（可选）" />
            <button :disabled="!draft.relation || !draft.target" @click="addEdgeDraft">加入变更</button>
          </div>

          <template v-if="detail.in_edges.length">
            <strong>入边 {{ detail.in_edges.length }}<span class="muted">（写在对方文件里）</span></strong>
            <ul class="edges">
              <li v-for="e in detail.in_edges" :key="e.id">{{ e.source }} {{ e.type }} →</li>
            </ul>
          </template>
        </template>
        <p class="muted" v-if="selected.orphan">这个节点在索引里不存在，只剩布局记录。</p>
      </aside>

      <aside v-if="pending.length" class="changes">
        <h3>待写回的变更 {{ pending.length }}</h3>
        <p class="muted">未确认前不会碰任何 md 文件。</p>
        <ul class="edges">
          <li v-for="(c, i) in pending" :key="i">
            {{ describeChange(c) }}
            <button class="mini" @click="dropChange(i)">✕</button>
          </li>
        </ul>
        <div class="row">
          <button @click="previewChanges">预览变更</button>
          <button class="primary" :disabled="!changePreview" @click="applyChanges">确认写入</button>
          <button @click="pending = []; changePreview = null">全部放弃</button>
        </div>
        <template v-if="changePreview">
          <strong>将改动 {{ changePreview.files.length }} 个文件</strong>
          <div v-for="f in changePreview.files" :key="f.path">
            <div class="muted">{{ f.path }}</div>
            <pre class="diff">{{ f.diff }}</pre>
          </div>
        </template>
      </aside>

      <DigestPanel v-if="showDigest" :digest="digest" @goto="gotoNode" @refresh="refreshDigest"
                   @review="markReviewed" @close="showDigest = false" />
    </main>
    <div v-if="mode === 'history'" class="hint">X 轴是年份，Y 轴是泳道（选了时间线就按它的直接子分组分）·
      金色流动虚线是「被激活」的跨代关系 · 拖滑块按年回放 · 历史视图只是浏览，不会改结构布局</div>
    <div v-else class="hint">拖空白平移 · 滚轮缩放 · shift+拖空白框选 · 拖节点到别的分组框内即改归属（松手 300ms 后自动保存，⌘Z 可撤销）·
      悬停/选中节点高亮它的边 · 点簇卡片放大进那个域（Esc 或「返回全景」退回）· 点「跨组 n 束」展开明细</div>
  </div>
</template>
