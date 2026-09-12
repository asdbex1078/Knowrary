// X6 图实例与「index + layout → 画布元素」的投影。
// 投影层只放当前应该可见的元素；阶段 5 的 LOD 会在这里按缩放级别裁剪。
import { Graph } from '@antv/x6'
import { Selection } from '@antv/x6-plugin-selection'
import { Transform } from '@antv/x6-plugin-transform'
import { clusterSummary, containerOf } from './lod'
import { CLUSTER_H, CLUSTER_W, FAMILY_STYLE, clusterBox, NODE_H, NODE_W, aggregateAttrs, aggregateLabel, clusterAttrs,
         activationAttrs, edgeAttrs, groupAttrs, imageAttrs, laneAttrs, nodeAttrs, noteAttrs, paletteFor,
         refAttrs, registerShapes, sizeFor, tickAttrs, tokens } from './shapes'
import { AXIS_H, buildTimeline } from './timeline'

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
    nodes.push({ id: `tick:${tick.year}`, shape: 'kg-tick', x: tick.x + 40, y: AXIS_H,
                 width: 1, height: Math.max(plan.height - AXIS_H, 80), zIndex: 2,
                 attrs: tickAttrs(tick.year), data: { kind: 'tick' } })
  }
  for (const [id, box] of plan.placed) {
    const meta = byId.get(id)
    const group = layout.nodes?.[id]?.group
    nodes.push({
      id, shape: 'kg-node', x: box.x, y: box.y, width: box.w, height: box.h, zIndex: 10,
      attrs: nodeAttrs(meta, null, paletteFor(group || (meta?.field ? `field:${meta.field}` : null), colorKeys)),
      data: { kind: 'node', group: group || null, field: meta?.field || null },
    })
  }
  const edges = plan.edges.map((e) => {
    const gold = e.type === '被激活'
    const attrs = gold ? activationAttrs() : edgeAttrs(e.family)
    return {
      id: e.id, source: e.source, target: e.target, zIndex: gold ? 8 : 5,
      attrs, connector: { name: 'smooth' },
      data: { kind: 'edge', family: e.family, type: e.type, year: e.year ?? null,
              baseWidth: attrs.line.strokeWidth },
    }
  })
  return { nodes, edges, plan }
}

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
    panning: { enabled: true, eventTypes: ['leftMouseDown', 'rightMouseDown'] },
    mousewheel: { enabled: true, modifiers: null, minScale: 0.05, maxScale: 3 },
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
      validate: ({ child, parent }) => child.shape === 'kg-node' && parent?.shape === 'kg-group',
    },
    // 全图默认走直线：orth 直角折线不做避障，193 条边会绕成迷宫。
    // 手工调过拐点的边仍按 layout.edges 里存的 router 渲染。
    connecting: { router: 'normal', connector: 'normal', allowBlank: false },
    interacting: { nodeMovable: true, edgeMovable: false, edgeLabelMovable: false },
  })
  // 拖空白 = 平移；shift + 拖空白 = 框选
  graph.use(new Selection({ enabled: true, multiple: true, rubberband: true, modifiers: 'shift',
    showNodeSelectionBox: true, filter: (cell) => cell.shape === 'kg-node' }))
  // 只有图片可以拉伸：知识点卡片的大小是按 pageRank 定的，手动改会让"大小=重要性"这条读图规则失效
  graph.use(new Transform({ resizing: { enabled: (node) => node.shape === 'kg-image', minWidth: 80,
    minHeight: 60, preserveAspectRatio: true }, rotating: false }))
  return graph
}

function groupDepth(groups, id, seen = new Set()) {
  const g = groups[id]
  if (!g?.parent || seen.has(id)) return 0
  seen.add(id)
  return 1 + groupDepth(groups, g.parent, seen)
}

