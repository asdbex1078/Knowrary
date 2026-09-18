// X6 图实例与「index + layout → 画布元素」的投影。
// 投影层只放当前应该可见的元素；阶段 5 的 LOD 会在这里按缩放级别裁剪。
import { Graph } from '@antv/x6'
import { Selection } from '@antv/x6-plugin-selection'
import { Snapline } from '@antv/x6-plugin-snapline'
import { Transform } from '@antv/x6-plugin-transform'
import { clusterSummary, containerOf } from './lod.js'
import { CLUSTER_H, CLUSTER_W, CURSOR_ID, CURSOR_W, FAMILY_STYLE, clusterBox, NODE_H, NODE_W, aggregateAttrs,
         aggregateLabel, clusterAttrs, activationAttrs, dotAttrs, edgeAttrs, groupAttrs, imageAttrs, laneAttrs, nodeAttrs,
         noteAttrs, paletteFor, NEUTRAL, refAttrs, registerShapes, sizeFor, tickAttrs, tokens } from './shapes.js'
import { AXIS_H, TICK_OFFSET, activeAt, buildTimeline } from './timeline.js'
import { buildLineage } from './lineage.js'

/**
 * 历史视图的画布元素：泳道 + 年份刻度 + 有 year 的节点 + 两端都在图里的边。
 * 与结构视图共用同一个 X6 实例与节点形状，只是位置来源不同（设计文档 3.8）。
 */
export function buildHistoryCells(index, layout, options = {}) {
  const plan = buildTimeline(index, layout, options)
  const byId = new Map(index.nodes.map((n) => [n.id, n]))
  const fields = [...new Set(index.nodes.map((n) => n.field).filter(Boolean))].sort().map((f) => `field:${f}`)
  const colorKeys = [...Object.keys(layout.groups), ...fields]
  const nodes = []
  for (const lane of plan.lanes) {
    nodes.push({ id: `lane:${lane.name}`, shape: 'kg-lane', x: -40, y: lane.y,
                 width: lane.width + 80, height: lane.h, zIndex: 1, attrs: laneAttrs(lane.name),
                 data: { kind: 'lane' } })
  }
  for (const tick of plan.ticks) {
    nodes.push({ id: `tick:${tick.year}`, shape: 'kg-tick', x: tick.x + TICK_OFFSET, y: AXIS_H,
                 width: 1, height: Math.max(plan.height - AXIS_H, 80), zIndex: 2,
                 attrs: tickAttrs(tick.year), data: { kind: 'tick' } })
  }
  for (const [id, box] of plan.placed) {
    const meta = byId.get(id)
    const group = layout.nodes?.[id]?.group
    const color = paletteFor(group || (meta?.field ? `field:${meta.field}` : null), colorKeys)
    nodes.push({
      id, shape: 'kg-dot', x: box.x, y: box.y, width: box.w, height: box.h, zIndex: 10,
      attrs: dotAttrs(meta, color, { showName: box.showName !== false, year: box.year }),
      data: { kind: 'node', group: group || null, field: meta?.field || null,
              name: meta?.name || id, year: box.year },
    })
  }
  // 时间游标：位置由 paintHistoryTime 每次挪，这里只负责把它建出来
  nodes.push({ id: CURSOR_ID, shape: 'kg-cursor', x: -999, y: AXIS_H - 6, width: CURSOR_W,
               height: Math.max(plan.height - AXIS_H + 6, 80), zIndex: 9,
               attrs: { label: { text: '' } }, data: { kind: 'cursor' } })
  const edges = plan.edges.map((e) => {
    const gold = e.type === '被激活'
    const attrs = gold ? activationAttrs() : edgeAttrs(e.family)
    return {
      id: e.id, source: e.source, target: e.target, zIndex: gold ? 8 : 5,
      attrs, connector: { name: 'smooth' },
      data: { kind: 'edge', family: e.family, type: e.type, year: e.year ?? null,
              baseWidth: attrs.line.strokeWidth, baseDash: attrs.line.strokeDasharray || null,
              baseClass: attrs.line.class || null, baseZ: gold ? 8 : 5 },
    }
  })
  return { nodes, edges, plan }
}

const LIT_MS = 620               // "刚被游标扫过"的点亮时长，和 CSS 里的 kg-lit 对齐

/** 一个 cell 对应的那个 <g>，取不到就返回 null（还没挂载 / 已经被换掉）。 */
function viewEl(graph, id) {
  const cell = graph.getCellById(id)
  return cell ? graph.findViewByCell(cell)?.container || null : null
}

function lit(el) {
  clearTimeout(el.__kgLit)
  el.classList.remove('kg-lit')
  void el.getBoundingClientRect()          // 强制回流，否则连点两站时动画不会重放
  el.classList.add('kg-lit')
  el.__kgLit = setTimeout(() => el.classList.remove('kg-lit'), LIT_MS)
}

