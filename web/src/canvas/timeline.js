// 历史视图布局（设计文档 3.8）：X 轴锁死在年份上，所以这里可以放心自动布局——
// 位置不是算法乱猜的，是数据本身决定的。历史视图的坐标不持久化，每次进入重算。
import { NODE_H, NODE_W, sizeFor } from './shapes'

export const YEAR_W = 130          // 一年最多占多少像素（跨度小时用这个）
export const YEAR_W_MIN = 30       // 一年最少占多少像素
export const TARGET_W = 2800       // 整条时间轴的目标宽度：跨度越大，年宽自动越窄
export const COMPACT_GAP = 20      // 紧凑模式下，空白超过这么多年就压缩
export const COMPACT_W = 180       // 压缩后的固定宽度
export const ROW_H = 86            // 泳道内一行的高度
export const LANE_PAD = 30         // 泳道上下留白
export const LANE_TITLE_H = 26     // 泳道标题占的高度，节点从它下面开始排
export const LANE_GAP = 24
export const BLOCK_GAP = 60        // 多条时间线叠加时，块与块之间的空行
export const AXIS_H = 40
export const TRUNK_GAP = 74        // 主干与第一排旁支之间的距离

/** 分组的所有后代（含自己）。 */
export function descendants(groups, gid) {
  const out = new Set([gid])
  let grew = true
  while (grew) {
    grew = false
    for (const [id, g] of Object.entries(groups)) {
      if (g.parent && out.has(g.parent) && !out.has(id)) {
        out.add(id)
        grew = true
      }
    }
  }
  return out
}

/** 时间线选择器的选项：layout 分组树的任意层级，按层级缩进。 */
export function timelineOptions(layout) {
  const groups = layout?.groups || {}
  const depth = (id, seen = new Set()) => {
    const g = groups[id]
    if (!g?.parent || seen.has(id)) return 0
    seen.add(id)
    return 1 + depth(g.parent, seen)
  }
  return Object.entries(groups)
    .map(([id, g]) => ({ id, name: g.name, depth: depth(id) }))
    .sort((a, b) => (a.depth - b.depth) || a.name.localeCompare(b.name, 'zh'))
}

/**
 * 年份 → x 的比例尺。
 *
 * 年宽按跨度自适应：真实数据跨了 1936–2018，固定 130px/年会拉出一万像素，
 * 适应窗口之后整张图缩成一条线什么都看不清。紧凑模式再把长空白段压成固定宽度。
 */
export function yearScale(years, compact) {
  const sorted = [...new Set(years)].sort((a, b) => a - b)
  if (!sorted.length) return { at: () => 0, ticks: [], width: 0, unit: YEAR_W }
  const span = sorted[sorted.length - 1] - sorted[0]
  const unit = span > 0 ? Math.min(YEAR_W, Math.max(YEAR_W_MIN, TARGET_W / span)) : YEAR_W
  const pos = new Map([[sorted[0], 0]])
  let x = 0
  for (let i = 1; i < sorted.length; i++) {
    const gap = sorted[i] - sorted[i - 1]
    x += compact && gap > COMPACT_GAP ? COMPACT_W : gap * unit
    pos.set(sorted[i], x)
  }
  const at = (year) => {
    if (pos.has(year)) return pos.get(year)
    // 落在两个已知年份之间：按线性段插值，紧凑段则取压缩后的比例
    const prev = [...pos.keys()].filter((y) => y < year).pop()
    const next = [...pos.keys()].find((y) => y > year)
    if (prev === undefined) return 0
    if (next === undefined) return pos.get(prev) + (year - prev) * unit
    const span = pos.get(next) - pos.get(prev)
    return pos.get(prev) + span * ((year - prev) / (next - prev))
  }
  return { at, ticks: sorted.map((y) => ({ year: y, x: pos.get(y) })), width: x, unit }
}

