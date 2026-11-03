// 专题画布上手画的东西：图形（框 / 大框 / 文字 / 圆 / 三角 / 菱形 / 圆柱）和自由连线（2026-10-13）。
//
// **只存在专题画布的 layout 里**（`shapes` / `links`），全局图不画——画图工具只开在专题里，
// 全局图保持只有知识点和关系。图形先只是画；升级成知识点后（`node` 指向它）框上的名字和悬停摘要
// 从索引现取，知识仍然只住在 md 里。
import { Graph } from '@antv/x6'
import { FAMILY_STYLE, PALETTE, STATE_FILL, currentTheme, tokens } from './shapes.js'

export const SHAPE_KINDS = [
  { kind: 'rect', name: '矩形', w: 140, h: 60 },
  { kind: 'frame', name: '大框', w: 560, h: 240 },
  { kind: 'text', name: '文字', w: 160, h: 32 },
  { kind: 'ellipse', name: '圆', w: 110, h: 110 },
  { kind: 'triangle', name: '三角', w: 120, h: 100 },
  { kind: 'diamond', name: '菱形', w: 130, h: 90 },
  { kind: 'cylinder', name: '圆柱', w: 110, h: 120 },
]
const SIZE = Object.fromEntries(SHAPE_KINDS.map((k) => [k.kind, k]))

/** 图形的配色只给这一小组：手画的图要的是「分得清」，不是调色盘。 */
export const SHAPE_COLORS = [null, ...PALETTE.slice(0, 6).map((c) => c.key)]

// 从左边图形栏拖进画布时 dataTransfer 里的类型：图形种类 / 知识点 id
export const DRAG_SHAPE = 'application/x-knowrary-shape'
export const DRAG_NODE = 'application/x-knowrary-node'

export const SHAPE_PREFIX = 'shape:'
export const LINK_PREFIX = 'link:'
export const shapeCellId = (sid) => `${SHAPE_PREFIX}${sid}`
export const isDrawnShape = (cell) => !!cell?.shape?.startsWith('kg-draw-')
export const isLink = (cell) => (cell?.getData?.() || {}).kind === 'link'

// 圆柱：顶面一个椭圆 + 侧面和底弧。**只能用贝塞尔，不能用圆弧（A）**——refD 按框的大小缩放路径时
// 圆弧的半径不跟着缩，画出来是个歪杯子（截图核对过）。半个椭圆用一段三次贝塞尔，控制点在 ±4/3·ry
const CYLINDER = 'M 0 10 C 0 -3.33 100 -3.33 100 10 C 100 23.33 0 23.33 0 10 Z '
  + 'M 0 10 L 0 90 C 0 103.33 100 103.33 100 90 L 100 10'

const BODY = {
  rect: { tagName: 'rect', attrs: { refWidth: '100%', refHeight: '100%', rx: 6, ry: 6 } },
  frame: { tagName: 'rect', attrs: { refWidth: '100%', refHeight: '100%', rx: 10, ry: 10, strokeDasharray: '6 4' } },
  // 文字框没有填充也没有描边，pointerEvents: all 让整块都能点中（双击改字、右键）
  text: { tagName: 'rect', attrs: { refWidth: '100%', refHeight: '100%', pointerEvents: 'all' } },
  ellipse: { tagName: 'ellipse', attrs: { refCx: '50%', refCy: '50%', refRx: '50%', refRy: '50%' } },
  triangle: { tagName: 'polygon', attrs: { refPoints: '0,10 5,0 10,10' } },
  diamond: { tagName: 'polygon', attrs: { refPoints: '0,5 5,0 10,5 5,10' } },
  cylinder: { tagName: 'path', attrs: { refD: CYLINDER } },
  // 组合框（2026-10-30）：把几个图形框在一起一起拖的弱框。填充近乎透明、虚线边，
  // 孩子画在它上面（zIndex 更高）；pointerEvents: all 让框内空白也抓得住，拖它 = 拖整组。
  group: { tagName: 'rect', attrs: { refWidth: '100%', refHeight: '100%', rx: 8, ry: 8,
                                     strokeDasharray: '5 4', pointerEvents: 'all' } },
}