/**
 * 把时间游标挪到 upto，并按"已发生 / 未来"给节点和边加减 class。
 *
 * **全程不碰 cell 的增删**：这正是回放不再一闪一闪的原因。
 * 以前每帧都 fromJSON 重建整张图，节点 DOM 一换，绑在 .x6-node 上的入场动画就重放一遍，
 * 0.32s 的动画配 0.22s 的间隔 = 永远播不完的闪；加上比例尺跟着可见节点变，已出现的点还一直在滑。
 *
 * prev 是上一站的"已发生"集合，用来只给这一站新亮起来的点做一次点亮动画；传 null 就不点亮
 * （进入历史视图的第一帧、或者滑块往回拖时，不该满屏闪光）。
 * 返回这一站的集合，调用方留着当下一次的 prev。
 */
export function paintHistoryTime(graph, plan, { upto = null, validity = false, prev = null } = {}) {
  if (!graph || !plan) return null
  const active = activeAt(plan, upto, validity)
  for (const id of plan.placed.keys()) {
    const el = viewEl(graph, id)
    if (!el) continue
    const on = active.has(id)
    el.classList.toggle('kg-future', !on)
    if (on && prev && !prev.has(id)) lit(el)
  }
  for (const e of plan.edges) {
    const el = viewEl(graph, e.id)
    if (el) el.classList.toggle('kg-future', !(active.has(e.source) && active.has(e.target)))
  }
  const cursor = graph.getCellById(CURSOR_ID)
  if (cursor) {
    const el = viewEl(graph, CURSOR_ID)
    // 「全部年份」没有游标可言：藏起来，而不是杵在最右边假装扫完了
    el?.classList.toggle('kg-off', upto === null)
    if (upto !== null) {
      cursor.position(plan.at(upto) + TICK_OFFSET - CURSOR_W / 2, AXIS_H - 6)
      cursor.attr('label/text', String(upto))
    }
  }
  return active
}

/** 导览当前停在哪一站：同一时刻只有一个 .kg-stop。传 null 就是全摘掉。 */
export function markStop(graph, id) {
  if (!graph) return
  for (const el of graph.container.querySelectorAll('.kg-stop')) el.classList.remove('kg-stop')
  if (id) viewEl(graph, id)?.classList.add('kg-stop')
}

const DRAGGABLE = new Set(['kg-node', 'kg-group', 'kg-cluster', 'kg-note', 'kg-ref', 'kg-image'])

export const LABEL_ZOOM = 0.8 // 边标签只在放大到这个比例以上才画（性能守则 4）