// 抽象层：**从下往上**排（底层在下、应用在上），所以渲染时要倒过来铺。
// 这是和主题（field / 分组）正交的另一个维度——一个节点只能落一个分组，
// 两个维度抢同一个字段的话，结构图和历史图必有一个要将就。
export const LAYERS = ['理论', '硬件', '体系结构', '汇编接口', '系统软件', '高级语言', 'AI应用']
export const UNLAYERED = '未分层'
export const BY_LAYER = '__layer__'        // 时间线选择器里的特殊一档

/** 泳道归属：按抽象层 / 按所选时间线的直接子分组 / 都没选就按 field。 */
function laneOf(node, layout, selected) {
  const place = layout.nodes?.[node.id]
  const groups = layout.groups || {}
  if (selected.includes(BY_LAYER)) return node.layer || UNLAYERED
  if (!selected.length) return node.field || '(未指定)'
  for (const root of selected) {
    const family = descendants(groups, root)
    if (!place?.group || !family.has(place.group)) continue
    let cur = place.group
    while (cur && groups[cur]?.parent && groups[cur].parent !== root) cur = groups[cur].parent
    const name = groups[cur]?.parent === root ? groups[cur].name : `${groups[root]?.name} · 直属`
    return `${groups[root]?.name || root}／${name}`
  }
  return null
}

/**
 * 一个节点在时间轴上的盒子。
 *
 * start / end 顺手带上：游标每走一站都要判"这一年它还有效吗"，
 * 存在盒子里就不用回头再查一遍 index。
 */
function boxOf(node, scale) {
  const size = sizeFor(node)
  return {
    id: node.id, year: node.year, w: size.w, h: size.h, x: scale.at(node.year),
    start: typeof node.start_year === 'number' ? node.start_year : node.year,
    end: typeof node.end_year === 'number' ? node.end_year : null,
  }
}

/** 泳道内的扫描线放置：按年份从左到右，撞上了就往下挪一行。 */
function packLane(items) {
  const rows = []
  for (const item of items.sort((a, b) => a.year - b.year || a.id.localeCompare(b.id))) {
    let row = rows.findIndex((end) => end <= item.x)
    if (row < 0) {
      row = rows.length
      rows.push(0)
    }
    rows[row] = item.x + item.w + 24
    item.row = row
  }
  return rows.length || 1
}

/**
 * 找出最长的一条演化链，当主干用。
 *
 * 只沿时间正向走（source.year ≤ target.year）：一来演化本来就该是这个方向，
 * 二来顺手保证了无环——诊断报告里那 13 处关系环说明图上真的会有反向边，
 * 拿它跑最长路会死循环。
 */
export function longestChain(nodes, edges) {
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const out = new Map()
  for (const e of edges) {
    if (e.family !== '演化') continue
    const a = byId.get(e.source)
    const b = byId.get(e.target)
    if (!a || !b || a.year > b.year) continue
    out.set(e.source, [...(out.get(e.source) || []), e.target])
  }
  const order = [...byId.keys()].sort((x, y) => byId.get(x).year - byId.get(y).year)
  const best = new Map(order.map((id) => [id, 1]))
  const prev = new Map()
  for (const id of order) {
    for (const t of out.get(id) || []) {
      if (best.get(id) + 1 > best.get(t)) {
        best.set(t, best.get(id) + 1)
        prev.set(t, id)
      }
    }
  }
  let end = null
  for (const [id, n] of best) if (end === null || n > best.get(end)) end = id
  const chain = []
  for (let cur = end; cur !== undefined && cur !== null; cur = prev.get(cur)) chain.unshift(cur)
  return chain.length >= 2 ? chain : []          // 一个点不叫链
}

/**
 * 主干道布局：把最长的那条演化链拉成一条水平主轴，其余节点作为旁支挂在上下。
 *
 * 为什么需要它：泳道布局回答"谁和谁是一类"，而聚焦到一条线时你要问的是"谁接谁"。
 * 同一个泳道里几条链交织时，散点加连线根本读不出主线走向。
 * X 仍然锁死在年份上，所以这里只动 Y——位置依旧不是算法乱猜的。
 */