export function buildCells(index, layout, options = {}) {
  const { families = null, showLabels = false, collapsed = new Set(), zoom = 1, due = new Set() } = options
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
    if (hasCollapsedAncestor(gid)) continue          // 祖先已折叠，里面的东西都不画
    const color = colorOf(gid)
    if (collapsed.has(gid)) {
      const summary = clusterSummary(layout, index, gid)
      const box = clusterBox(g, zoom)
      nodes.push({
        id: gid, shape: 'kg-cluster',
        x: g.x + (g.w - box.w) / 2, y: g.y + (g.h - box.h) / 2, width: box.w, height: box.h, zIndex: 12,
        attrs: clusterAttrs(g.name, summary, color, box),
        data: { kind: 'cluster', group: gid, count: summary.count },
      })
      continue
    }
    nodes.push({
      id: gid, shape: 'kg-group', x: g.x, y: g.y, width: g.w, height: g.h,
      zIndex: 1 + groupDepth(layout.groups, gid),
      attrs: groupAttrs(g.name, color),
      data: { kind: 'group', parent: g.parent || null },
    })
  }
  for (const [nid, n] of Object.entries(layout.nodes)) {
    if (visibleOf(nid) !== nid) continue              // 被折进某个簇里了
    const meta = byId.get(nid)
    const size = sizeFor(meta)
    nodes.push({
      id: nid, shape: 'kg-node', x: n.x, y: n.y,
      width: meta ? size.w : (n.w || NODE_W), height: meta ? size.h : (n.h || NODE_H),
      zIndex: 10,
      attrs: meta ? nodeAttrs(meta, n, colorOf(n.group, meta.field), due.has(nid)) : orphanAttrs(nid),
      data: { kind: 'node', group: n.group || null, orphan: !meta, field: meta?.field || null },
    })
  }
  // 便签与引用卡：只存在 layout.json 里，不参与关系与索引
  for (const note of layout.notes || []) {
    if (note.group && collapsed.has(note.group)) continue
    nodes.push({
      id: `note:${note.id}`, shape: 'kg-note', x: note.x, y: note.y,
      width: note.w || 190, height: note.h || 74, zIndex: 11,
      attrs: noteAttrs(note), data: { kind: 'note', raw: note },
    })
  }
  for (const img of layout.images || []) {
    if (img.group && collapsed.has(img.group)) continue
    nodes.push({
      id: `img:${img.id}`, shape: 'kg-image', x: img.x, y: img.y,
      width: img.w || 320, height: img.h || 200, zIndex: 2,     // 压在节点下面，当背景板用
      attrs: imageAttrs(img), data: { kind: 'image', raw: img },
    })
  }
  for (const ref of layout.refs || []) {
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
      router: style?.router ? { name: style.router } : undefined,
      connector: curved ? { name: 'smooth' } : undefined,
      labels: showLabels ? [edgeLabel(e)] : [],
      data: { kind: 'edge', family: e.family, type: e.type, year: e.year ?? null,
              baseWidth: base.line.strokeWidth },
    })
  }
  for (const [pair, items] of aggregated) {
    const [from, to] = pair.split('->')
    const attrs = aggregateAttrs(items.length)
    edges.push({
      id: `agg:${pair}`, source: from, target: to, zIndex: 4,
      attrs, labels: showLabels ? [aggregateLabel(items.length)] : [],   // 缩小时不画数字，避免满屏小标签
      data: { kind: 'agg', pair, count: items.length, baseWidth: attrs.line.strokeWidth,
              families: [...new Set(items.map((e) => e.family))] },
    })
  }
  return { nodes, edges }
}

// 分流：两端在同一分组（或该组对已展开）的边照常画；跨分组的边按「源分组 → 目标分组」聚合。
// 聚合后一屏里横穿全图的长斜线从几十条降到十几条，点开某一对分组才看明细。
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
    groups.get(pair).push(e)
  }
  return { detail, groups }
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

// 悬停 / 选中某个节点时：它的边亮起来、其余边淡出，网状图才看得清
export function highlightEdges(graph, relatedIds) {
  graph.batchUpdate(() => {
    for (const edge of graph.getEdges()) {
      const base = edge.getData()?.baseWidth || 1
      const on = !relatedIds || relatedIds.has(edge.id)
      edge.attr('line/opacity', on ? 1 : 0.07)
      edge.attr('line/strokeWidth', relatedIds && on ? base * 2 : base)
      edge.setZIndex(relatedIds && on ? 30 : edge.getData()?.kind === 'agg' ? 4 : 5)
    }
  })
}


// 分组被拖动时，X6 已经把子元素一起移了；这里收集所有需要落盘的新坐标
export function movedPositions(cell) {
  const out = []
  const walk = (c) => {
    const pos = c.position()
    out.push({ id: c.id, kind: c.shape === 'kg-group' ? 'group' : 'node', x: Math.round(pos.x), y: Math.round(pos.y) })
    for (const child of c.getChildren() || []) walk(child)
  }
  walk(cell)
  return out
}
