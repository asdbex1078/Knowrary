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

  const box = (node) => {
    const size = sizeFor(node)
    return { id: node.id, year: node.year, w: size.w, h: size.h, x: scale.at(node.year) }
  }
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
 * 时间滑块拖到 t 时，这个节点还该不该出现。
 *
 * 默认是"出生年 ≤ t"的叙事视角；勾上有效期就换成区间视角（F4.5，法律 / 标准场景）：
 * start_year ≤ t < end_year，2010 年废止的东西在 2015 年就不该还挂在图上。
 */
export function visibleAt(node, upto, validity) {
  if (upto === null) return true
  if (!validity) return node.year <= upto
  const start = typeof node.start_year === 'number' ? node.start_year : node.year
  const end = typeof node.end_year === 'number' ? node.end_year : null
  return start <= upto && (end === null || upto < end)
}

/**
 * 组装一张历史图。
 * opts: { timelines, families, compact, upto, validity }
 */
export function buildTimeline(index, layout, opts = {}) {
  const { timelines = [], families = new Set(['演化']), compact = false, upto = null,
          validity = false, trunk = false } = opts
  const withYear = index.nodes.filter((n) => !n.virtual && typeof n.year === 'number')
  const laneNames = new Map()
  const kept = []
  for (const node of withYear) {
    const lane = laneOf(node, layout, timelines)
    if (lane === null) continue                     // 不在所选时间线里
    if (!visibleAt(node, upto, validity)) continue
    laneNames.set(lane, true)
    kept.push({ node, lane })
  }
  const scale = yearScale(kept.map((k) => k.node.year), compact)

  // 主干道：先算出可见的演化边，再挑最长链。挑不出链（没有演化边、或只剩孤点）
  // 就退回泳道，而不是给一张空图——那样用户只会以为功能坏了。
  if (trunk) {
    const visible = new Set(kept.map((k) => k.node.id))
    const ev = index.edges.filter((e) => visible.has(e.source) && visible.has(e.target))
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
    const items = kept.filter((k) => k.lane === lane).map(({ node }) => {
      const size = sizeFor(node)
      return { id: node.id, year: node.year, w: size.w, h: size.h, x: scale.at(node.year) }
    })
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
  const edges = index.edges.filter((e) => families.has(e.family)
    && placed.has(e.source) && placed.has(e.target))
  // 两端都有 year 才画，所以缺年份的演化边是"数据欠账"，单独报出来而不是悄悄丢掉
  const missingYear = index.edges.filter((e) => e.family === '演化' && e.year == null
    && placed.has(e.source) && placed.has(e.target)).map((e) => e.id)
  return {
    placed, lanes, ticks: scale.ticks, width: scale.width + NODE_W + 80,
    height, edges, ...extra,
    diagnostics: {
      noYear: index.nodes.filter((n) => !n.virtual && typeof n.year !== 'number').length,
      missingYear, skipped: withYear.length - kept.length,
    },
  }
}

export const NODE_FALLBACK = { w: NODE_W, h: NODE_H }