export function registerDrawShapes() {
  for (const [kind, body] of Object.entries(BODY)) {
    const frame = kind === 'frame'
    // group 不在 SHAPE_KINDS（不从图形栏画，只由「组合」命令生成），SIZE 里没有它 → 给个兜底尺寸
    const sz = SIZE[kind] || { w: 200, h: 120 }
    Graph.registerNode(`kg-draw-${kind}`, {
      width: sz.w, height: sz.h,
      // badge：审图意见的角标（还没处理的条数），没有意见时不画
      // sqStudy / sqExam：施工图（2026-10-23）——绑定了知识点的框画和全局图同款的学 / 考小方块
      markup: [{ tagName: body.tagName, selector: 'body' }, { tagName: 'text', selector: 'label' },
               { tagName: 'circle', selector: 'badge' }, { tagName: 'text', selector: 'badgeText' },
               { tagName: 'rect', selector: 'sqStudy' }, { tagName: 'rect', selector: 'sqExam' }],
      attrs: {
        body: { ...body.attrs, strokeWidth: 1.4 },
        sqStudy: { x: 10, refY: '100%', refY2: -13, width: 7, height: 7, rx: 2, ry: 2,
                   fill: 'transparent', pointerEvents: 'none' },
        sqExam: { x: 20, refY: '100%', refY2: -13, width: 7, height: 7, rx: 2, ry: 2,
                  fill: 'transparent', pointerEvents: 'none' },
        badge: { r: 9, refX: '100%', refX2: -2, refY: 2, fill: '#d8a838', display: 'none', cursor: 'pointer' },
        badgeText: { refX: '100%', refX2: -2, refY: 2, fontSize: 10.5, fontWeight: 700, fill: '#fff',
                     textAnchor: 'middle', textVerticalAnchor: 'middle', display: 'none', pointerEvents: 'none' },
        // 大框的名字在左上角（里面要放小框）；别的居中
        label: frame
          ? { refX: 14, refY: 16, textAnchor: 'start', textVerticalAnchor: 'middle', fontSize: 14, fontWeight: 600 }
          : { refX: '50%', refY: kind === 'triangle' ? '66%' : '50%', textAnchor: 'middle', textVerticalAnchor: 'middle',
              fontSize: 13, textWrap: { width: -16, height: -8, ellipsis: true } },
      },
    }, true)
  }
}

function colorOf(key) {
  return PALETTE.find((c) => c.key === key) || null
}

/** 图形的样式：没写颜色的按主题走（暗色模式自动换），写了的取那一套色板。
 * 施工图（2026-10-23）：绑定了知识点的框**常显**学 / 考双态小方块；
 * 还没学出来的（索引里没有、或只有壳）整框淡显——竣工图上一眼看出施工到哪了。 */
export function shapeAttrs(shape, meta = null, state = null) {
  const dark = currentTheme() === 'dark'
  const tone = colorOf(shape.style?.fill)
  const bare = shape.kind === 'text'
  const isGroup = shape.kind === 'group'
  const fill = bare ? 'transparent'
    : isGroup ? (dark ? '#ffffff0a' : '#00000005')
    : tone ? (dark ? `${tone.line}33` : tone.fill)
    : shape.kind === 'frame' ? (dark ? '#ffffff08' : '#00000005') : tokens().groupFill
  const stroke = bare ? 'transparent'
    : isGroup ? (dark ? '#6b7683' : '#9aa7b4')
    : tone ? tone.line : (dark ? '#6b7683' : '#8a99a8')
  // 组合框不写字（它只是把别的图形框起来）；别的照旧
  const text = isGroup ? '' : (shape.text || meta?.name || (shape.node ? shape.node : bare ? '（双击写字）' : ''))
  const unlearned = !!shape.node && (!meta || meta.stub)
  return {
    body: { fill, stroke, strokeWidth: shape.node ? 2.2 : 1.4,     // 升级成知识点的框描边加粗，一眼分得出
            fillOpacity: unlearned ? 0.5 : 1, strokeOpacity: unlearned ? 0.45 : 1,
            ...(unlearned ? { strokeDasharray: '4 3' } : {}) },
    label: { text, fill: shape.style?.color || (tone ? tone.text : tokens().title),
             opacity: unlearned ? 0.55 : 1,
             fontSize: shape.style?.size || (shape.kind === 'frame' ? 14 : 13),
             fontWeight: shape.style?.bold || shape.kind === 'frame' ? 600 : 400 },
    sqStudy: { fill: shape.node && !bare && state ? (STATE_FILL[state.study] || 'transparent') : 'transparent' },
    sqExam: { fill: shape.node && !bare && state ? (STATE_FILL[state.exam] || 'transparent') : 'transparent' },
  }
}

