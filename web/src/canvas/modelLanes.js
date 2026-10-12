// 谱系图的排版（2026-09-30 模型谱系第 3 步；2026-10-12 起一张图 = 谱系目录的一个一级子目录：
// 大模型 / CPU / 操作系统各一张，这里只排选中那张图里的家族）：一个家族一条泳道，泳道里按发布顺序**等距**排开。
//
// 等距是他选的（不按真实时间比例）：最紧凑、名字不打架；代价是各家之间时间对不齐——
// 所以每条泳道里跨进新的一年时标一个年份刻度，时间感不至于全丢。
// 点一样大，名字旁写这张图 `谱系.md` 里 badge 指定的那一格（大模型是参数、CPU 是位宽…）；名字一上一下交错。
// 纯函数、不碰 DOM：给 `/api/models` 的结果和筛选条件，算出每个点、每根线、每个刻度的坐标。

export const COL = 124          // 相邻两个型号的间距
export const LANE_H = 92        // 一条泳道多高（名字上下各留一行）
export const LANE_FOLDED = 30   // 收起的泳道
export const LEFT = 132         // 左边写家族名的那一栏
export const TOP = 12

/** 671e9 → 671B、1e12 → 1T、2.8e12 → 2.8T。 */
export function humanParams(n) {
  if (typeof n !== 'number' || !(n > 0)) return ''
  const [div, unit] = n >= 1e12 ? [1e12, 'T'] : n >= 1e9 ? [1e9, 'B'] : n >= 1e6 ? [1e6, 'M'] : [1e3, 'K']
  return `${+(n / div).toFixed(n / div >= 100 ? 0 : 1)}${unit}`
}

/** 带单位的数：声明了单位的照写（65nm），没单位的按量级缩写（1.9B 个晶体管、671B 参数）。 */
export function humanNumber(n, unit = '') {
  if (typeof n !== 'number' || !(n > 0)) return ''
  if (unit) return `${+n.toFixed(n >= 100 ? 0 : 2)}${unit}`
  return n >= 1e3 ? humanParams(n) : String(+n.toFixed(2))
}

/** 一张图的声明：id → { id, name, badge, fields }。 */
export function lineageOf(doc, id) {
  return (doc?.lineages || []).find((l) => l.id === id) || null
}

/** 名字旁那一格：badge 是数值字段就按量级 / 单位写，是文字就照抄原文。 */
export function badgeOf(model, spec) {
  const key = spec?.badge
  if (!key) return ''
  const field = spec.fields.find((f) => f.key === key)
  if (field?.numeric && typeof model.numbers?.[key] === 'number') return humanNumber(model.numbers[key], field.unit)
  return model.values?.[key] || ''
}

const norm = (s) => String(s || '').trim().toLowerCase()

/** 型号用到的技术点的键：对上了知识点的用知识点 id，没对上的用文字（小写）。 */
export function techKeys(model) {
  return (model.tech || []).map((t) => (t.node ? `node:${t.node}` : `text:${norm(t.text)}`))
}

/** 技术点下拉：出现过的技术点和各自被几个型号用到，多的在前。 */
export function techOptions(doc, lineage = null) {
  const seen = new Map()
  for (const fam of (doc?.families || []).filter((f) => lineage == null || f.lineage === lineage)) {
    for (const m of fam.models) {
      for (const [i, key] of techKeys(m).entries()) {
        const row = seen.get(key) || { key, label: m.tech[i].text, count: 0 }
        row.count += 1
        seen.set(key, row)
      }
    }
  }
  return [...seen.values()].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
}

function visibleFamilies(doc, open, lineage) {
  const fams = (doc?.families || []).filter((f) => (lineage == null || f.lineage === lineage)
    && (open === 'all' || (open === 'open' ? f.open === true : f.open === false)))
  const first = (f) => Math.min(...f.models.map((m) => (m.t ?? Infinity)))
  return [...fams].sort((a, b) => first(a) - first(b) || a.name.localeCompare(b.name))
}

function ordered(models) {
  // 有日期的按时间，没日期的按文件里的顺序排在最后（不能丢：人写了就该看得见）
  const dated = models.filter((m) => m.t != null).sort((a, b) => a.t - b.t)
  return [...dated, ...models.filter((m) => m.t == null)]
}

/**
 * 算整张图。`opts`：
 * - `lineage`（看哪张图；null = 不分图）或 `lineages`（几张一起看）；
 * - `open`（all / open / closed）、`hidden`（收起的家族 id）、`tech`（高亮的技术点键）。
 *
 * **单看一张图按发布顺序等距**（他选的，最紧凑）；**两张以上叠加时换成共用的真实年份轴**
 * （2026-10-12 第 2 步拍板）——等距下 80386 和 Linux 0.11 各排各的，跨图那根线的斜度什么都不代表；
 * 共用年份轴上 1985 和 1991 竖着对得上，线才有意义。叠加时每张图前面插一行图名（`heads`）。
 *
 * 返回 `{ lanes, heads, items, links, axis, timed, width, height }`；`items` 里每个点带 `x, y, labelUp, label, dim`。
 */
