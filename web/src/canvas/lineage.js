// 谱系树：只画**演化族**（演化为 / 扩展为 / 被激活），自下而上长。
//
// 和历史视图的分工：历史视图的 X 轴锁死在年份上，回答"什么时候"；
// 谱系树回答"从什么长出什么"——同一年冒出来的三个分支在时间轴上挤成一列，
// 在谱系树上才看得出它们是三条独立的枝。所以坐标轴不一样，不是同一张图的两种皮肤。
//
// 三条规矩：
// 1. **只认演化族**。对照 / 依赖 / 结构进来就不是谱系了，是整张图。
// 2. **枝丫粗细 = 这条枝上挂着多少东西**（可达节点数），一眼看出主干在哪。
// 3. **有环就断环**，并且报出来——图上真的有反向边（诊断里那 13 处环），
//    不断环这里会死循环。断掉的边不画，但要让人知道断了哪几条。
import { NODE_H, NODE_W } from './shapes'

export const FAMILY = '演化'
export const LEVEL_H = 150         // 一层多高
export const COL_GAP = 44          // 同一层两个点之间至少留多少
export const COMPONENT_GAP = 120   // 两块互不相连的树之间留多少
export const MIN_W = 1.6           // 最细的枝
export const MAX_W = 9             // 最粗的枝

/** 演化族的边 + 被它们连起来的点。孤点不进树——谱系树讲的是"谁长出谁"。 */
export function lineageGraph(index) {
  const edges = (index.edges || []).filter((e) => e.family === FAMILY)
  const ids = new Set()
  for (const e of edges) { ids.add(e.source); ids.add(e.target) }
  const nodes = (index.nodes || []).filter((n) => ids.has(n.id) && !n.virtual)
  const known = new Set(nodes.map((n) => n.id))
  return { nodes, edges: edges.filter((e) => known.has(e.source) && known.has(e.target)) }
}

/**
 * 断环：按深度优先走一遍，指回"正在走的路径"上的边就是回边，丢掉。
 *
 * 年份能帮上忙但不能全靠：缺 year 的点不少，而"演化"本身就该是从早指向晚，
 * 所以先按年份把明显反的挑出来，再用 DFS 兜底。
 */
export function breakCycles(nodes, edges) {
  const year = new Map(nodes.map((n) => [n.id, typeof n.year === 'number' ? n.year : null]))
  const kept = []
  const dropped = []
  for (const e of edges) {
    const a = year.get(e.source)
    const b = year.get(e.target)
    if (a !== null && b !== null && a > b) dropped.push({ id: e.id, why: '年份是反的' })
    else kept.push(e)
  }
  const out = new Map()
  for (const e of kept) {
    if (!out.has(e.source)) out.set(e.source, [])
    out.get(e.source).push(e)
  }
  const color = new Map()             // 0 未访问 / 1 在路径上 / 2 已完成
  const good = []
  const walk = (id) => {
    color.set(id, 1)
    for (const e of out.get(id) || []) {
      const c = color.get(e.target) || 0
      if (c === 1) { dropped.push({ id: e.id, why: '成环' }); continue }
      good.push(e)
      if (c === 0) walk(e.target)
    }
    color.set(id, 2)
  }
  for (const n of nodes) if (!color.has(n.id)) walk(n.id)
  return { edges: good, dropped }
}

/** 每个点的层号：到任意一个根的**最长**距离。用最长而不是最短，边才一定向上跨层。 */
export function ranksOf(nodes, edges) {
  const out = new Map()
  const indeg = new Map(nodes.map((n) => [n.id, 0]))
  for (const e of edges) {
    if (!out.has(e.source)) out.set(e.source, [])
    out.get(e.source).push(e.target)
    indeg.set(e.target, (indeg.get(e.target) || 0) + 1)
  }
  const rank = new Map(nodes.map((n) => [n.id, 0]))
  const queue = nodes.filter((n) => !indeg.get(n.id)).map((n) => n.id)
  const left = new Map(indeg)
  while (queue.length) {
    const id = queue.shift()
    for (const t of out.get(id) || []) {
      rank.set(t, Math.max(rank.get(t) || 0, (rank.get(id) || 0) + 1))
      left.set(t, left.get(t) - 1)
      if (left.get(t) === 0) queue.push(t)
    }
  }
  return rank
}

/** 一条枝上挂着多少东西：从这条边的终点出发能走到的点（含它自己），去重。 */
export function branchWeight(edges) {
  const out = new Map()
  for (const e of edges) {
    if (!out.has(e.source)) out.set(e.source, [])
    out.get(e.source).push(e.target)
  }
  const memo = new Map()
  const reach = (id, seen = new Set()) => {
    if (memo.has(id)) return memo.get(id)
    if (seen.has(id)) return new Set()
    seen.add(id)
    const acc = new Set([id])
    for (const t of out.get(id) || []) for (const x of reach(t, seen)) acc.add(x)
    memo.set(id, acc)
    return acc
  }
  return new Map(edges.map((e) => [e.id, reach(e.target).size]))
}

/**
 * 横坐标：同层的点按"父节点的平均位置"排，来回扫几遍收敛。
 *
 * 不用 dagre 那套完整的交叉最小化——这里几十个点，简单的重心法已经足够，
 * 而且结果稳定（同一份数据每次画出来一样），省得图每次打开都换个样子。
 */