export function createGraph(container) {
  registerShapes()
  const graph = new Graph({
    container,
    autoResize: true,
    // 异步渲染关掉：切布局时若上一次 fromJSON 还没渲染完，X6 会丢掉这批 cell（模型里有、DOM 里没有）。
    // 这个规模（几百到几千）用不上它。
    async: false,
    // 虚拟渲染（只画视口内的 cell）关掉：它按"当前可视区"决定一个 cell 建好后要不要摆位，
    // 而补画链路只认 WAITING 状态的 view——实测有 view 卡在"已挂载但更新没消费"，
    // 于是平移 / 缩小之后新进视野的节点再也不出现（看得见边、看不见节点）。
    // 这个规模（72 节点 + LOD 折叠，一屏几百个 cell）全量渲染毫无压力，
    // 真正的降维手段是 LOD 折叠成簇卡片，不是 virtual。等真上几千节点再回头评估（阶段 8）。
    virtual: false,
    background: { color: tokens().bg },
    grid: { visible: true, size: 24, type: 'dot', args: { color: tokens().grid, thickness: 1 } },
    // 右键留给上下文菜单，平移只认左键拖空白
    panning: { enabled: true, eventTypes: ['leftMouseDown'] },
    // 滚轮只在按住 ⌘/Ctrl 时缩放。触控板两指滑动在 Mac 上就是普通 wheel 事件，
    // 之前 modifiers: null 把它当成缩放，于是"上下滑 = 整张图忽大忽小"。
    // 捏合缩放浏览器会带 ctrlKey（即使没按键盘），所以捏合仍然缩放。
    mousewheel: { enabled: true, modifiers: ['ctrl', 'meta'], minScale: 0.05, maxScale: 3 },
    embedding: {
      enabled: true,
      frontOnly: false, // 默认只认最前面的元素，会被落点上的其他节点挡住，导致拖进分组反而丢了归属
      // 落点可能同时落在多层分组里；按面积从大到小排序，X6 取最后一个 → 命中最内层分组
      findParent({ node }) {
        const bbox = node.getBBox()
        return this.getNodes()
          .filter((n) => n.shape === 'kg-group' && n.id !== node.id)
          .filter((n) => n.getBBox().containsRect(bbox))
          .sort((a, b) => b.getBBox().width * b.getBBox().height - a.getBBox().width * a.getBBox().height)
      },
      validate: ({ child, parent }) => {
        if (!parent || parent.shape !== 'kg-group') return false
        if (child.shape === 'kg-node') return true
        if (child.shape !== 'kg-group') return false
        // 防环：沿 parent 链往上走，不能碰到 child
        let cur = parent
        while (cur) {
          if (cur.id === child.id) return false
          cur = cur.getParent()
        }
        return true
      },
    },
    // 全图默认走直线：orth 直角折线不做避障，193 条边会绕成迷宫。
    // 手工调过拐点的边仍按 layout.edges 里存的 router 渲染。
    connecting: { router: 'normal', connector: 'normal', allowBlank: false },
    // 只有"内容"能拖。泳道、年份刻度、时间游标是图的骨架不是图的内容——
    // 它们的坐标是算出来的，拖走既没意义，还会被 node:moved 当成真节点写进 layout。
    interacting: { nodeMovable: (view) => DRAGGABLE.has(view.cell.shape) && !view.cell.getData()?.borrowed,
                   edgeMovable: false, edgeLabelMovable: false },
  })
  // 拖空白 = 平移；shift + 拖空白 = 框选
  graph.use(new Selection({ enabled: true, multiple: true, rubberband: true, modifiers: 'shift',
    showNodeSelectionBox: true, filter: (cell) => cell.shape === 'kg-node' }))
  // 可以拉伸的只有图片和分组框。
  // 知识点卡片不行：它的大小是按 pageRank 定的，手动改会让"大小=重要性"这条读图规则失效。
  // 分组框要能拉：框的大小不是算出来的结论，是"我打算在这里画多少东西"的预留——
  // 想在组里加内容、想留白画连线，都得先有地方。簇卡片（折叠态）不给拉，它的尺寸跟着缩放走。
  graph.use(new Transform({
    resizing: {
      enabled: (node) => node.shape === 'kg-image' || node.shape === 'kg-group',
      minWidth: (node) => (node.shape === 'kg-group' ? GROUP_MIN_W : 80),
      minHeight: (node) => (node.shape === 'kg-group' ? GROUP_MIN_H : 60),
      preserveAspectRatio: (node) => node.shape === 'kg-image',
    },
    rotating: false,
  }))
  // 对齐线：拖动时和邻居对齐就画一条参考线。手工摆位的图"一眼望去还行、放大全是歪的"，
  // 根子是没有参考系——1~2px 的错位肉眼在缩小状态下看不出来，放大后全暴露。
  // 默认开，可在画布工具里关掉（tolerance 给 6px：太小吸不住，太大会"粘"到不想对齐的邻居）。
  // 图片是当背景板用的大方块（zIndex 压在节点下面），拿它当对齐参照只会满屏参考线；
  // 其余手放上去的东西——节点、分组框、簇卡片、便签、引用卡——都参与对齐。
  graph.use(new Snapline({ enabled: true, tolerance: 6, sharp: true, resizing: true,
    filter: (node) => node.shape !== 'kg-image' }))
  bindWheelPan(graph)
  return graph
}

export const GROUP_MIN_W = 200   // 再小就装不下一张知识点卡片（160）加边距
export const GROUP_MIN_H = 120

export const SNAP_GRID = 8   // 松手后坐标取整到这个网格；比背景网格（24）细，不会明显挪动位置

/** 对齐线开关。关掉时连吸附一起关，"吸附"这件事对用户是一个概念。 */
export function setSnap(graph, on) {
  const plugin = graph.getPlugin('snapline')
  if (!plugin) return
  if (on) plugin.enable()
  else plugin.disable()
}

/** 某个落点对齐到网格后要挪多少。两个分量都是 0 就说明本来就在格子上。 */
export function snapDelta(x, y, grid = SNAP_GRID) {
  return { dx: Math.round(x / grid) * grid - x, dy: Math.round(y / grid) * grid - y }
}

/**
 * 两指滑动 / 滚轮 = 平移画布（Figma、Miro 都是这个手势）。
 *
 * 缩放交给 ⌘/Ctrl + 滚轮和触控板捏合（捏合的 wheel 事件带 ctrlKey），
 * 那两种在上面的 mousewheel.modifiers 里由 X6 自己处理，这里直接放行。
 */
function bindWheelPan(graph) {
  graph.container.addEventListener('wheel', (ev) => {
    if (ev.ctrlKey || ev.metaKey) return
    ev.preventDefault()
    // shift + 滚轮横向滚：鼠标只有一个滚轮时也能左右移动
    const [dx, dy] = ev.shiftKey && !ev.deltaX ? [ev.deltaY, 0] : [ev.deltaX, ev.deltaY]
    graph.translateBy(-dx, -dy)
  }, { passive: false })
}

/** 分组的祖先链（不含自己）。邻居模式下要连父框一起留住，否则子框会悬空。 */
function groupChain(groups, id) {
  const out = []
  const seen = new Set([id])
  let cur = groups[id]?.parent
  while (cur && !seen.has(cur)) {
    out.push(cur)
    seen.add(cur)
    cur = groups[cur]?.parent
  }
  return out
}

function groupDepth(groups, id, seen = new Set()) {
  const g = groups[id]
  if (!g?.parent || seen.has(id)) return 0
  seen.add(id)
  return 1 + groupDepth(groups, g.parent, seen)
}

