// 组内重排：每个分组自己挑一种摆法，整张图仍是分组之间自由摆。
//
// 这是 Combo 式布局的实质——图谱里不同的域结构天然不一样：
// 「计算机史」是一棵层级树，脑图最省地方；「NLP」是一团互相引用的网，力导向才看得出簇；
// 刚导入还没理清关系的域，网格最整齐。全图统一跑一种算法，总有一半的域被摆坏。
//
// 只动**直属于这个组的节点**：子域有自己的框和自己的摆法，不该被父域的算法推着走。
import { NODE_H, NODE_W, sizeFor } from './shapes'
import { runMindmap } from './layouts'

const STRUCT_FAMILY = '结构'
const TOP = 44          // 组名占的高度，节点从这条线以下开始摆
const PAD = 24
const CELL = { w: NODE_W + 40, h: NODE_H + 28 }
const COLS_MAX = 5

export const GROUP_LAYOUTS = {
  mindmap: { label: '脑图（按结构边分层）', run: mindmapInside },
  force: { label: '力导向（按连接松紧聚散）', run: forceInside },
  grid: { label: '网格（按重要性排齐）', run: gridInside },
}

/** 这个组直属的节点 id（不含子域里的）。 */
export function membersOf(layout, gid) {
  return Object.entries(layout.nodes || {}).filter(([, n]) => n.group === gid).map(([id]) => id)
}

/** 组里有没有子域：有的话不给重排，子域各自摆自己的，父域只管框。 */
export function hasSubGroups(layout, gid) {
  return Object.values(layout.groups || {}).some((g) => g.parent === gid)
}

/**
 * 算出某个组重排后的样子。返回 { nodes: { id: {x, y} }（画布绝对坐标）, box: { w, h } }。
 * 调用方负责落盘：节点坐标 + 分组框的新尺寸。
 */
export function layoutGroup(index, layout, gid, kind) {
  const spec = GROUP_LAYOUTS[kind]
  const box = layout.groups?.[gid]
  const ids = membersOf(layout, gid)
  if (!spec || !box || ids.length < 2) return null
  const byId = new Map(index.nodes.map((n) => [n.id, n]))
  const inside = index.edges.filter((e) => ids.includes(e.source) && ids.includes(e.target))
  const placed = spec.run(ids, inside, byId)
  if (!placed) return null
  // 算法各自从 (0,0) 开始摆，这里统一平移到框内并量出框该多大
  const w = Math.max(...placed.map((p) => p.x + (p.w || NODE_W)))
  const h = Math.max(...placed.map((p) => p.y + (p.h || NODE_H)))
  const nodes = {}
  for (const p of placed) nodes[p.id] = { x: Math.round(box.x + PAD + p.x), y: Math.round(box.y + TOP + p.y) }
  return { nodes, box: { w: Math.round(w + 2 * PAD), h: Math.round(h + TOP + PAD) } }
}

function sizeOf(byId, id) {
  const s = sizeFor(byId.get(id))
  return { w: s?.w || NODE_W, h: s?.h || NODE_H }
}

/** 网格：按权重从高到低填格子，读图时左上角就是这个域最重要的几个点。 */
function gridInside(ids, _edges, byId) {
  const sorted = [...ids].sort((a, b) => (byId.get(b)?.weight || 0) - (byId.get(a)?.weight || 0))
  const cols = Math.max(1, Math.min(COLS_MAX, Math.ceil(Math.sqrt(sorted.length))))
  return sorted.map((id, i) => ({ id, x: (i % cols) * CELL.w, y: Math.floor(i / cols) * CELL.h,
                                  ...sizeOf(byId, id) }))
}

/**
 * 脑图：用组内的结构边搭森林。多父只认度数最高的那个父，和全图脑图同一套规矩。
 * 组里可能有好几棵树（甚至全是孤点），逐棵往下摞。
 */
function mindmapInside(ids, edges, byId) {
  const set = new Set(ids)
  const children = new Map()
  const parents = new Map()
  for (const e of edges) {
    if (e.family !== STRUCT_FAMILY) continue
    if (!children.has(e.source)) children.set(e.source, [])
    children.get(e.source).push(e.target)
    if (!parents.has(e.target)) parents.set(e.target, [])
    parents.get(e.target).push(e.source)
  }
  if (!children.size) return null              // 组内没有结构边，脑图无从谈起
  const mainParent = new Map()
  for (const [child, ps] of parents) {
    mainParent.set(child, [...ps].sort(
      (a, b) => (byId.get(b)?.degree || 0) - (byId.get(a)?.degree || 0) || a.localeCompare(b))[0])
  }
  const used = new Set()
  const toTree = (id) => {
    if (used.has(id)) return null
    used.add(id)
    const kids = (children.get(id) || []).filter((c) => mainParent.get(c) === id && set.has(c))
      .sort().map(toTree).filter(Boolean)
    return { id, name: byId.get(id)?.name || id, children: kids }
  }
  const roots = [...set].filter((id) => children.has(id) && !mainParent.has(id)).sort()
  const out = []
  let y = 0
  for (const root of roots) {
    const tree = toTree(root)
    if (!tree) continue
    const laid = runMindmap(tree)
    for (const p of laid.points) {
      out.push({ id: p.id, x: p.x - laid.minX, y: y + (p.y - laid.minY), ...sizeOf(byId, p.id) })
    }
    y += laid.h + 40
  }
  // 没进树的（孤点、环里的）在下面补一行，一个都不能丢
  const rest = [...set].filter((id) => !used.has(id)).sort()
  const cols = Math.max(1, Math.min(COLS_MAX, rest.length))
  rest.forEach((id, i) => out.push({ id, x: (i % cols) * CELL.w,
                                     y: y + Math.floor(i / cols) * CELL.h, ...sizeOf(byId, id) }))
  return out.length ? out : null
}