function trunkLayout(kept, scale, edges) {
  const nodes = kept.map((k) => k.node)
  const chain = longestChain(nodes, edges)
  if (!chain.length) return null
  const onTrunk = new Set(chain)

  const box = (node) => boxOf(node, scale)
  const trunk = chain.map((id) => box(nodes.find((n) => n.id === id)))
  const rest = nodes.filter((n) => !onTrunk.has(n.id)).map(box)

  // 旁支上下交替，各自跑一遍扫描线：只往一边堆会把图拉得很高
  const up = [], down = []
  rest.sort((a, b) => a.year - b.year || a.id.localeCompare(b.id))
  rest.forEach((item, i) => (i % 2 ? up : down).push(item))
  const upRows = packLane(up)
  const downRows = packLane(down)

  const trunkY = AXIS_H + LANE_GAP + LANE_TITLE_H + upRows * ROW_H + (up.length ? TRUNK_GAP : LANE_PAD)
  const placed = new Map()
  for (const item of trunk) placed.set(item.id, { ...item, y: trunkY, trunk: true })
  for (const item of up) placed.set(item.id, { ...item, y: trunkY - TRUNK_GAP - (item.row + 1) * ROW_H + ROW_H })
  for (const item of down) placed.set(item.id, { ...item, y: trunkY + TRUNK_GAP + item.row * ROW_H })

  const height = trunkY + TRUNK_GAP + downRows * ROW_H + LANE_PAD + (down.length ? 0 : -TRUNK_GAP)
  const name = `主干：${chain[0]} → … → ${chain[chain.length - 1]}（${chain.length} 站）`
  return { placed, chain,
           lanes: [{ name, y: AXIS_H + LANE_GAP, h: height - AXIS_H - LANE_GAP,
                     width: scale.width + NODE_W + 80 }],
           height: height + LANE_GAP }
}

/**
 * 时间游标停在 upto 时，图上哪些点算"已经发生"。
 *
 * 默认是"出生年 ≤ upto"的叙事视角；勾上有效期就换成区间视角（F4.5，法律 / 标准场景）：
 * start_year ≤ upto < end_year，2010 年废止的东西在 2015 年就不算还有效。
 *
 * **它只决定画法，不决定进不进图。** 布局是拿全量节点一次算定的，游标扫过只是把
 * "未来"的点淡下去——这样拖滑块 / 回放时比例尺和泳道行数都不变，已经出现的点
 * 不会跟着滑、不会重排、更不会整屏重刷一遍入场动画。
 *
 * 只认 plan 里存好的 year / start / end，不回头查 index：回放每站都要算一次。
 */
export function activeAt(plan, upto, validity) {
  const on = new Set()
  for (const [id, box] of plan.placed) {
    if (upto === null || (!validity && box.year <= upto)
        || (validity && box.start <= upto && (box.end === null || upto < box.end))) on.add(id)
  }
  return on
}

/**
 * 组装一张历史图。
 * opts: { timelines, families, compact, upto, validity }
 */