/**
 * 谱系树的 cell：节点还是那张卡片（和全局图认得出是同一个东西），
 * 边按"这条枝上挂着多少"加粗。
 */
export function buildLineageCells(index, layout, options = {}) {
  const plan = buildLineage(index, options)
  const byId = new Map(index.nodes.map((n) => [n.id, n]))
  const fields = [...new Set(index.nodes.map((n) => n.field).filter(Boolean))].sort().map((f) => `field:${f}`)
  const colorKeys = [...Object.keys(layout.groups || {}), ...fields]
  const nodes = []
  for (const [id, box] of plan.placed) {
    const meta = byId.get(id)
    const group = layout.nodes?.[id]?.group
    nodes.push({
      id, shape: 'kg-node', x: box.x, y: box.y, width: box.w, height: box.h, zIndex: 10,
      attrs: nodeAttrs(meta, null, paletteFor(group || (meta?.field ? `field:${meta.field}` : null), colorKeys)),
      data: { kind: 'node', group: group || null, field: meta?.field || null, rank: box.rank },
    })
  }
  const edges = plan.edges.map((e) => {
    const gold = e.type === '被激活'
    const base = gold ? activationAttrs() : edgeAttrs(e.family)
    const attrs = { ...base, line: { ...base.line, strokeWidth: e.width } }
    return {
      id: e.id, source: e.source, target: e.target, zIndex: gold ? 8 : 5,
      // 竖向 S 弯：树是从下往上长的，横向的 smooth 会把枝拧成麻花
      attrs, connector: { name: 'smooth', args: { direction: 'V' } },
      data: { kind: 'edge', family: e.family, type: e.type, year: e.year ?? null, count: e.count,
              baseWidth: e.width, baseDash: base.line.strokeDasharray || null,
              baseClass: base.line.class || null, baseZ: gold ? 8 : 5 },
    }
  })
  return { nodes, edges, plan }
}