/** 悬停要显示的字：自己写的说明优先，升级成知识点的没写就用它的一句话摘要。 */
export function tipOf(shape, meta = null) {
  return shape.tip || meta?.desc || ''
}

/**
 * 一个方块（手画图形或节点框）在某个版本下的状态（版本演进 A 方案，2026-10-27）：
 *   `present` 实显 ·  `appear` 这一版新增 ·  `ghost` 区间不覆盖当前版，淡成虚影。
 * `order` 是中心 `版本` 轴，box 的 `since`/`until` 落在轴上比下标决定区间。
 * 没选版本 / 没有版本轴 → 一律 present；`since`/`until` 没写 = 那一边开口；
 * 写了却不在轴上（打错字）当没写处理，不误伤。
 */
export function versionStateOf(box, ver, order) {
  if (!ver || !Array.isArray(order) || !order.length) return 'present'
  const vi = order.indexOf(ver)
  if (vi < 0) return 'present'
  const si = box?.since ? order.indexOf(box.since) : -1
  const ui = box?.until ? order.indexOf(box.until) : -1
  if (si >= 0 && vi < si) return 'ghost'
  if (ui >= 0 && vi > ui) return 'ghost'
  return si >= 0 && vi === si ? 'appear' : 'present'
}

/**
 * 把一段正文切成「前言 + 各版本小节」（细节抽屉按版本高亮，2026-10-27）。
 * 约定：正文里 `## 0.12` / `## 2.6` 这样的二级标题，标题名对上版本轴 `order` 的就是版本小节；
 * 第一个版本标题之前的算前言（常驻、永远显示）；对不上版本轴的二级标题留在它所在的小节里，
 * 当普通正文走（标题行原样保留，交给 Markdown 渲染）。版本小节的标题行本身摘掉——
 * 抽屉自己画版本条，不重复显示。返回 `[{ ver, text }]`，前言的 `ver` 为 null。
 */