export function buildTimeline(index, layout, opts = {}) {
  const { timelines = [], families = new Set(['演化']), compact = false, trunk = false } = opts
  // upto / validity **不参与布局**：它们只决定哪些点画成"已发生"（见 activeAt）。
  // 以前这两个值会把节点整个滤掉，于是 yearScale 只拿可见年份算，
  // 滑块一动整条 X 轴就重新拉伸、泳道行数也跟着变——回放时全图一直在滑在跳。
  const withYear = index.nodes.filter((n) => !n.virtual && typeof n.year === 'number')
  const laneNames = new Map()
  const kept = []
  for (const node of withYear) {
    const lane = laneOf(node, layout, timelines)
    if (lane === null) continue                     // 不在所选时间线里
    laneNames.set(lane, true)
    kept.push({ node, lane })
  }
  const scale = yearScale(kept.map((k) => k.node.year), compact)

  // 主干道：先算出图上的演化边，再挑最长链。挑不出链（没有演化边、或只剩孤点）
  // 就退回泳道，而不是给一张空图——那样用户只会以为功能坏了。
  if (trunk) {
    const inGraph = new Set(kept.map((k) => k.node.id))
    const ev = index.edges.filter((e) => inGraph.has(e.source) && inGraph.has(e.target))
    const laid = trunkLayout(kept, scale, ev)
    if (laid) return finish(laid.placed, laid.lanes, laid.height, scale, index, kept, withYear,
                            families, { trunk: laid.chain })
  }

  const placed = new Map()
  const lanes = []
  let y = AXIS_H + LANE_GAP
  let lastBlock = null
  const byLayer = timelines.includes(BY_LAYER)
  // 按层时用固定次序（底层在下 → 数组倒过来铺），别用字典序——
  // 「AI应用」排在「体系结构」前面这种事，一眼就看得出是错的。
  const order = byLayer
    ? [...LAYERS].reverse().concat(UNLAYERED).filter((l) => laneNames.has(l))
    : [...laneNames.keys()].sort((a, b) => a.localeCompare(b, 'zh'))
  for (const lane of order) {
    const block = lane.split('／')[0]
    if (lastBlock !== null && block !== lastBlock) y += BLOCK_GAP     // 多条时间线之间留空行
    lastBlock = block
    const items = kept.filter((k) => k.lane === lane).map(({ node }) => boxOf(node, scale))
    const rows = packLane(items)
    const h = rows * ROW_H + LANE_PAD + LANE_TITLE_H
    const top = y + LANE_TITLE_H + LANE_PAD / 2      // 标题下面才开始排节点，别压着泳道名
    for (const item of items) placed.set(item.id, { ...item, y: top + item.row * ROW_H, lane })
    lanes.push({ name: lane, y, h, width: scale.width + NODE_W + 80 })
    y += h + LANE_GAP
  }

  return finish(placed, lanes, y, scale, index, kept, withYear, families, {})
}

/** 两种布局共用的收尾：挑边、算诊断、拼出 plan。 */
function finish(placed, lanes, height, scale, index, kept, withYear, families, extra) {
  const inGraph = index.edges.filter((e) => placed.has(e.source) && placed.has(e.target))
  const edges = inGraph.filter((e) => families.has(e.family))
  // 两端都有 year 才画，所以缺年份的演化边是"数据欠账"，单独报出来而不是悄悄丢掉
  const missingYear = inGraph.filter((e) => e.family === '演化' && e.year == null).map((e) => e.id)
  // 最长演化链：主干道布局本来就要算，泳道布局这里补算一次给「沿演化链导览」用。
  // **不受关系族开关影响**——导览走的是"谁接谁"，不该因为用户把演化边关了就没路可走。
  const chain = extra.trunk
    || longestChain([...placed].map(([id, box]) => ({ id, year: box.year })), inGraph)
  return {
    placed, lanes, ticks: scale.ticks, width: scale.width + NODE_W + 80,
    height, edges, ...extra, chain,
    at: scale.at,                                   // 年份 → x，游标定位要用
    eventYears: scale.ticks.map((t) => t.year),     // "有事发生"的年份，回放按这个跳站
    // 链上相邻两站之间的那条演化边，导览要点亮它
    chainEdges: chain.slice(1).map((id, i) => inGraph.find(
      (e) => e.family === '演化' && e.source === chain[i] && e.target === id)?.id).filter(Boolean),
    diagnostics: {
      noYear: index.nodes.filter((n) => !n.virtual && typeof n.year !== 'number').length,
      missingYear, skipped: withYear.length - kept.length,
      // 导览走得动走不动，取决于这两个数。链短的时候要能说清是"功能坏了"还是"图里就这么点线"
      evoAll: index.edges.filter((e) => e.family === '演化').length,
      evoUsable: inGraph.filter((e) => e.family === '演化').length,
    },
  }
}

export const NODE_FALLBACK = { w: NODE_W, h: NODE_H }