export function buildCells(index, layout, options = {}) {
  const { families = null, showLabels = false, collapsed = new Set(), zoom = 1, due = new Set(),
          states = {}, only = null, avoidNodes = false, borrowed = new Set() } = options
  // borrowed：画在项目画布上、但不属于这个项目的一跳邻居（GPU 前面的 CPU）。
  // 它们的坐标是**算出来的**（调用方按相连的项目内点摆位），没有也不该有 layout 记录——
  // 所以既不能拖（见 nodeMovable），拖了也不会落盘。
  // only：「只看某个节点的邻居」模式，画布上只留这一小撮节点与它们之间的边。
  // 做成投影层的过滤而不是把别的元素调暗——网状图里"调暗"照样挡视线。
  const keepGroup = only
    ? new Set(Object.entries(layout.nodes).filter(([nid]) => only.has(nid))
        .flatMap(([, n]) => (n.group ? [n.group, ...groupChain(layout.groups, n.group)] : [])))
    : null
  // 折叠后节点"显示成谁"：最外层被折叠的祖先分组，或它自己
  const visibleOf = (nid) => containerOf(layout, nid, collapsed)
  const hasCollapsedAncestor = (gid) => {
    let cur = layout.groups[gid]?.parent
    const seen = new Set()
    while (cur && !seen.has(cur)) {
      if (collapsed.has(cur)) return true
      seen.add(cur)
      cur = layout.groups[cur]?.parent
    }
    return false
  }
  const byId = new Map(index.nodes.map((n) => [n.id, n]))
  // 按"最内层分组"取色：一个簇一个色相。径向布局下没有分组，退回按 field 取色。
  const leaves = Object.keys(layout.groups)
    .filter((g) => !Object.values(layout.groups).some((x) => x.parent === g))
    .sort()
  const fields = [...new Set(index.nodes.map((n) => n.field).filter(Boolean))].sort().map((f) => `field:${f}`)
  const colorKeys = [...leaves, ...fields]
  const colorOf = (gid, field) => paletteFor(gid || (field ? `field:${field}` : null), colorKeys)
  const nodes = []
  for (const [gid, g] of Object.entries(layout.groups)) {
    if (keepGroup && !keepGroup.has(gid)) continue   // 邻居模式：空分组框只是噪音
    if (hasCollapsedAncestor(gid)) continue          // 祖先已折叠，里面的东西都不画
    const color = colorOf(gid)
    if (collapsed.has(gid)) {
      const summary = clusterSummary(layout, index, gid)
      const box = clusterBox(g)
      // 卡片缩在分组框正中间，落盘时要减掉这个偏移才是分组框自己的坐标
      const dx = (g.w - box.w) / 2
      const dy = (g.h - box.h) / 2
      nodes.push({
        id: gid, shape: 'kg-cluster',
        x: g.x + dx, y: g.y + dy, width: box.w, height: box.h, zIndex: 12,
        attrs: clusterAttrs(g.name, summary, color, box, g.doc || null, zoom),
        data: { kind: 'cluster', group: gid, count: summary.count, dx, dy },
      })
      continue
    }
    nodes.push({
      id: gid, shape: 'kg-group', x: g.x, y: g.y, width: g.w, height: g.h,
      zIndex: 1 + groupDepth(layout.groups, gid),
      attrs: groupAttrs(g.name, color, g.doc || null, zoom, g),
      data: { kind: 'group', parent: g.parent || null, doc: g.doc || null },
    })
  }
  for (const [nid, n] of Object.entries(layout.nodes)) {
    if (only && !only.has(nid)) continue
    if (visibleOf(nid) !== nid) continue              // 被折进某个簇里了
    const meta = byId.get(nid)
    const size = sizeFor(meta)
    nodes.push({
      id: nid, shape: 'kg-node', x: n.x, y: n.y,
      width: meta ? size.w : (n.w || NODE_W), height: meta ? size.h : (n.h || NODE_H),
      zIndex: 10,
      attrs: meta
        ? nodeAttrs(meta, n, colorOf(n.group, meta.field),
                    { due: due.has(nid), state: states[nid] || null, borrowed: borrowed.has(nid) })
        : n.state === 'ghost'
          ? nodeAttrs({ id: nid, name: nid }, n, NEUTRAL, { ghost: true })   // 幽灵占位，不是孤立记录
          : orphanAttrs(nid),
      data: { kind: 'node', group: n.group || null, orphan: !meta && n.state !== 'ghost',
              ghost: !meta && n.state === 'ghost', borrowed: borrowed.has(nid),
              field: meta?.field || null },
    })
  }
  // 便签与引用卡：只存在 layout.json 里，不参与关系与索引
  for (const note of (only ? [] : layout.notes || [])) {
    if (note.group && collapsed.has(note.group)) continue
    nodes.push({
      id: `note:${note.id}`, shape: 'kg-note', x: note.x, y: note.y,
      width: note.w || 190, height: note.h || 74, zIndex: 11,
      attrs: noteAttrs(note), data: { kind: 'note', raw: note },
    })
  }
  for (const img of (only ? [] : layout.images || [])) {
    if (img.group && collapsed.has(img.group)) continue
    nodes.push({
      id: `img:${img.id}`, shape: 'kg-image', x: img.x, y: img.y,
      width: img.w || 320, height: img.h || 200, zIndex: 2,     // 压在节点下面，当背景板用
      attrs: imageAttrs(img), data: { kind: 'image', raw: img },
    })
  }
  for (const ref of (only ? [] : layout.refs || [])) {
    if (ref.group && collapsed.has(ref.group)) continue
    const meta = byId.get(ref.target)
    nodes.push({
      id: `ref:${ref.id}`, shape: 'kg-ref', x: ref.x, y: ref.y,
      width: ref.w || 170, height: ref.h || 46, zIndex: 11,
      attrs: refAttrs(meta?.name || ref.target, colorOf(layout.nodes[ref.target]?.group, meta?.field)),
      data: { kind: 'ref', target: ref.target, raw: ref },
    })
  }

  const placed = new Set(Object.keys(layout.nodes))
  const visibleEdges = index.edges.filter(
    (e) => placed.has(e.source) && placed.has(e.target) && (!families || families.has(e.family))
      && (!only || (only.has(e.source) && only.has(e.target)))
      && visibleOf(e.source) !== visibleOf(e.target),   // 两端折进同一簇 → 内部关系，不画
  )
  const { detail, groups: aggregated } = splitEdges(visibleEdges, layout, { ...options, collapsed })
  const seen = new Map() // 同一对节点的第几条边，用来错开平行边
  const edges = []
  for (const e of detail) {
    const style = layout.edges?.[e.id]
    const key = [e.source, e.target].sort().join('\u0000')
    const rank = seen.get(key) ?? 0
    seen.set(key, rank + 1)
    const bend = style?.vertices?.length ? null : parallelBend(layout, e, rank, detail, key)
    const base = edgeAttrs(e.family)
    // 结构族是层级主干（脑图模式下尤其），用平滑曲线，观感接近脑图工具
    const curved = FAMILY_STYLE[e.family]?.curved || !!bend
    edges.push({
      id: e.id, source: e.source, target: e.target, zIndex: 5,
      attrs: base,
      vertices: style?.vertices || (bend ? [bend] : []),
      // 「绕开卡片」：manhattan 把节点当障碍物绕行。默认不开——它会把所有线掰成直角，
      // 是另一种观感；而且手工拐过的边必须听人的，不能被自动路由推翻。
      router: style?.router ? { name: style.router }
        : (avoidNodes && !style?.vertices?.length
            ? MANHATTAN
            : undefined),
      connector: style?.router || (avoidNodes && !style?.vertices?.length)
        ? { name: 'rounded', args: { radius: 8 } }
        : (curved ? { name: 'smooth' } : undefined),
      labels: showLabels ? [edgeLabel(e)] : [],
      data: { kind: 'edge', family: e.family, type: e.type, year: e.year ?? null,
              baseWidth: base.line.strokeWidth, baseDash: base.line.strokeDasharray || null,
              baseClass: base.line.class || null, baseZ: 5 },
    })
  }
  for (const [pair, items] of aggregated) {
    const [from, to] = pair.split('->')
    const attrs = aggregateAttrs(items.length)
    edges.push({
      id: `agg:${pair}`, source: from, target: to, zIndex: 4,
      attrs, labels: showLabels ? [aggregateLabel(items.length)] : [],   // 缩小时不画数字，避免满屏小标签
      // 聚合边一样要绕：跨组的那几条最长，也最容易横穿别人的卡片
      router: avoidNodes ? MANHATTAN : undefined,
      connector: avoidNodes ? { name: 'rounded', args: { radius: 8 } } : undefined,
      data: { kind: 'agg', pair, count: items.length, baseWidth: attrs.line.strokeWidth, baseDash: null,
              baseClass: null, baseZ: 4, families: [...new Set(items.map((e) => e.family))] },
    })
  }
  return { nodes, edges }
}