export function splitByVersion(body, order) {
  const axis = new Set(Array.isArray(order) ? order.map((v) => String(v).trim()) : [])
  const blocks = []
  let cur = { ver: null, lines: [] }
  for (const line of String(body || '').split('\n')) {
    const m = line.match(/^##\s+(.+?)\s*$/)
    if (m && axis.has(m[1].trim())) {
      blocks.push(cur)
      cur = { ver: m[1].trim(), lines: [] }   // 标题行不进正文，抽屉自己标版本
    } else {
      cur.lines.push(line)
    }
  }
  blocks.push(cur)
  return blocks
    .map((b) => ({ ver: b.ver, text: b.lines.join('\n').trim() }))
    .filter((b) => b.ver || b.text)      // 空前言（正文一上来就是版本小节）丢掉
}

function depthOf(shapes, sid) {
  let n = 0
  let cur = shapes[sid]?.parent
  while (cur && n < 20) { n += 1; cur = shapes[cur]?.parent }
  return n
}

const PICKED = '#d8a838'      // 选中的图形 / 连线描金边，和路径高亮同一支金色

/** 图形和连线的 cell。大框压在下面（越外层越靠下），别的图形和知识点在上面。
 * `picked` = 当前选中的 { shape, link }；`states` = {节点 id: {study, exam}}，施工图染色用。 */
export function drawCells(layout, index, picked = {}, states = {}) {
  const byId = new Map((index?.nodes || []).map((n) => [n.id, n]))
  const shapes = layout?.shapes || {}
  const nodes = Object.entries(shapes).map(([sid, s]) => {
    const meta = s.node ? byId.get(s.node) : null
    const attrs = shapeAttrs(s, meta, s.node ? states[s.node] : null)
    // 选中描金边：单选（primary）或多选集合里的都算（组合前的框选）
    if (picked.shape === sid || picked.shapes?.has?.(sid)) Object.assign(attrs.body, { stroke: PICKED, strokeWidth: 2.6 })
    const open = picked.reviews?.[sid] || 0
    if (open) Object.assign(attrs, { badge: { display: 'block' }, badgeText: { display: 'block', text: String(open) } })
    return {
      id: shapeCellId(sid), shape: `kg-draw-${s.kind}`, x: s.x, y: s.y, width: s.w, height: s.h,
      // 大框和组合框都压在孩子下面（越外层越靠下）；别的图形和知识点在上面
      zIndex: s.kind === 'frame' || s.kind === 'group' ? 1 + depthOf(shapes, sid) : 20,
      attrs,
      data: { kind: 'shape', sid, group: s.parent ? shapeCellId(s.parent) : null, tip: tipOf(s, meta) },
    }
  })
  const indexEdges = new Map((index?.edges || []).map((e) => [e.id, e]))
  const edges = Object.entries(layout?.links || {}).map(([lid, l]) => linkCell(lid, l, indexEdges, picked))
  return { nodes, edges }
}

/**
 * 一条连线的 cell。写成过 md 关系的（`edge`）按那条关系的族画、线上默认写类型；
 * 那条关系后来在 md 里没了，就画成红色虚线并写明——画布上的线不能冒充一条已经不存在的知识。
 */
function linkCell(lid, l, indexEdges, picked) {
  const real = l.edge ? indexEdges.get(l.edge) : null
  const gone = !!l.edge && !real
  const fam = real ? FAMILY_STYLE[real.family] || FAMILY_STYLE['弱关联'] : null
  const stroke = picked.link === lid ? PICKED : gone ? '#c0392b'
    : fam ? fam.stroke : colorOf(l.style?.stroke)?.line || tokens().edgeLabel
  const text = gone ? `${l.text || l.edge.split('#')[1]}（md 里这条关系已不在）` : l.text || (real ? real.type : '')
  return {
    id: `${LINK_PREFIX}${lid}`, source: endCell(l.source), target: endCell(l.target), zIndex: 21,
    vertices: l.vertices || [],
    attrs: { line: { stroke, strokeWidth: picked.link === lid ? 2.6 : fam ? Math.max(fam.strokeWidth, 1.6) : 1.6,
                     strokeDasharray: gone ? '5 4' : fam?.strokeDasharray || null,
                     targetMarker: { name: 'block', width: 8, height: 6 } } },
    labels: text ? [{ attrs: { label: { text, fill: gone ? '#c0392b' : tokens().title, fontSize: 12 },
                               body: { fill: tokens().labelBg, stroke: 'none' } } }] : [],
    data: { kind: 'link', lid },
  }
}

/** 连线两端各是哪个知识点：连的是知识点卡片，或者升级过的框。任一端还只是图形就返回 null。 */
export function linkNodes(layout, lid) {
  const l = layout?.links?.[lid]
  if (!l) return null
  const nodeOf = (end) => (end.kind === 'node' ? end.id : layout.shapes?.[end.id]?.node || null)
  const from = nodeOf(l.source)
  const to = nodeOf(l.target)
  return from && to && from !== to ? { from, to } : null
}

/** 刚写进 md 的那条关系在索引里的 id：反向写法会被归一（「属于」写进去是「包含」倒过来），所以按两头 + 原文类型找。 */
export function writtenEdgeId(index, from, to, relation) {
  const e = (index?.edges || []).find((x) => ((x.source === from && x.target === to) || (x.source === to && x.target === from))
    && (x.type === relation || (x.raw_types || []).includes(relation)))
  return e?.id || null
}

const endCell = (end) => (end.kind === 'shape' ? shapeCellId(end.id) : end.id)

/** 画布上某个 cell 能不能当连线的一端，能的话是哪种。 */
export function linkEndOf(cell) {
  if (isDrawnShape(cell)) return { kind: 'shape', id: cell.getData().sid }
  if (cell?.shape === 'kg-node' && !cell.getData()?.orphan) return { kind: 'node', id: cell.id }
  return null
}

/** 一个图形连同它套着的所有小框（删大框时一起删，挪大框时一起挪）。 */
export function withDescendants(shapes, sid) {
  const out = [sid]
  for (let i = 0; i < out.length; i += 1) {
    for (const [id, s] of Object.entries(shapes)) if (s.parent === out[i] && !out.includes(id)) out.push(id)
  }
  return out
}

/** 框选命中：矩形框（图坐标，两角 x1/y1/x2/y2，顺序随意）碰到的所有图形 id（相交即选，不要求整个罩住）。 */
export function shapesInBox(shapes, box) {
  const x1 = Math.min(box.x1, box.x2); const x2 = Math.max(box.x1, box.x2)
  const y1 = Math.min(box.y1, box.y2); const y2 = Math.max(box.y1, box.y2)
  return Object.entries(shapes || {})
    .filter(([, s]) => s.x < x2 && s.x + s.w > x1 && s.y < y2 && s.y + s.h > y1)
    .map(([id]) => id)
}

export const GROUP_PAD = 14

/** 把一组图形框起来的外接框（四周留点内边距），组合框就放在这个位置大小。 */
export function groupBox(shapes, ids, pad = GROUP_PAD) {
  const boxes = ids.map((id) => shapes[id]).filter(Boolean)
  if (!boxes.length) return null
  const x0 = Math.min(...boxes.map((s) => s.x))
  const y0 = Math.min(...boxes.map((s) => s.y))
  const x1 = Math.max(...boxes.map((s) => s.x + s.w))
  const y1 = Math.max(...boxes.map((s) => s.y + s.h))
  return { x: Math.round(x0 - pad), y: Math.round(y0 - pad),
           w: Math.round(x1 - x0 + pad * 2), h: Math.round(y1 - y0 + pad * 2) }
}

/** 这组图形有没有共同的父：有就把新组合框挂到那个父下（保持原来的嵌套），没有就挂到顶层。 */
export function commonParent(shapes, ids) {
  const parents = new Set(ids.map((id) => shapes[id]?.parent || null))
  return parents.size === 1 ? [...parents][0] : null
}

/** 解组补丁：删掉组合框本身，里面的图形改挂到组合框原来的父上（留在原位，不删）。 */
export function ungroupPatch(layout, gid) {
  const shapes = layout.shapes || {}
  const host = shapes[gid]?.parent || null
  const kids = Object.entries(shapes).filter(([, s]) => s.parent === gid)
  return { shapes: { [gid]: null, ...Object.fromEntries(kids.map(([id]) => [id, { parent: host }])) } }
}

/** 删图形的补丁：它和它套着的小框，以及连着这些图形的线（悬空的线服务端会拒）。 */
export function removalPatch(layout, sid) {
  const gone = new Set(withDescendants(layout.shapes || {}, sid))
  const links = Object.entries(layout.links || {})
    .filter(([, l]) => [l.source, l.target].some((e) => e.kind === 'shape' && gone.has(e.id)))
  return { shapes: Object.fromEntries([...gone].map((id) => [id, null])),
           links: Object.fromEntries(links.map(([id]) => [id, null])) }
}

/** 从画布上拿掉一个知识点时，连着它的线也得一起拿掉。 */
export function linksOfNode(layout, nid) {
  return Object.entries(layout.links || {})
    .filter(([, l]) => [l.source, l.target].some((e) => e.kind === 'node' && e.id === nid))
    .map(([id]) => id)
}

export function newShape(kind, at) {
  const { w, h } = SIZE[kind] || SIZE.rect
  return { kind, x: Math.round(at.x - w / 2), y: Math.round(at.y - h / 2), w, h, text: '', tip: '' }
}

/** 悬停说明：一个跟着鼠标的浮层。X6 的 SVG 里塞 <title> 只有系统那个小黄条，样式和延迟都管不了。 */
export function bindTips(graph) {
  const tip = document.createElement('div')
  tip.className = 'draw-tip'
  tip.hidden = true
  graph.container.appendChild(tip)
  graph.on('node:mouseenter', ({ node, e }) => {
    const text = isDrawnShape(node) ? node.getData()?.tip : ''
    if (!text) return
    tip.textContent = text
    tip.hidden = false
    place(e)
  })
  graph.on('node:mousemove', ({ e }) => { if (!tip.hidden) place(e) })
  graph.on('node:mouseleave', () => { tip.hidden = true })
  function place(e) {
    const box = graph.container.getBoundingClientRect()
    tip.style.left = `${e.clientX - box.left + 14}px`
    tip.style.top = `${e.clientY - box.top + 14}px`
  }
}

// ---- 框升级成知识点（第 3 步）----

const PART_TYPES = ['部件', '包含']

function hasPart(index, parent, child) {
  return (index?.edges || []).some((e) => e.source === parent && e.target === child && PART_TYPES.includes(e.type))
}

/** 大框里直接套着的、已经升级成知识点的小框，按从左到右（同一列从上到下）排。 */
export function boundChildren(layout, sid) {
  return Object.values(layout?.shapes || {})
    .filter((s) => s.parent === sid && s.node)
    .sort((a, b) => a.x - b.x || a.y - b.y)
    .map((s) => s.node)
}

/**
 * 把图形 `sid` 升级成 / 绑定到知识点 `nodeId` 时，md 里要跟着补的变更（不含 create_node 本身）。
 *
 * - 它是大框：自己当这个专题的中心（`专题: <画布名>`，`isNew` 时由 create_node 带上，这里不重复），
 *   里面已经升级过的小框按从左到右各补一条 `部件::`——**只补缺的，不重排已有的**（重排会丢边上的注释和年份）。
 * - 它套在一个已经升级过的大框里：那个大框的知识点补一条 `部件:: 它`。
 */
export function upgradeChanges({ layout, index, sid, nodeId, topic, isNew = false }) {
  const shapes = layout?.shapes || {}
  const shape = shapes[sid]
  const changes = []
  if (!shape) return changes
  const meta = (index?.nodes || []).find((n) => n.id === nodeId)
  if (shape.kind === 'frame') {
    if (!isNew && meta?.topic !== topic) {
      changes.push({ type: 'update_frontmatter', source: nodeId, fields: { 专题: topic } })
    }
    for (const child of boundChildren(layout, sid)) {
      if (child !== nodeId && !hasPart(index, nodeId, child)) {
        changes.push({ type: 'add_edge', source: nodeId, relation: '部件', target: child })
      }
    }
  }
  const host = shape.parent ? shapes[shape.parent]?.node : null
  if (host && host !== nodeId && !hasPart(index, host, nodeId)) {
    changes.push({ type: 'add_edge', source: host, relation: '部件', target: nodeId })
  }
  return changes
}

/**
 * 原地改字（照 draw.io）：在图形 / 连线标签上方盖一个文本框。
 * Enter 确定、Shift+Enter 换行、Esc 取消、点别处也算确定。`box` 是图坐标里的框。
 */
export function editInPlace(graph, box, text, onDone) {
  const host = graph.container
  const r = graph.localToGraph(box)
  const zoom = graph.zoom()
  const area = document.createElement('textarea')
  area.className = 'draw-inline'
  area.value = text || ''
  Object.assign(area.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${Math.max(r.width, 120)}px`,
                              height: `${Math.max(r.height, 32)}px`, fontSize: `${Math.max(12, 13 * zoom)}px` })
  let settled = false
  const finish = (save) => {
    if (settled) return
    settled = true
    const value = area.value
    area.remove()
    if (save && value !== (text || '')) onDone(value)
  }
  area.addEventListener('keydown', (e) => {
    e.stopPropagation()                         // 别让画布的快捷键（Delete / 数字键）吃掉打字
    if (e.key === 'Escape') finish(false)
    else if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); finish(true) }
  })
  area.addEventListener('mousedown', (e) => e.stopPropagation())   // 在框里选字不该拖动画布
  area.addEventListener('blur', () => finish(true))
  host.appendChild(area)
  area.focus()
  area.select()
  return area
}

/**
 * 删一条索引边要发的 remove_edge：**按它实际写在哪个文件里删**。
 *
 * 索引里的边是归一过的——「子 属于 父」写在子的 md 里，索引记成「父 包含 子」。
 * 以前直接拿归一后的 源 / 类型 / 目标 去删，会去父的 md 里找「包含:: 子」，找不到就报「要删除的关系不存在」。
 * 两边都写了（互逆重复维护）就两边都删。（2026-10-13 专题画布「从 md 删掉这条关系」时发现，全局图同样受影响）
 */
export function removeEdgeChanges(edge, index) {
  const owner = new Map((index?.nodes || []).filter((n) => n.path).map((n) => [n.path, n.id]))
  const inverse = (edge.raw_types || []).find((t) => t !== edge.type) || edge.type
  const out = (edge.declared_in || []).map((file) => (owner.get(file) === edge.target
    ? { type: 'remove_edge', source: edge.target, relation: inverse, target: edge.source }
    : { type: 'remove_edge', source: edge.source, relation: edge.type, target: edge.target }))
  return out.length ? out : [{ type: 'remove_edge', source: edge.source, relation: edge.type, target: edge.target }]
}

// ---- 一键初稿与侧栏建议（第 5 步）：方位由服务端按关系算（/api/topics/<专题>/draft），这里只管摆 ----

export const DRAFT_SIDES = [
  { key: 'up', name: '上 · 谁在用它' }, { key: 'down', name: '下 · 它跑在什么上' },
  { key: 'left', name: '左 · 前身' }, { key: 'right', name: '右 · 后继' },
  { key: 'compare', name: '对照' }, { key: 'pending', name: '其他相关（待你决定）' },
]
const CARD_W = 160
const CARD_H = 60
const PART_W = 140
const PART_GAP = 10
const ROW = 130        // 上下每跳一行
const COL = 230        // 左右每跳一列

/** 已经在这张图上的知识点：卡片，或者升级过的框。 */
export function onCanvas(layout) {
  return new Set([...Object.keys(layout?.nodes || {}),
                  ...Object.values(layout?.shapes || {}).map((s) => s.node).filter(Boolean)])
}

/** 侧栏：按方位分组的、还没上图的相关点。 */
export function suggestions(layout, draft) {
  const placed = onCanvas(layout)
  const parts = (draft?.parts || []).map((id) => ({ id, hop: 1, via: draft.center }))
  return [{ key: 'parts', name: '中 · 部件（方块）', items: parts }, ...DRAFT_SIDES.map((side) => ({ ...side, items: draft?.[side.key] || [] }))]
    .map((g) => ({ ...g, items: g.items.filter((it) => !placed.has(it.id)) }))
    .filter((g) => g.items.length)
}

/**
 * 一键初稿：只摆还没上图的，已经摆好的一个都不动。
 * 中心没有对应的大框就建一个（绑定中心），方块按 `部件::` 的顺序排在框里；上下按跳数一行一行往外，
 * 左右按跳数一列一列往外（分叉在同一列里上下错开），对照放在右边那一列下面。
 * 返回 { shapes, nodes }：要新增的图形和卡片（id → 条目），调用方当补丁写。`newId(prefix)` 生成图形 id。
 */
export function draftPlan(layout, draft, at, newId) {
  const placed = onCanvas(layout)
  const out = { shapes: {}, nodes: {} }
  const frame = centerFrame(layout, draft, at, newId, out)
  placeParts(layout, draft, frame, placed, newId, out)
  const cx = frame.x + frame.w / 2
  const cy = frame.y + frame.h / 2
  const put = (id, x, y) => {
    if (placed.has(id) || out.nodes[id]) return
    out.nodes[id] = { x: Math.round(x - CARD_W / 2), y: Math.round(y - CARD_H / 2) }
  }
  rows(draft?.up).forEach((ids, k) => spread(ids, cx, frame.y - (k + 1) * ROW + 10, put))
  rows(draft?.down).forEach((ids, k) => spread(ids, cx, frame.y + frame.h + (k + 1) * ROW - 40, put))
  rows(draft?.right).forEach((ids, k) => stack(ids, frame.x + frame.w + 60 + CARD_W / 2 + k * COL, cy, put))
  rows(draft?.left).forEach((ids, k) => stack(ids, frame.x - 60 - CARD_W / 2 - k * COL, cy, put))
  ;(draft?.compare || []).forEach((it, i) => put(it.id, frame.x + frame.w + 60 + CARD_W / 2 + i * (CARD_W + 30), cy + 130))
  return out
}

/** 中心对应的大框：已经有就用它（框的位置大小以画布为准），没有就在 `at` 建一个绑定中心的。 */
function centerFrame(layout, draft, at, newId, out) {
  const hit = Object.entries(layout?.shapes || {}).find(([, s]) => s.node === draft.center)
  if (hit) return { sid: hit[0], ...hit[1] }
  const n = Math.max((draft.parts || []).length, 3)
  const w = n * (PART_W + PART_GAP) + 30
  const h = 170
  const sid = newId('s')
  const frame = { kind: 'frame', x: Math.round(at.x - w / 2), y: Math.round(at.y - h / 2), w, h,
                  text: '', tip: '', parent: null, node: draft.center }
  out.shapes[sid] = frame
  return { sid, ...frame }
}

/** 方块：大框里还没有的部件，接在框里已有的小框后面排一排；排不下就把框撑宽。 */
function placeParts(layout, draft, frame, placed, newId, out) {
  const kids = Object.values(layout?.shapes || {}).filter((s) => s.parent === frame.sid)
  let x = kids.length ? Math.max(...kids.map((s) => s.x + s.w)) + PART_GAP : frame.x + 15
  for (const part of draft.parts || []) {
    if (placed.has(part)) continue
    out.shapes[newId('s')] = { kind: 'rect', x, y: frame.y + 60, w: PART_W, h: 70, text: '', tip: '',
                               parent: frame.sid, node: part }
    x += PART_W + PART_GAP
  }
  const need = x + 5 - frame.x
  if (need > frame.w) {
    const sid = frame.sid
    out.shapes[sid] = { ...(out.shapes[sid] || {}), w: need }
    frame.w = need
  }
}

/** 按跳数分行：[[第 1 跳的 id…], [第 2 跳…], …] */
function rows(items) {
  const out = []
  for (const it of items || []) (out[it.hop - 1] ||= []).push(it.id)
  return out.filter(Boolean)
}

/** 一行横着摆开，以 cx 居中。 */
function spread(ids, cx, y, put) {
  const step = CARD_W + 30
  ids.forEach((id, i) => put(id, cx + (i - (ids.length - 1) / 2) * step, y))
}

/** 一列竖着摆开（分叉），以 cy 居中。 */
function stack(ids, x, cy, put) {
  const step = CARD_H + 30
  ids.forEach((id, i) => put(id, x, cy + (i - (ids.length - 1) / 2) * step))
}

/** 每个框上还没处理的审图意见条数（角标用）：意见指着它，或者「缺了什么」要放进它这个大框。 */
export function openReviewCounts(review) {
  const out = {}
  for (const it of review?.items || []) {
    const sid = it.status === 'open' ? it.target || (it.kind === 'link' && it.fix?.from?.kind === 'shape' ? it.fix.from.id : null) : null
    if (sid) out[sid] = (out[sid] || 0) + 1
  }
  return out
}