export function placeX(nodes, edges, rank) {
  const byRank = new Map()
  for (const n of nodes) {
    const r = rank.get(n.id) || 0
    if (!byRank.has(r)) byRank.set(r, [])
    byRank.get(r).push(n.id)
  }
  for (const list of byRank.values()) list.sort((a, b) => a.localeCompare(b, 'zh'))

  const parents = new Map()
  const children = new Map()
  for (const e of edges) {
    if (!parents.has(e.target)) parents.set(e.target, [])
    parents.get(e.target).push(e.source)
    if (!children.has(e.source)) children.set(e.source, [])
    children.get(e.source).push(e.target)
  }
  const x = new Map()
  const ranks = [...byRank.keys()].sort((a, b) => a - b)
  for (const r of ranks) byRank.get(r).forEach((id, i) => x.set(id, i * (NODE_W + COL_GAP)))

  const mean = (ids) => (ids?.length
    ? ids.reduce((s, id) => s + (x.get(id) ?? 0), 0) / ids.length : null)
  const sweep = (order, pick) => {
    for (const r of order) {
      const list = [...byRank.get(r)]
      const want = new Map(list.map((id) => [id, pick(id) ?? x.get(id)]))
      list.sort((a, b) => want.get(a) - want.get(b) || a.localeCompare(b, 'zh'))
      let cursor = -Infinity
      for (const id of list) {
        const at = Math.max(want.get(id), cursor)
        x.set(id, at)
        cursor = at + NODE_W + COL_GAP
      }
      byRank.set(r, list)
    }
  }
  for (let i = 0; i < 4; i++) {
    sweep(ranks, (id) => mean(parents.get(id)))
    sweep([...ranks].reverse(), (id) => mean(children.get(id)))
  }
  return { x, byRank, ranks }
}

/** 弱连通块：互不相连的几棵树各排各的，否则中间会空出一大片。 */
export function components(nodes, edges) {
  const near = new Map(nodes.map((n) => [n.id, []]))
  for (const e of edges) {
    near.get(e.source)?.push(e.target)
    near.get(e.target)?.push(e.source)
  }
  const seen = new Set()
  const out = []
  for (const n of nodes) {
    if (seen.has(n.id)) continue
    const bag = []
    const stack = [n.id]
    seen.add(n.id)
    while (stack.length) {
      const id = stack.pop()
      bag.push(id)
      for (const t of near.get(id) || []) if (!seen.has(t)) { seen.add(t); stack.push(t) }
    }
    out.push(new Set(bag))
  }
  return out
}

/**
 * 整棵树。根在**下**、叶在**上**——和"从数学长到 Transformer"那个方向一致。
 *
 * 一块一块地排：Clang 那一串和注意力那一串之间没有任何边，混在一个坐标系里算重心，
 * 会在中间空出一屏宽的空白（实测 46% 缩放下整整半屏什么都没有）。
 */
export function buildLineage(index) {
  const base = lineageGraph(index)
  const { edges, dropped } = breakCycles(base.nodes, base.edges)
  const rank = ranksOf(base.nodes, edges)
  const x = new Map()
  const ranks = new Set()
  let offset = 0
  for (const bag of components(base.nodes, edges)) {
    const part = base.nodes.filter((n) => bag.has(n.id))
    const partEdges = edges.filter((e) => bag.has(e.source))
    const laidOut = placeX(part, partEdges, rank)
    const xs = part.map((n) => laidOut.x.get(n.id) ?? 0)
    const lo = Math.min(...xs)
    const hi = Math.max(...xs)
    for (const n of part) x.set(n.id, (laidOut.x.get(n.id) ?? 0) - lo + offset)
    for (const r of laidOut.ranks) ranks.add(r)
    offset += hi - lo + NODE_W + COMPONENT_GAP
  }
  const weight = branchWeight(edges)
  const top = Math.max(...ranks, 0)
  const placed = new Map()
  let minX = Infinity
  for (const n of base.nodes) {
    const r = rank.get(n.id) || 0
    placed.set(n.id, { id: n.id, x: x.get(n.id) ?? 0, y: (top - r) * LEVEL_H, rank: r,
                       w: NODE_W, h: NODE_H })
    minX = Math.min(minX, x.get(n.id) ?? 0)
  }
  for (const box of placed.values()) box.x -= minX === Infinity ? 0 : minX

  const maxWeight = Math.max(1, ...weight.values())
  // 粗细按可达数缩放，但**从 1 开始算**：挂着一个点的叶子边必须是最细的那根，
  // 直接拿 w/maxW 开方的话，叶子边也会有一半粗，粗细就分不出主次了。
  const span = Math.max(1, maxWeight - 1)
  const laid = edges.map((e) => {
    const count = weight.get(e.id) || 1
    return { ...e, count,
             width: MIN_W + (MAX_W - MIN_W) * Math.sqrt((count - 1) / span) }
  })
  const width = Math.max(...[...placed.values()].map((b) => b.x + b.w), NODE_W) + 80
  const height = (top + 1) * LEVEL_H + 80
  const roots = base.nodes.filter((n) => (rank.get(n.id) || 0) === 0).map((n) => n.id)
  return { placed, edges: laid, width, height, levels: top + 1, roots, dropped,
           total: base.nodes.length }
}