export function computeLanes(doc, { lineage = null, lineages = null, open = 'all', hidden = new Set(), tech = null } = {}) {
  const ids = lineages || [lineage]
  const timed = ids.length > 1
  const scale = timed ? yearScale(doc, ids) : null
  const lanes = []
  const heads = []
  const items = []
  let y = TOP + (timed ? AXIS_H : 0)
  let right = LEFT
  for (const id of ids) {
    const spec = lineageOf(doc, id)
    if (timed) {
      heads.push({ lineage: id, name: spec?.name || id || '未分谱系', y, h: HEAD_H })
      y += HEAD_H
    }
    for (const fam of visibleFamilies(doc, open, id)) {
      const folded = hidden.has(fam.id)
      const h = folded ? LANE_FOLDED : LANE_H
      const lane = { family: fam, y, h, folded, ticks: [], x2: LEFT }
      if (!folded) (timed ? layTimed : layLane)(lane, items, tech, spec, scale)
      right = Math.max(right, lane.x2)
      lanes.push(lane)
      y += h
    }
  }
  markLabels(items)
  const at = new Map(items.map((it) => [it.id, it]))
  const links = (doc?.links || []).filter((l) => at.has(l.source) && at.has(l.target))
    .map((l) => {
      const a = at.get(l.source)
      const b = at.get(l.target)
      return { ...l, d: linkPath(a, b), mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2 }
    })
  return { lanes, heads, items, links, timed, axis: scale ? scale.ticks(right) : [],
           width: right + 24, height: y + TOP }
}

export const YEAR_PX = 56        // 叠加时一年多宽
export const MIN_GAP = 34        // 叠加时同一泳道两个点至少隔这么多（同一年好几个型号别叠成一个点）
export const AXIS_H = 26         // 叠加时顶上那条年份轴
export const HEAD_H = 26         // 叠加时每张图的图名行

/** 叠加时共用的年份比例尺：从这几张图里最早的那一年起。 */
function yearScale(doc, ids) {
  const ts = (doc?.families || []).filter((f) => ids.includes(f.lineage))
    .flatMap((f) => f.models.map((m) => m.t)).filter((t) => t != null)
  const t0 = ts.length ? Math.floor(Math.min(...ts)) : 2000
  const at = (t) => LEFT + COL / 2 + (t - t0) * YEAR_PX
  const step = [1, 2, 5, 10].find((n) => n * YEAR_PX >= 48) || 10   // 「1985」约 28px，刻度隔 48px 以上就不打架
  return {
    at,
    ticks: (right) => {
      const out = []
      for (let year = Math.ceil(t0 / step) * step; at(year) <= right; year += step) out.push({ year, x: at(year) })
      return out
    },
  }
}

function item(lane, m, i, x, tech, spec) {
  const badge = badgeOf(m, spec)
  return { id: m.id, model: m, family: lane.family, x, y: lane.y + lane.h / 2, col: i, labelUp: i % 2 === 0,
           label: badge ? `${m.name} · ${badge}` : m.name, dim: !!tech && !techKeys(m).includes(tech) }
}

function layLane(lane, items, tech, spec) {
  let lastYear = null
  ordered(lane.family.models).forEach((m, i) => {
    const x = LEFT + i * COL + COL / 2
    items.push(item(lane, m, i, x, tech, spec))
    if (m.year != null && m.year !== lastYear) lane.ticks.push({ x, year: m.year })
    if (m.year != null) lastYear = m.year
    lane.x2 = x + COL / 2
  })
}

/** 叠加时：按真实时间落位；和前一个挨得太近就往右让一点（让出来的那点误差比叠成一个点强）。没日期的接在最后。 */
function layTimed(lane, items, tech, spec, scale) {
  let prev = -Infinity
  ordered(lane.family.models).forEach((m, i) => {
    const want = m.t != null ? scale.at(m.t) : prev + COL
    const x = Math.max(want, prev + MIN_GAP)
    items.push(item(lane, m, i, x, tech, spec))
    prev = x
    lane.x2 = x + COL / 2
  })
}

/** 名字大概多宽（11px 字：汉字约 11、字母数字约 6.2）。 */
export function labelWidth(text = '') {
  return [...String(text)].reduce((w, c) => w + (c.charCodeAt(0) < 256 ? 6.2 : 11), 0)
}

/**
 * 谁的名字写得出来：名字居中写在点的上 / 下方，同一泳道同一侧（上或下）的两个名字撞了，后一个就不写，
 * 留给悬停卡片。叠加时按真实时间排，2024–2025 的大模型挤在一起，不这么做会糊成一片（截图里 V2 和 R1 叠在一起）。
 */
function markLabels(items) {
  const last = new Map()                           // `${泳道 y}|${上下}` → 上一个写出来的名字 { x, w }
  for (const it of [...items].sort((a, b) => a.y - b.y || a.x - b.x)) {
    const key = `${it.y}|${it.labelUp}`
    const w = labelWidth(it.label)
    const prev = last.get(key)
    it.showLabel = !prev || it.x - prev.x >= (prev.w + w) / 2 + 8
    if (it.showLabel) last.set(key, { x: it.x, w })
  }
}

/** 同一泳道相邻的直连；隔了几个的从上面拱过去；跨泳道（基于、跨图关系）画 S 形曲线。 */
function linkPath(a, b) {
  if (a.y === b.y) {
    if (Math.abs(b.col - a.col) <= 1) return `M${a.x},${a.y} L${b.x},${b.y}`
    const lift = Math.min(38, 14 + Math.abs(b.x - a.x) / 20)
    return `M${a.x},${a.y} C${a.x},${a.y - lift} ${b.x},${b.y - lift} ${b.x},${b.y}`
  }
  const mid = (a.y + b.y) / 2
  return `M${a.x},${a.y} C${a.x},${mid} ${b.x},${mid} ${b.x},${b.y}`
}