// 一对分组之间**少于这么多条**就不聚合：一条「A 组 → B 组」的灰线代替不了
// 「NPU 对比 GPU」这种具体关系——人看图就是在看这个。聚合是治"几十条长斜线糊成一片"的药，
// 不是默认形态；按层分泳道之后几乎每条边都跨组，一刀切聚合会让整张图只剩卡片之间的灰线。
const AGG_MIN = 3

// 绕开卡片的路由。参数是实测调出来的，不是抄默认值：
// manhattan 在网格上跑 A*，**搜不出路就悄悄退回直线**（那时线照样穿卡片）。
// step 决定网格粗细，而卡片之间的缝只有三四十像素——step 28 时格子比缝还宽，
// 于是一条路都找不到：项目画布 17 条边有 9 条穿模。step 12 才穿得过去，实测 0 条穿模。
// excludeTerminals：自己的两端不当障碍物，否则出发点就被判成"在障碍里"。
// 代价是重算一次全图的线要十几毫秒，只在这张图重画时发生，值。
const MANHATTAN = { name: 'manhattan',
                    args: { padding: 12, step: 20, maximumLoops: 50000,
                            excludeTerminals: ['source', 'target'] } }

// 分流：两端在同一分组（或该组对已展开、或这对分组之间线本来就不多）的边照常画；
// 只有密到 AGG_MIN 条以上的跨分组边才按「源分组 → 目标分组」并成一束。
function splitEdges(visibleEdges, layout, options) {
  const { aggregate = true, expanded = new Set(), collapsed = new Set() } = options
  const insideCluster = (nid) => containerOf(layout, nid, collapsed) !== nid
  /**
   * 聚合边的端点必须是画布上真实存在的 cell：
   * - 节点被折进簇里 → 用簇 id；
   * - 否则用它所属分组；分组都没有（顶层裸节点）→ 用它自己。
   * 早先这里对裸节点返回 null，一旦它和某个折叠簇有边，就会拿 null 当 key，
   * 后面 `pair.split('->')` 直接抛错（画布自愈能兜住，但折叠就失效了）。
   */
  const endpointOf = (nid) => {
    const container = containerOf(layout, nid, collapsed)
    if (container !== nid) return container
    return layout.nodes[nid]?.group || nid
  }
  const detail = []
  const groups = new Map()
  for (const e of visibleEdges) {
    const clustered = insideCluster(e.source) || insideCluster(e.target)   // 有一端被折进簇里
    const a = endpointOf(e.source)
    const b = endpointOf(e.target)
    const pair = `${a}->${b}`
    // 两端落进同一个簇：那是簇的内部关系，簇卡片已经代表了它，不画
    if (clustered && a === b) continue
    const gs = layout.nodes[e.source]?.group
    const gt = layout.nodes[e.target]?.group
    // 两端都摆在画布上、又同属一个分组：画节点到节点的真实连线。
    // 聚合只针对跨分组的边——早先这里用 endpointOf（未折叠时返回所属分组）算出的
    // a === b 一并 continue 掉了，等于把所有组内连线都丢了（实测 21 条一条不剩）。
    if (!clustered && gs && gs === gt) {
      detail.push(e)
      continue
    }
    const bothGrouped = a === gs && b === gt
    if (!clustered && (!aggregate || !bothGrouped || expanded.has(pair))) {
      detail.push(e)
      continue
    }
    if (a === b) continue                     // 端点重合就画不出边（裸节点落在同一个容器上）
    if (!groups.has(pair)) groups.set(pair, [])
    groups.get(pair).push({ edge: e, clustered })
  }
  // 线不多的那些组对退回去画真实连线；有一端被折进簇里的没得退（那个节点根本不在画布上）
  const dense = new Map()
  for (const [pair, items] of groups) {
    const stuck = items.filter((x) => x.clustered)
    if (items.length >= AGG_MIN || stuck.length === items.length) {
      dense.set(pair, items.map((x) => x.edge))
      continue
    }
    for (const x of items) {
      if (x.clustered) dense.set(pair, [...(dense.get(pair) || []), x.edge])
      else detail.push(x.edge)
    }
  }
  return { detail, groups: dense }
}

