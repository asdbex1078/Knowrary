// 模型谱系的排版（2026-09-30，模型谱系第 3 步）：一个家族一条泳道，泳道里按发布顺序**等距**排开。
//
// 等距是他选的（不按真实时间比例）：最紧凑、名字不打架；代价是各家之间时间对不齐——
// 所以每条泳道里跨进新的一年时标一个年份刻度，时间感不至于全丢。
// 点一样大，参数写在名字旁；名字一上一下交错，避开相邻那个。
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

const norm = (s) => String(s || '').trim().toLowerCase()

/** 型号用到的技术点的键：对上了知识点的用知识点 id，没对上的用文字（小写）。 */
export function techKeys(model) {
  return (model.tech || []).map((t) => (t.node ? `node:${t.node}` : `text:${norm(t.text)}`))
}

/** 技术点下拉：出现过的技术点和各自被几个型号用到，多的在前。 */
export function techOptions(doc) {
  const seen = new Map()
  for (const fam of doc?.families || []) {
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

function visibleFamilies(doc, open) {
  const fams = (doc?.families || []).filter((f) => open === 'all' || (open === 'open' ? f.open === true : f.open === false))
  const first = (f) => Math.min(...f.models.map((m) => (m.t ?? Infinity)))
  return [...fams].sort((a, b) => first(a) - first(b) || a.name.localeCompare(b.name))
}

function ordered(models) {
  // 有日期的按时间，没日期的按文件里的顺序排在最后（不能丢：人写了就该看得见）
  const dated = models.filter((m) => m.t != null).sort((a, b) => a.t - b.t)
  return [...dated, ...models.filter((m) => m.t == null)]
}

/**
 * 算整张图。`opts`：`open`（all / open / closed）、`hidden`（收起的家族 id）、`tech`（高亮的技术点键）。
 * 返回 `{ lanes, items, links, width, height }`；`items` 里每个点带 `x, y, labelUp, label, dim`。
 */
export function computeLanes(doc, { open = 'all', hidden = new Set(), tech = null } = {}) {
  const lanes = []
  const items = []
  let y = TOP
  let cols = 0
  for (const fam of visibleFamilies(doc, open)) {
    const folded = hidden.has(fam.id)
    const h = folded ? LANE_FOLDED : LANE_H
    const lane = { family: fam, y, h, folded, ticks: [] }
    if (!folded) layLane(lane, items, tech)
    cols = Math.max(cols, folded ? 0 : fam.models.length)
    lanes.push(lane)
    y += h
  }
  const at = new Map(items.map((it) => [it.id, it]))
  const links = (doc?.links || []).filter((l) => at.has(l.source) && at.has(l.target))
    .map((l) => ({ ...l, d: linkPath(at.get(l.source), at.get(l.target), l.kind) }))
  return { lanes, items, links, width: LEFT + cols * COL + 24, height: y + TOP }
}

function layLane(lane, items, tech) {
  let lastYear = null
  ordered(lane.family.models).forEach((m, i) => {
    const x = LEFT + i * COL + COL / 2
    const size = humanParams(m.params_n)
    items.push({ id: m.id, model: m, family: lane.family, x, y: lane.y + lane.h / 2, labelUp: i % 2 === 0,
                 label: size ? `${m.name} · ${size}` : m.name, dim: !!tech && !techKeys(m).includes(tech) })
    if (m.year != null && m.year !== lastYear) lane.ticks.push({ x, year: m.year })
    if (m.year != null) lastYear = m.year
  })
}

/** 同一泳道相邻的直连；隔了几个的从上面拱过去；跨泳道（基于）画 S 形曲线。 */
function linkPath(a, b, kind) {
  if (a.y === b.y) {
    if (Math.abs(b.x - a.x) <= COL + 1) return `M${a.x},${a.y} L${b.x},${b.y}`
    const lift = Math.min(38, 14 + Math.abs(b.x - a.x) / 20)
    return `M${a.x},${a.y} C${a.x},${a.y - lift} ${b.x},${b.y - lift} ${b.x},${b.y}`
  }
  const mid = (a.y + b.y) / 2
  return `M${a.x},${a.y} C${a.x},${mid} ${b.x},${mid} ${b.x},${b.y}`
}