/**
 * 力导向（Fruchterman-Reingold）：连得紧的互相拉近，没关系的互相推开。
 *
 * 自己写而不是引第三方布局包：这里只有几十个点，算法本体二十行，
 * 而且我们要的就是"跑完给一组坐标"，不需要一个带自己渲染循环的布局引擎。
 * 迭代是确定性的（初始位置按 id 排序放在圆周上），同一个组重排两次结果一样。
 *
 * 必须带向心力：教科书版 FR 假定图是连通的，靠边把点拉住。知识图谱里一个域
 * 常常有大半节点还没连上关系（甚至整个 vault 正在重连、一条边都没有），
 * 纯斥力会让它们一路飞出去——实测 15 个孤点跑完框有 16000px 宽。
 * 向心力强度按"n 个点铺成一张合适大小的圆盘"反解出来，规模变了也不用重调。
 */
function forceInside(ids, edges, byId) {
  const n = ids.length
  const area = n * CELL.w * CELL.h
  const k = Math.sqrt(area / n)                  // 理想边长
  const sorted = [...ids].sort()
  const pos = new Map(sorted.map((id, i) => {
    const a = (2 * Math.PI * i) / n
    return [id, { x: Math.cos(a) * k * Math.sqrt(n) / 2, y: Math.sin(a) * k * Math.sqrt(n) / 2 }]
  }))
  const links = edges.filter((e) => e.source !== e.target)
  const radius = (k * Math.sqrt(n)) / 2               // 目标圆盘半径：面积 ≈ n 个格子
  const gravity = (n * k * k) / (radius * radius)     // 盘边上向心力 ≈ 其余点的斥力合力
  const ITER = 300
  for (let step = 0; step < ITER; step++) {
    const temp = k * (1 - step / ITER)            // 退火：越到后面挪得越小，最后收敛
    const disp = new Map(sorted.map((id) => [id, { x: 0, y: 0 }]))
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const [a, b] = [pos.get(sorted[i]), pos.get(sorted[j])]
        let dx = a.x - b.x
        let dy = a.y - b.y
        let d = Math.hypot(dx, dy)
        if (d < 0.01) { dx = (i - j) * 0.01; dy = 0.01; d = 0.014 }   // 完全重合就随便掰开一点
        const rep = (k * k) / d
        const da = disp.get(sorted[i])
        const db = disp.get(sorted[j])
        da.x += (dx / d) * rep; da.y += (dy / d) * rep
        db.x -= (dx / d) * rep; db.y -= (dy / d) * rep
      }
    }
    for (const e of links) {
      const [a, b] = [pos.get(e.source), pos.get(e.target)]
      if (!a || !b) continue
      const dx = a.x - b.x
      const dy = a.y - b.y
      const d = Math.hypot(dx, dy) || 0.01
      const att = (d * d) / k
      const da = disp.get(e.source)
      const db = disp.get(e.target)
      da.x -= (dx / d) * att; da.y -= (dy / d) * att
      db.x += (dx / d) * att; db.y += (dy / d) * att
    }
    for (const id of sorted) {
      const d = disp.get(id)
      const p0 = pos.get(id)
      d.x -= p0.x * gravity                            // 向心：把没有边拉着的点收回盘内
      d.y -= p0.y * gravity
      const len = Math.hypot(d.x, d.y) || 1
      const p = pos.get(id)
      p.x += (d.x / len) * Math.min(len, temp)
      p.y += (d.y / len) * Math.min(len, temp)
    }
  }
  // 力导向出来的是点，画布上是卡片：按各自尺寸从中心退回左上角，再整体平移到第一象限
  const boxes = sorted.map((id) => {
    const size = sizeOf(byId, id)
    const p = pos.get(id)
    return { id, x: p.x - size.w / 2, y: p.y - size.h / 2, ...size }
  })
  relaxOverlaps(boxes)
  const minX = Math.min(...boxes.map((b) => b.x))
  const minY = Math.min(...boxes.map((b) => b.y))
  return boxes.map((b) => ({ ...b, x: b.x - minX, y: b.y - minY }))
}

const GAP = 16   // 卡片之间至少留这么多空

/**
 * 力导向把节点当质点算，画布上它们是有宽高的卡片，收敛后常常压在一起。
 * 这里按矩形重叠量把它们推开：只推最小的那一边，布局的大形状不会被改掉。
 */
function relaxOverlaps(boxes, rounds = 60) {
  for (let r = 0; r < rounds; r++) {
    let moved = false
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i]
        const b = boxes[j]
        const ox = (a.w + b.w) / 2 + GAP - Math.abs((a.x + a.w / 2) - (b.x + b.w / 2))
        const oy = (a.h + b.h) / 2 + GAP - Math.abs((a.y + a.h / 2) - (b.y + b.h / 2))
        if (ox <= 0 || oy <= 0) continue
        moved = true
        if (ox < oy) {
          const push = (ox / 2) * ((a.x <= b.x) ? -1 : 1)
          a.x += push
          b.x -= push
        } else {
          const push = (oy / 2) * ((a.y <= b.y) ? -1 : 1)
          a.y += push
          b.y -= push
        }
      }
    }
    if (!moved) break
  }
}