// 同一对节点之间有多条边时（如 CPU 部件/控制 寄存器），给第 n 条边一个法向拐点，
// 让它们成为几条分开的弧线而不是一条重叠的粗线。
function parallelBend(layout, edge, rank, all, key) {
  const total = all.filter((e) => [e.source, e.target].sort().join('\u0000') === key).length
  if (total < 2) return null
  const a = layout.nodes[edge.source]
  const b = layout.nodes[edge.target]
  if (!a || !b) return null
  const ax = a.x + (a.w || NODE_W) / 2
  const ay = a.y + (a.h || NODE_H) / 2
  const bx = b.x + (b.w || NODE_W) / 2
  const by = b.y + (b.h || NODE_H) / 2
  const dx = bx - ax
  const dy = by - ay
  const len = Math.hypot(dx, dy) || 1
  const offset = (rank % 2 === 0 ? 1 : -1) * (Math.floor(rank / 2) + 1) * 18
  return { x: (ax + bx) / 2 + (-dy / len) * offset, y: (ay + by) / 2 + (dx / len) * offset }
}

function orphanAttrs(nid) {
  return {
    body: { fill: '#fff6f6', stroke: '#d98a8a', strokeDasharray: '5 3', rx: 10, ry: 10 },
    title: { text: nid, fill: '#a34747', fontSize: 13 },
    desc: { text: '索引里没有这个节点', fill: '#c08585', fontSize: 10.5 },
  }
}

function edgeLabel(edge) {
  return {
    attrs: {
      text: { text: edge.year ? `${edge.type} ${edge.year}` : edge.type, fontSize: 10, fill: '#7a8794' },
      rect: { fill: '#f7f8fa', stroke: 'none' },
    },
  }
}

export function mount(graph, cells) {
  graph.fromJSON(cells)
  // fromJSON 之后再建父子关系：分组移动时 X6 自动带着子元素走
  for (const cell of cells.nodes) {
    const parentId = cell.data.kind === 'group' ? cell.data.parent : cell.data.group
    if (!parentId) continue
    const parent = graph.getCellById(parentId)
    const child = graph.getCellById(cell.id)
    if (parent && child) parent.addChild(child)
  }
  // **建完父子关系必须让绕行路由重算一遍。**
  // 路由是在 fromJSON 那一刻算的，那时节点还不是分组的孩子，于是分组框自己
  // 成了一个盖住全场的障碍物——一条路都找不到，manhattan 静静退回直线，
  // 线照样从卡片身上穿过去（全局图 2 条、项目图 10 条，肉眼还不容易发现）。
  // 重算一次十几毫秒，只在重画时发生。
  const routed = cells.edges.filter((e) => e.router)
  if (routed.length) {
    for (const spec of routed) {
      const edge = graph.getCellById(spec.id)
      if (!edge) continue
      // 先静默清掉再设回去：X6 对 prop 做深比较，设一个"一模一样"的值不会触发重算
      edge.prop('router', undefined, { silent: true })
      edge.prop('router', { ...spec.router, args: { ...spec.router.args } })
    }
  }
}

/** layout 里所有元素占的框（不建 cell 也能算，用来判断存的视口还值不值得恢复）。 */
export function contentBBox(layout) {
  let x0 = Infinity; let y0 = Infinity; let x1 = -Infinity; let y1 = -Infinity
  const eat = (b, dw = NODE_W, dh = NODE_H) => {
    if (typeof b?.x !== 'number' || typeof b?.y !== 'number') return
    x0 = Math.min(x0, b.x)
    y0 = Math.min(y0, b.y)
    x1 = Math.max(x1, b.x + (b.w || dw))
    y1 = Math.max(y1, b.y + (b.h || dh))
  }
  Object.values(layout?.groups || {}).forEach((g) => eat(g))
  Object.values(layout?.nodes || {}).forEach((n) => eat(n))
  for (const key of ['notes', 'refs', 'images']) (layout?.[key] || []).forEach((i) => eat(i))
  if (!Number.isFinite(x0)) return null
  return { x: x0, y: y0, width: Math.max(x1 - x0, 1), height: Math.max(y1 - y0, 1) }
}

export function applyViewport(graph, viewport) {
  if (!viewport || !viewport.zoom || (!viewport.cx && !viewport.cy)) {
    graph.zoomToFit({ padding: 60, maxScale: 1 })
    return
  }
  graph.zoomTo(viewport.zoom)
  graph.centerPoint(viewport.cx, viewport.cy)
}

export function currentViewport(graph) {
  const center = graph.graphToLocal(graph.container.clientWidth / 2, graph.container.clientHeight / 2)
  return { zoom: +graph.zoom().toFixed(3), cx: Math.round(center.x), cy: Math.round(center.y) }
}

export function applyEdgeLabels(graph, index, show) {
  const meta = new Map(index.edges.map((e) => [e.id, e]))
  graph.batchUpdate(() => {
    for (const edge of graph.getEdges()) {
      const e = meta.get(edge.id)
      edge.setLabels(show && e ? [edgeLabel(e)] : [])
    }
  })
}

