// 术语按需查（名词解释第 4 步，2026-10-28）：选中正文里一个词，查它在术语表里的义项。
//
// 用户选的是"不自动描、只按需查"：不扫正文描下划线，选中一段字正好匹配某词条就弹解释。
// 匹配按整词相等（词条或别名），ASCII 不分大小写、中文照原样；多义不丢，按语境排好、best 在前。

/** 选中的一段字 → 命中的义项们（词条或别名整词相等）。空选 / 没命中 → []。 */
export function findSenses(entries, surface) {
  const s = String(surface || '').trim()
  if (!s || !Array.isArray(entries)) return []
  const lc = s.toLowerCase()
  const same = (x) => x === s || String(x).toLowerCase() === lc   // ASCII 不分大小写，中文照原样（lc 不影响）
  return entries.filter((e) => same(e.term) || (Array.isArray(e.aka) && e.aka.some(same)))
}

/** 多义按语境排序（不丢）：当前专题的在最前，全局次之，其余在后。`ctx.topic` = 当前专题名。 */
export function resolveSenses(senses, ctx = {}) {
  const topic = ctx.topic || ''
  const rank = (scope) => (scope === topic && topic ? 0 : scope === '全局' ? 1 : 2)
  return [...senses].sort((a, b) => rank(a.scope) - rank(b.scope))
}