// 悬停 / 选中某个节点时：它的边亮起来、其余边淡出，网状图才看得清。
//
// flow：亮起来的边跑虚线动画（.kg-flow），像有光点顺着关系流过去。
// 静态的"变粗"只告诉你哪几根有关，流动还额外告诉你**朝哪个方向**——
// 这在一张到处是双向语义的图里，比箭头小三角好认得多。
// 实线族本身没有 dash，流不起来，所以高亮期间临时给一段 dash，退出时按 baseDash 还原。
const FLOW_DASH = '7 6'

/**
 * 写 line 上的某个属性，值没变就一个字节都不动。
 *
 * 不是为了省事：X6 删属性（removeAttrByPath）或把属性置空会触发整条边的视图重建，
 * 原来的 <path> 元素被换掉。悬停高亮每次都无条件重写的话，重建有概率正好落在
 * mousedown 与 mouseup 之间——X6 就不再合成 click，点边挂不上拐点手柄。
 */
function setLine(edge, key, value) {
  const now = edge.attr(`line/${key}`) ?? null
  if ((value ?? null) === now) return
  if (value == null) edge.removeAttrByPath(`line/${key}`)
  else edge.attr(`line/${key}`, value)
}

export function highlightEdges(graph, relatedIds, { flow = false } = {}) {
  graph.batchUpdate(() => {
    for (const edge of graph.getEdges()) {
      const data = edge.getData() || {}
      const base = data.baseWidth || 1
      const on = !relatedIds || relatedIds.has(edge.id)
      const lit = !!relatedIds && on
      edge.attr('line/opacity', on ? 1 : 0.07)
      edge.attr('line/strokeWidth', lit ? base * 2 : base)
      edge.setZIndex(lit ? 30 : data.baseZ ?? (data.kind === 'agg' ? 4 : 5))
      // 退出高亮要还原成这条边**自己的**基础样式："被激活"本来就自带金色流动虚线，
      // 一律清空会把它也抹掉（历史视图里那条边就不流动了）。
      const wantClass = lit && flow ? 'kg-flow' : data.baseClass || null
      const wantDash = lit && flow ? (data.baseDash || FLOW_DASH) : data.baseDash ?? null
      setLine(edge, 'class', wantClass)
      setLine(edge, 'strokeDasharray', wantDash)
    }
  })
}

const PATH_COLOR = '#d8a838'   // 和"被激活"用同一支金色：两者都是"沿着关系走"的意思

/**
 * 点亮一条路径：路上的节点描金边，路上的边流动，其余一律淡出。
 * 节点的原样式先存进 data.pathBase，清除时照着还原——重绘会重建 cell，所以不怕存漏。
 */
export function highlightPath(graph, nodeIds, edgeIds) {
  highlightEdges(graph, edgeIds, { flow: true })
  graph.batchUpdate(() => {
    for (const node of graph.getNodes()) {
      if (node.shape !== 'kg-node') continue
      const on = nodeIds.has(node.id)
      const data = node.getData() || {}
      if (on) {
        if (!data.pathBase) {
          node.setData({ pathBase: { stroke: node.attr('body/stroke'),
                                     strokeWidth: node.attr('body/strokeWidth') } }, { deep: true })
        }
        node.attr('body/stroke', PATH_COLOR)
        node.attr('body/strokeWidth', 2.6)
      } else {
        node.attr('body/opacity', 0.35)
      }
    }
  })
}

export function clearPath(graph) {
  graph.batchUpdate(() => {
    for (const node of graph.getNodes()) {
      if (node.shape !== 'kg-node') continue
      const base = (node.getData() || {}).pathBase
      if (base) {
        node.attr('body/stroke', base.stroke)
        node.attr('body/strokeWidth', base.strokeWidth)
        node.setData({ pathBase: null }, { deep: true })
      }
      node.attr('body/opacity', 1)
    }
  })
  highlightEdges(graph, null)
}


// 分组被拖动时，X6 已经把子元素一起移了；这里收集所有需要落盘的新坐标。
// 簇卡片（折叠起来的分组）是分组的另一种形态，落盘时必须写回 groups 而不是 nodes——
// 早先按 shape !== 'kg-group' 一律当节点写，结果拖过的每个簇都在 layout.nodes 里
// 留下一条同名幽灵记录（画布上是红色虚线孤儿），分组框自己一次都没移动过。
export function movedPositions(cell) {
  const out = []
  const walk = (c) => {
    const pos = c.position()
    const data = c.getData() || {}
    if (data.kind === 'cluster') {
      out.push({ id: c.id, kind: 'group', x: Math.round(pos.x - (data.dx || 0)), y: Math.round(pos.y - (data.dy || 0)) })
      return   // 簇里的节点没建 cell，由调用方按位移量整体平移
    }
    out.push({ id: c.id, kind: c.shape === 'kg-group' ? 'group' : 'node', x: Math.round(pos.x), y: Math.round(pos.y) })
    for (const child of c.getChildren() || []) walk(child)
  }
  walk(cell)
  return out
}
